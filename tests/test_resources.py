"""外部资源层（零 token：monkeypatch resources._get/_post + 进程内跑 cli.main）。

注意：绝不要 subprocess bin/wd.py——realpath 会逃出 tmp HOME 指向真仓库；
CLI 一律进程内 `cli.main([...])`。配置快照 fixture 防外溢。
"""
import asyncio
import json
import time
from dataclasses import asdict

import pytest
import yaml


class FakeResp:
    def __init__(self, status_code=200, json_data=None, text="", headers=None,
                 content=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers or {}
        self.content = content if content is not None else text.encode()

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def restore_res_cfg():
    from loadn_webui.config import CONFIG
    snap = asdict(CONFIG.resources)
    yield
    for k, v in snap.items():
        setattr(CONFIG.resources, k, v)


@pytest.fixture()
def http_log(monkeypatch):
    """按 URL 分发的假 HTTP；records 记录 (method,url,kwargs)。"""
    from loadn_webui import resources
    routes: dict = {}
    records: list = []

    async def fake_get(url, **kw):
        records.append(("GET", url, kw))
        for pat, fn in routes.items():
            if pat in url:
                return fn(url, kw)
        return FakeResp(404, {"error": "no route"})

    async def fake_post(url, **kw):
        records.append(("POST", url, kw))
        for pat, fn in routes.items():
            if pat in url:
                return fn(url, kw)
        return FakeResp(404, {"error": "no route"})

    monkeypatch.setattr(resources, "_get", fake_get)
    monkeypatch.setattr(resources, "_post", fake_post)
    return {"routes": routes, "records": records}


# ---------------------------------------------------------------- 设置 API
async def test_settings_resources_roundtrip(client):
    r = await client.get("/api/settings")
    assert r.status_code == 200
    res = r.json()["resources"]
    assert res["ocr_url"].startswith("http://127.0.0.1:8686")
    assert res["bocha_key_set"] is False
    assert res["zhipu_key_set"] is False and res["zhipu_engine"] == "search_pro"

    secrets = {"sandbox_api_key": "***REDACTED***", "sms_token": "tok-abcdef12345",
               "mail_auth_code": "mailcode12345",
               "twocaptcha_key": "cap12345", "bocha_key": "bocha12345",
               "zhipu_key": "zhipu12345"}
    r = await client.put("/api/settings/resources", json={
        "ocr_url": "http://127.0.0.1:8686/", "adb_addr": "192.0.2.78:5555",
        "zhipu_engine": "search_std", **secrets})
    assert r.status_code == 200, r.text
    res = r.json()["resources"]
    for k in secrets:
        assert res[f"{k}_set"] is True
        assert secrets[k] not in json.dumps(res)          # 明文永不回传
        assert res[f"{k}_hint"].endswith(secrets[k][-5:])
    assert res["ocr_url"] == "http://127.0.0.1:8686"      # 尾斜杠剥掉
    assert res["zhipu_engine"] == "search_std"

    # 落盘（tmp HOME）
    from loadn_webui.config import PATHS
    data = yaml.safe_load((PATHS["root"] / "config.yaml").read_text())
    assert data["resources"]["bocha_key"] == "bocha12345"

    # 留空 = 保持不变
    r = await client.put("/api/settings/resources", json={"adb_addr": "192.0.2.99:5555"})
    res = r.json()["resources"]
    assert res["bocha_key_set"] is True and res["adb_addr"] == "192.0.2.99:5555"

    # 非法值
    r = await client.put("/api/settings/resources", json={"ocr_url": "not-a-url"})
    assert r.status_code == 400
    r = await client.put("/api/settings/resources", json={"adb_addr": "noport"})
    assert r.status_code == 400


# ---------------------------------------------------------------- 智谱搜索
async def test_zhipu_search(http_log, monkeypatch):
    from loadn_webui import resources
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "zhipu_key", "zp-key")

    captured = {}

    def capture(url, kw):
        captured["url"] = url
        captured["auth"] = kw.get("headers", {}).get("Authorization")
        captured["json"] = kw.get("json")
        return FakeResp(200, {"search_result": [
            {"title": "结果A", "link": "https://a", "content": "摘要A",
             "media": "知乎", "publish_date": "2026-09-01"},
            {"title": "无链接条目", "link": "", "content": "被丢弃"},
        ]})

    http_log["routes"]["bigmodel.cn"] = capture
    hits = await resources.zhipu_search("测试", n=5, recency="oneWeek",
                                        domain="www.sohu.com")
    assert hits == [{"name": "结果A", "url": "https://a", "snippet": "摘要A",
                     "siteName": "知乎", "date": "2026-09-01"},
                    # search_std 常整体无 link——保留条目，url 为空
                    {"name": "无链接条目", "url": "", "snippet": "被丢弃",
                     "siteName": "", "date": ""}]
    assert captured["url"].endswith("/api/paas/v4/web_search")
    assert captured["auth"] == "Bearer zp-key"
    body = captured["json"]
    assert body["search_engine"] == "search_pro"        # 默认继承 zhipu_engine
    assert body["search_query"] == "测试" and body["count"] == 5
    assert body["search_recency_filter"] == "oneWeek"
    assert body["search_domain_filter"] == "www.sohu.com"

    # engine 覆盖
    await resources.zhipu_search("x", engine="search_std")
    assert captured["json"]["search_engine"] == "search_std"
    assert "search_recency_filter" not in captured["json"]

    # 非 200 报错
    http_log["routes"]["bigmodel.cn"] = lambda u, k: FakeResp(
        401, {"error": {"message": "Invalid API Key"}})
    with pytest.raises(RuntimeError, match="智谱搜索失败.*401"):
        await resources.zhipu_search("x")

    # 未配置 → 不发网络
    monkeypatch.setattr(CONFIG.resources, "zhipu_key", "")
    with pytest.raises(RuntimeError, match="未配置智谱"):
        await resources.zhipu_search("x")


# ---------------------------------------------------------------- 网页抓取
NOISY_HTML = """<html><head><title>测试页标题</title><style>.x .y</style>
<script>var a=1;</script></head><body>
<nav><a href="/">首页</a><a href="/about">关于</a></nav>
<header>站点横幅 LOGO</header>
<aside>侧边推荐：随便什么</aside>
<footer>版权所有 © 2026 备案号</footer>
<main><article><h1>正文标题</h1>
<p>{p1}</p><p>{p2}</p><p>{p3}</p>
<table><tr><td>表格数据</td></tr></table>
</article></main></body></html>"""


def _page_html() -> str:
    # 三段互不相同（trafilatura 会去重重复段落）
    mk = lambda w: (w + "的具体论述与展开，") * 20   # noqa: E731
    return NOISY_HTML.format(p1=mk("第一段"), p2=mk("第二段"), p3=mk("第三段"))


async def test_fetch_page_direct_and_cache(http_log, monkeypatch):
    from loadn_webui import resources
    from loadn_webui.config import PATHS

    # 保险丝：本测的用例都不应触发 CDP，真触发说明直接提取链退化
    async def cdp_fail(url, *, wait, html_out):
        raise RuntimeError("should-not-here")
    monkeypatch.setattr(resources, "_fetch_via_cdp", cdp_fail)

    html = _page_html()
    http_log["routes"]["example.com"] = lambda u, k: FakeResp(
        200, text=html, headers={"content-type": "text/html; charset=utf-8"})
    out = await resources.fetch_page("https://example.com/post?a=1")
    assert out["ok"] and out["method"] == "direct" and out["cached"] is False
    assert "正文标题" in out["text"] and "表格数据" in out["text"]
    for noise in ("站点横幅", "版权所有", "侧边推荐", "var a=1"):
        assert noise not in out["text"]
    assert len(out["text"]) > 500

    # 缓存命中（不再发网络）
    http_log["routes"].clear()
    out2 = await resources.fetch_page("https://example.com/post?a=1")
    assert out2["cached"] is True and out2["method"] == "cache"
    assert out2["path"] == out["path"]

    # --force 重抓
    http_log["routes"]["example.com"] = lambda u, k: FakeResp(
        200, text=html, headers={"content-type": "text/html"})
    out3 = await resources.fetch_page("https://example.com/post?a=1", force=True)
    assert out3["cached"] is False and out3["method"] == "direct"

    # 非 HTML / 非 200 / 挑战页都记为直接抓失败 → 转 CDP（下一测覆盖兜底）；
    # 这里验证 CDP 也失败时合并报错
    async def cdp_fail2(url, *, wait, html_out):
        raise RuntimeError("sandbox_cdp_error: no chrome")
    monkeypatch.setattr(resources, "_fetch_via_cdp", cdp_fail2)
    http_log["routes"]["example.com"] = lambda u, k: FakeResp(
        403, text="Forbidden")
    with pytest.raises(RuntimeError, match=r"抓取失败.*HTTP 403.*CDP"):
        await resources.fetch_page("https://example.com/403", force=True)


async def test_fetch_page_cdp_fallback(http_log, monkeypatch, tmp_path):
    from loadn_webui import resources

    async def fake_cdp(url, *, wait, html_out):
        html_out.write_text(_page_html(), encoding="utf-8")
        return "CDP渲染标题"
    monkeypatch.setattr(resources, "_fetch_via_cdp", fake_cdp)

    # 情形1：直接抓 403 → CDP
    http_log["routes"]["example.com"] = lambda u, k: FakeResp(403, text="denied")
    out = await resources.fetch_page("https://example.com/js", force=True)
    assert out["method"] == "cdp" and out["title"] == "CDP渲染标题"
    assert "正文标题" in out["text"] and "版权所有" not in out["text"]

    # 情形2：反爬挑战页特征 → CDP
    http_log["routes"]["example.com"] = lambda u, k: FakeResp(
        200, text="<html>Please wait... Just a moment...</html>",
        headers={"content-type": "text/html"})
    out = await resources.fetch_page("https://example.com/cf", force=True)
    assert out["method"] == "cdp"


# ---------------------------------------------------------------- 图片 OCR
IMG_HTML = """<html><body>
<nav><img src="https://img.example.com/logo.png"></nav>
<main><article><h1>带图正文标题</h1>
<p>{p1}</p><p>{p2}</p>
<img src="https://img.example.com/chart-a.png">
<img src="https://img.example.com/chart-a.png">
<img data-src="https://img.example.com/chart-b.png">
<img src="https://img.example.com/ads/300x250/promo.jpg">
<img src="/relative/pic-c.png">
</article></main></body></html>"""


def test_image_url_filter():
    from loadn_webui.resources import _image_url_filtered
    assert _image_url_filtered("https://x/avatar/1.png")
    assert _image_url_filtered("https://x/i16x16con.png")
    assert _image_url_filtered("")
    assert not _image_url_filtered("https://x/photos/800x600/shot.jpg")
    assert not _image_url_filtered("data:image/png;base64,AAAA")


def test_image_likely_text():
    import io
    from PIL import Image
    from loadn_webui.resources import _image_likely_text
    img = Image.new("RGB", (300, 200))
    v, px = 42, []
    for _ in range(300 * 200):                  # LCG 伪随机噪点撑大文件
        v = (v * 1103515245 + 12345) % (1 << 31)
        px.append(((v >> 16) & 255, (v >> 8) & 255, v & 255))
    img.putdata(px)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    assert _image_likely_text(buf.getvalue()) is True
    buf2 = io.BytesIO()
    Image.new("RGB", (40, 40)).save(buf2, "PNG")
    assert _image_likely_text(buf2.getvalue()) is False


def test_main_image_srcs():
    from loadn_webui.resources import _main_image_srcs
    para = ("主要内容段落文字的具体展开论述，" * 20)
    html = IMG_HTML.format(p1=para + "甲", p2=para + "乙")
    srcs = _main_image_srcs(html, "https://www.example.com/news/1")
    # nav 的 logo 不取；重复去重；懒加载 data-src 取；广告+小尺寸滤掉；相对路径解析
    assert srcs == ["https://img.example.com/chart-a.png",
                    "https://img.example.com/chart-b.png",
                    "https://www.example.com/relative/pic-c.png"]


async def test_fetch_page_image_ocr(http_log, monkeypatch):
    from loadn_webui import resources

    async def fake_ocr(data, *, vlm):
        return "图里的文字内容"
    monkeypatch.setattr(resources, "_ocr_one_image", fake_ocr)
    monkeypatch.setattr(resources, "_image_likely_text", lambda d: True)

    para = ("主要内容段落文字的具体展开论述，" * 30)
    http_log["routes"]["www.example.com"] = lambda u, k: FakeResp(
        200, text=IMG_HTML.format(p1=para, p2=para),
        headers={"content-type": "text/html"})
    http_log["routes"]["img.example.com"] = lambda u, k: FakeResp(
        200, content=b"\x89PNG-fake-bytes" * 100)

    out = await resources.fetch_page("https://www.example.com/news/9", force=True)
    assert out["ok"] and out["method"] == "direct"
    assert [im["text"] for im in out["images"]] == ["图里的文字内容"] * 3
    assert "## 关键图片内容（OCR ×3）" in out["text"]
    assert "图里的文字内容" in out["text"]
    # 广告/小尺寸 URL 没被下载
    assert not any("promo" in u or "300x250" in u
                   for _, u, _ in http_log["records"])

    # off → 不 OCR
    async def _boom(*a, **k):
        raise AssertionError("ocr=off 不应触发 OCR")
    monkeypatch.setattr(resources, "_ocr_page_images", _boom)
    out = await resources.fetch_page("https://www.example.com/news/9", force=True,
                                     ocr="off")
    assert out["images"] == [] and "关键图片" not in out["text"]


# ---------------------------------------------------------------- ping_all
async def test_ping_all(http_log, monkeypatch):
    from loadn_webui import resources
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "sandbox_api_key", "***REDACTED***")
    monkeypatch.setattr(CONFIG.resources, "sms_token", "t")
    monkeypatch.setattr(CONFIG.resources, "twocaptcha_key", "k")
    monkeypatch.setattr(CONFIG.resources, "bocha_key", "b")
    monkeypatch.setattr(CONFIG.resources, "zhipu_key", "z")
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "ark-key")   # vlm 回退

    http_log["routes"]["8686/health"] = lambda u, k: FakeResp(200)
    http_log["routes"]["21111/health"] = lambda u, k: FakeResp(200)
    http_log["routes"]["21111/mcp"] = lambda u, k: FakeResp(200, {"jsonrpc": "2.0"})
    http_log["routes"]["generate_204"] = lambda u, k: FakeResp(204)
    http_log["routes"]["sms.example.test"] = lambda u, k: FakeResp(200, {"count": 5, "items": []})
    http_log["routes"]["2captcha.com"] = lambda u, k: FakeResp(200, text="3.15")
    http_log["routes"]["bochaai.com"] = lambda u, k: FakeResp(
        200, {"data": {"webPages": {"value": [{"name": "n", "url": "u"}]}}})
    http_log["routes"]["bigmodel.cn"] = lambda u, k: FakeResp(
        200, {"search_result": [{"title": "n", "link": "https://u", "content": "c"}]})

    out = await resources.ping_all(["ocr", "sandbox", "sandbox_mcp", "proxy",
                                    "sms", "vlm", "twocaptcha", "bocha", "zhipu"])
    assert all(r["ok"] for r in out.values()), out
    assert out["twocaptcha"]["msg"].startswith("余额")
    assert "search_pro" in out["zhipu"]["msg"]
    # vlm 只查配置不花钱：没有打到 ark
    assert not any("ark" in url for _, url, _ in http_log["records"])
    assert out["vlm"]["msg"].startswith("已配置")

    # 未配置 → 不发网络
    monkeypatch.setattr(CONFIG.resources, "bocha_key", "")
    monkeypatch.setattr(CONFIG.resources, "zhipu_key", "")
    out = await resources.ping_all(["bocha", "zhipu"])
    assert out["bocha"]["ok"] is False and "未配置" in out["bocha"]["msg"]
    assert out["zhipu"]["ok"] is False and "未配置" in out["zhipu"]["msg"]


# ---------------------------------------------------------------- ocr
async def test_ocr_parse_poll(tmp_path, http_log):
    from loadn_webui import resources
    f = tmp_path / "doc.png"
    f.write_bytes(b"\x89PNG fake")
    states = iter(["processing", "completed"])
    http_log["routes"]["8686/parse"] = lambda u, k: FakeResp(
        200, {"err_code": 0, "data": {"task_id": "t1", "status": "pending"}})
    http_log["routes"]["8686/query"] = lambda u, k: FakeResp(200, {
        "err_code": 0, "data": {"task_id": "t1", "status": next(states),
                                "ocr_result": "识别文本", "method": "RapidOCR (图片)",
                                "time": 0.6, "vlm_used": "false", "extract": {}}})
    out = await resources.ocr_parse(f, vlm="false", poll_interval=0.01, poll_timeout=3)
    assert out["text"] == "识别文本" and out["method"].startswith("RapidOCR")

    # 提交报错
    http_log["routes"]["8686/parse"] = lambda u, k: FakeResp(
        200, {"err_code": 500, "err_msg": "boom"})
    with pytest.raises(RuntimeError, match="boom"):
        await resources.ocr_parse(f, poll_interval=0.01)

    # 服务端标 failed → 立即带 error 报错（不傻等超时）
    http_log["routes"]["8686/parse"] = lambda u, k: FakeResp(
        200, {"err_code": 0, "data": {"task_id": "t3", "status": "pending"}})
    http_log["routes"]["8686/query"] = lambda u, k: FakeResp(200, {
        "err_code": 0, "data": {"task_id": "t3", "status": "failed",
                                "ocr_result": "", "error": "bad pdf"}})
    with pytest.raises(RuntimeError, match=r"OCR 解析失败.*bad pdf"):
        await resources.ocr_parse(f, poll_interval=0.01, poll_timeout=2)

    # 超时
    http_log["routes"]["8686/parse"] = lambda u, k: FakeResp(
        200, {"err_code": 0, "data": {"task_id": "t2", "status": "pending"}})
    http_log["routes"]["8686/query"] = lambda u, k: FakeResp(200, {
        "err_code": 0, "data": {"task_id": "t2", "status": "processing"}})
    with pytest.raises(TimeoutError):
        await resources.ocr_parse(f, poll_interval=0.01, poll_timeout=0.05)


# ---------------------------------------------------------------- vlm
async def test_vlm_ask_payload(tmp_path, http_log, monkeypatch):
    from loadn_webui import resources
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "vlm_api_key", "")      # 回退 titlegen
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "sk-fallback")
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG fake")

    captured = {}

    def capture(url, kw):
        captured["url"] = url
        captured["headers"] = kw.get("headers")
        captured["json"] = kw.get("json")
        return FakeResp(200, {"choices": [{"message": {"content": "图中是一只猫"}}]})

    http_log["routes"]["chat/completions"] = capture
    ans = await resources.vlm_ask([img], "描述这张图")
    assert ans == "图中是一只猫"
    assert captured["url"].startswith("https://ark.cn-beijing.volces.com/api/v3")
    assert captured["headers"]["Authorization"] == "Bearer sk-fallback"
    content = captured["json"]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image_url", "text"]
    assert content[0]["image_url"]["url"].startswith("data:image/")
    assert content[1]["text"] == "描述这张图"

    # 两边 key 都空 → 明确报错
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "")
    with pytest.raises(RuntimeError, match="未配置"):
        await resources.vlm_ask([img], "q")


# ---------------------------------------------------------------- sms
async def test_sms_wait(http_log, monkeypatch):
    from loadn_webui import resources
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "sms_token", "t")
    old = {"ts": "2026-09-03T10:00:00+08:00", "body": "旧验证码 111"}
    new = {"ts": "2026-09-03T10:05:00+08:00", "body": "新验证码 222"}
    http_log["routes"]["sms.example.test"] = lambda u, k: FakeResp(
        200, {"count": 2, "items": [new, old]})

    it = await resources.sms_wait("验证码", since_ts=0, interval=0.01)
    assert "222" in it["body"]

    # since 在最新之后 → 没有新短信 → 超时
    from datetime import datetime
    later = datetime.fromisoformat("2026-09-03T12:00:00+08:00").timestamp()
    with pytest.raises(TimeoutError):
        await resources.sms_wait("验证码", since_ts=later, timeout_s=0.05, interval=0.01)


# ---------------------------------------------------------------- mail（126 邮箱）
def test_extract_codes():
    from loadn_webui.resources import _extract_codes
    assert _extract_codes("您的验证码是 491573，10 分钟内有效") == ["491573"]
    assert _extract_codes("Your verification code: 582914. It expires soon.") == ["582914"]
    assert _extract_codes("验证码 4321，请勿泄露，重复 4321") == ["4321"]    # 去重
    # 日期/有效期片段（前后紧邻 -/. 的数字段）不算验证码
    assert _extract_codes("验证码有效期至 2026-09-13 12:00，请尽快使用") == []
    # 关键词不在 ±60 字符窗口内的长订单号不误报
    assert _extract_codes("订单号 88888888 已创建。" + "废话" * 40 + "感谢您的支持") == []
    assert _extract_codes("") == []


def test_parse_mail_html_only():
    """html-only 邮件：style 不进正文、链接取 href、实体反转义、中文头解码。"""
    from email.message import EmailMessage
    from loadn_webui.resources import _parse_mail
    msg = EmailMessage()
    msg["From"] = "GitHub <noreply@github.com>"
    msg["To"] = "user@example.com"
    msg["Subject"] = "【GitHub】您的验证码"
    msg["Date"] = "Sat, 13 Sep 2026 00:31:00 +0800"
    msg.set_content("")
    msg.add_alternative(
        "<style>.x{color:red}</style>"
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<p>您的验证码是 <b>491573</b>，10 分钟内有效</p>"
        '<a href="https://example.com/activate?x=1&amp;y=2">激活账户</a>'
        "</body></html>", subtype="html")
    it = _parse_mail(msg.as_bytes())
    assert it["codes"] == ["491573"]
    assert "491573" in it["body"] and "color:red" not in it["body"]
    assert it["links"] == ["https://example.com/activate?x=1&y=2"]
    assert it["receive_time"] == "2026-09-13 00:31:00"
    assert it["from"] == "GitHub <noreply@github.com>"


async def test_mail_wait(monkeypatch):
    from loadn_webui import resources
    old = {"ts": 100.0, "receive_time": "", "from": "", "subject": "旧邮件",
           "body": "", "links": [], "codes": []}
    new = {**old, "ts": 200.0, "subject": "验证码 654321", "codes": ["654321"]}
    calls = {"n": 0}

    async def fake_recent(kw="", n=10, box="", folder="INBOX"):
        calls["n"] += 1                    # 第 1 次取基线：只有旧邮件
        return {"count": 2, "items": ([new, old] if calls["n"] > 1 else [old])}
    monkeypatch.setattr(resources, "mail_recent", fake_recent)
    it = await resources.mail_wait("验证码", interval=0.01, box="163")
    assert it["ts"] == 200.0 and it["codes"] == ["654321"]

    async def fake_empty(kw="", n=10, box="", folder="INBOX"):
        return {"count": 0, "items": []}
    monkeypatch.setattr(resources, "mail_recent", fake_empty)
    with pytest.raises(TimeoutError):
        await resources.mail_wait("验证码", timeout_s=0.05, interval=0.01)


async def test_mail_requires_auth_code(monkeypatch, capsys):
    from loadn_webui import cli, resources
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "mail_auth_code", "")
    monkeypatch.setattr(CONFIG.resources, "mailboxes", [])
    with pytest.raises(RuntimeError, match="授权码"):
        await resources.mail_send("a@b.c", "标题", "正文")
    rc = await asyncio.to_thread(cli.main, ["r", "mail", "--n", "5"])
    assert rc == 1 and "错误" in capsys.readouterr().err


def test_mailbox_selection(monkeypatch):
    from loadn_webui import resources
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "mail_user", "user@example.com")
    monkeypatch.setattr(CONFIG.resources, "mail_auth_code", "c1")
    monkeypatch.setattr(CONFIG.resources, "mailboxes", [
        {"user": "user2@example.com", "auth_code": "c2",
         "imap": "imap.163.com:993", "smtp": "smtp.163.com:465"},
        {"user": "alias@example.com", "auth_code": "c3",
         "imap": "imap.qq.com:993", "smtp": "smtp.qq.com:465"},
        {"user": "user@example.com", "auth_code": "stale"},  # 与主邮箱同地址→忽略
    ])
    mb = resources._mailbox()                                 # 默认=主邮箱
    assert mb["user"] == "user@example.com" and mb["auth_code"] == "c1"
    assert resources._mailbox("163")["auth_code"] == "c2"     # 唯一子串
    assert resources._mailbox("alias@example.com")["auth_code"] == "c3"
    assert resources._mailbox("qq")["imap"] == "imap.qq.com:993"  # 服务器域名也算
    with pytest.raises(RuntimeError, match="未知邮箱"):
        resources._mailbox("gmail")
    with pytest.raises(RuntimeError, match="匹配到多个"):       # 三家的 .com 都含 com
        resources._mailbox("com")


# ---------------------------------------------------------------- captcha
async def test_captcha_solve(tmp_path, http_log, monkeypatch):
    from loadn_webui import resources
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "twocaptcha_key", "capkey")
    img = tmp_path / "cap.png"
    img.write_bytes(b"png")
    answers = iter(["CAPCHA_NOT_READY", "OK|TOK123"])

    async def _run():
        http_log["routes"]["in.php"] = lambda u, k: FakeResp(
            200, {"status": 1, "request": "777"})
        http_log["routes"]["res.php"] = lambda u, k: FakeResp(200, text=next(answers))
        return await resources.captcha_solve(image=img, interval=0.01, timeout_s=2)

    assert await _run() == "TOK123"
    posted = [kw for m, u, kw in http_log["records"] if m == "POST"]
    assert posted[0]["data"]["method"] == "base64"


# ---------------------------------------------------------------- CLI（进程内，线程里跑避免事件循环冲突）
async def test_cli_r(monkeypatch, capsys):
    import asyncio
    from loadn_webui import cli, resources

    async def fake_ping(only=None):
        return {"ocr": {"ok": True, "msg": "ok", "ms": 3},
                "bocha": {"ok": False, "msg": "401 Invalid API KEY"}}
    monkeypatch.setattr(resources, "ping_all", fake_ping)
    rc = await asyncio.to_thread(cli.main, ["r", "ping"])
    assert rc == 1                               # bocha FAIL → exit 1
    out = capsys.readouterr().out
    assert "ocr" in out and "FAIL" in out

    async def fake_search(q, *, n=10, freshness=None):
        return [{"name": "结果一", "url": "https://a", "snippet": "s", "siteName": "", "date": ""}]
    monkeypatch.setattr(resources, "bocha_search", fake_search)
    rc = await asyncio.to_thread(cli.main, ["r", "search", "测试", "--json"])
    assert rc == 0 and "结果一" in capsys.readouterr().out

    # --via zhipu 走智谱；auto 未配 key 落博查
    from loadn_webui.config import CONFIG

    async def fake_zhipu(q, *, n=10, engine=None, recency=None, domain=None):
        return [{"name": "智谱结果", "url": "https://z", "snippet": "s",
                 "siteName": "知乎", "date": "2026-09-01"}]
    monkeypatch.setattr(resources, "zhipu_search", fake_zhipu)
    monkeypatch.setattr(CONFIG.resources, "zhipu_key", "")
    rc = await asyncio.to_thread(cli.main, ["r", "search", "q", "--via", "zhipu",
                                            "--se", "search_std", "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and "智谱结果" in out
    monkeypatch.setattr(CONFIG.resources, "zhipu_key", "k")
    rc = await asyncio.to_thread(cli.main, ["r", "search", "q", "--json"])
    assert "智谱结果" in capsys.readouterr().out      # auto → zhipu

    async def fake_fetch(url, *, force=False, wait=5.0, proxy=False, ocr="auto",
                         max_chars=60000):
        return {"ok": True, "url": url, "title": "T", "text": "正文" * 300,
                "chars": 600, "method": "cdp", "cached": False, "path": "/tmp/x.md",
                "images": [{"src": "https://i", "text": "图字", "chars": 2}]}
    monkeypatch.setattr(resources, "fetch_page", fake_fetch)
    rc = await asyncio.to_thread(cli.main, ["r", "fetch", "https://x", "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and '"method": "cdp"' in out

    # 未配置 → stderr + exit 1
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "sms_token", "")
    rc = await asyncio.to_thread(cli.main, ["r", "sms", "--n", "5"])
    assert rc == 1 and "错误" in capsys.readouterr().err


# ---------------------------------------------------------------- skills 默认挂载
async def test_new_skills_default_mounted(client, ws_root):
    from loadn_webui import skills as skills_mod
    names = {s["name"] for s in skills_mod.available()}
    assert {"file-parse", "web-hands-on", "mobile-sms", "email-inbox"} <= names

    r = await client.post("/api/sessions", json={})
    sid = r.json()["session"]["id"]
    mounted = {p.name for p in (ws_root / sid / ".claude" / "skills").iterdir()}
    assert {"file-parse", "web-hands-on", "mobile-sms", "email-inbox"} <= mounted
