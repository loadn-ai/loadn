"""外部资源层：OCR / AIO 沙箱 / 代理 / 短信 / VLM / 2captcha / 博查搜索 / adb。

同一套函数服务两个入口——设置页的「测试全部」（HTTP API）和 agent 用的
`bin/wd.py r …` CLI。全部 async；HTTP 只走模块级 `_get/_post`，测试 monkeypatch
这两个即可零网络覆盖（承袭 skillhub 模式）。配置在调用时从 CONFIG.resources 读，
设置页保存后立即生效。密钥永远不进 skill 文本、不进子进程 env。
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import time
from pathlib import Path

from .config import CONFIG, PATHS


def _sec(name: str) -> str:
    """资源密钥统一入口（vault AES-GCM，带缓存）——config 明文已退役。"""
    from . import vault as vault_mod
    return vault_mod.get_res_secret(name)

BOCHA_API_BASE = "https://api.bochaai.com"
ZHIPU_WEB_SEARCH_API = "https://open.bigmodel.cn/api/paas/v4/web_search"
TWOCAPTCHA_API = "https://2captcha.com"


def _res():
    return CONFIG.resources


# ---------------------------------------------------------------- HTTP 底座
async def _get(url: str, *, headers=None, params=None, timeout=15.0, proxy=None,
              follow_redirects: bool = False):
    import httpx
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy or None,
                                 follow_redirects=follow_redirects) as cl:
        return await cl.get(url, headers=headers, params=params)


async def _post(url: str, *, headers=None, params=None, data=None, files=None,
                json=None, timeout=30.0, proxy=None):
    import httpx
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy or None) as cl:
        return await cl.post(url, headers=headers, params=params, data=data,
                             files=files, json=json)


# ---------------------------------------------------------------- OCR
async def ocr_parse(path: str | Path, *, vlm: str = "false", max_pages: int = 0,
                    prompt: str = "", extract: str = "",
                    poll_interval: float = 2.0, poll_timeout: float | None = None
                    ) -> dict:
    """多格式 OCR（图片/pdf/office）→ 文本。异步提交 + 轮询，永不 sync=true 长轮询。

    vlm=false 走 RapidOCR（秒级，清晰文本）；auto/true 走 VLM（分钟级，扫描件/
    多栏/表格/手写）。返回 {"task_id","text","method","time_s","vlm_used","extract"}。
    """
    r = _res()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    if poll_timeout is None:
        poll_timeout = 300.0 if vlm in ("auto", "true") else 90.0
    # save=true：结果落服务端才能用 /query 轮询（新版服务端 async 已强制落库，
    # 这里显式传 true 兼容旧版）；failed 状态带 error 字段，立即报错不傻等超时
    form = {"vlm": vlm, "sync": "false", "save": "true"}
    if max_pages:
        form["max_pages"] = str(max_pages)
    if prompt:
        form["prompt"] = prompt
    if extract:
        form["extract"] = extract
    fname, raw = p.name, p.read_bytes()
    # 服务端白名单不收 webp/gif：先转 png（gif 取首页）再上传
    if p.suffix.lower() in (".webp", ".gif"):
        from io import BytesIO

        from PIL import Image
        img = Image.open(BytesIO(raw))
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)
        img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        fname, raw = p.stem + ".png", buf.getvalue()
    resp = await _post(f"{r.ocr_url.rstrip('/')}/parse", data=form,
                       files={"file": (fname, raw)}, timeout=60.0)
    d = resp.json()
    if d.get("err_code") != 0:
        raise RuntimeError(f"OCR 提交失败: {d.get('err_msg') or d}")
    task_id = d["data"]["task_id"]
    t0 = time.monotonic()
    while True:
        q = (await _get(f"{r.ocr_url.rstrip('/')}/query",
                        params={"task_id": task_id}, timeout=15.0)).json()
        if q.get("err_code") != 0:
            raise RuntimeError(f"OCR 查询失败: {q.get('err_msg') or q}")
        data = q["data"]
        if data.get("status") == "completed":
            return {"task_id": task_id, "text": data.get("ocr_result") or "",
                    "method": data.get("method"), "time_s": data.get("time"),
                    "vlm_used": data.get("vlm_used"), "extract": data.get("extract") or {}}
        if data.get("status") == "failed":
            raise RuntimeError(f"OCR 解析失败: {data.get('error') or '未知原因'}"
                               f"（task={task_id}）")
        if time.monotonic() - t0 > poll_timeout:
            raise TimeoutError(f"OCR {poll_timeout:.0f}s 未完成（task={task_id}, "
                               f"status={data.get('status')}），可提高 --timeout 或后台跑")
        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------- VLM
def vlm_endpoint() -> tuple[str, str, str]:
    """(base, key, model)。vlm 段留空则继承 titlegen（同一把 ark key）。"""
    r = _res()
    base = (r.vlm_api_base or CONFIG.titlegen.api_base).rstrip("/")
    # 兼容整段 endpoint 粘贴
    base = base.removesuffix("/chat/completions")
    key = _sec("vlm_api_key") or CONFIG.titlegen.api_key
    return base, key, r.vlm_model


async def vlm_ask(images: list[str | Path], prompt: str, *, model: str | None = None,
                  max_tokens: int = 1024) -> str:
    """问视觉模型：本地图片（可多张）或图片 URL + 文本提问 → 文本回答。"""
    base, key, default_model = vlm_endpoint()
    if not key:
        raise RuntimeError("未配置 VLM API key（settings resources.vlm_api_key "
                           "或 titlegen.api_key）")
    content: list[dict] = []
    for img in images:
        s = str(img)
        if s.startswith(("http://", "https://", "data:")):
            content.append({"type": "image_url", "image_url": {"url": s}})
        else:
            p = Path(s)
            if not p.exists():
                raise FileNotFoundError(f"图片不存在: {p}")
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}})
    content.append({"type": "text", "text": prompt})
    resp = await _post(f"{base}/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": model or default_model, "max_tokens": max_tokens,
                             "messages": [{"role": "user", "content": content}]},
                       timeout=120.0)
    d = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(f"VLM 调用失败 ({resp.status_code}): "
                           f"{str(d)[:200]}")
    return str(((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


# ---------------------------------------------------------------- 博查搜索
async def bocha_search(query: str, *, n: int = 10, freshness: str | None = None
                       ) -> list[dict]:
    """博查中文网页搜索 → [{name,url,snippet,siteName,date}]。key 无效/未配抛错。"""
    if not _sec("bocha_key"):
        raise RuntimeError("未配置博查 key（resources.bocha_key）")
    body: dict = {"query": query, "count": n, "summary": True}
    if freshness:
        body["freshness"] = freshness
    resp = await _post(f"{BOCHA_API_BASE}/v1/web-search",
                       headers={"Authorization": f"Bearer {_sec('bocha_key')}"},
                       json=body, timeout=20.0)
    d = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(f"博查搜索失败 ({resp.status_code}): "
                           f"{d.get('message') or str(d)[:150]}")
    out = []
    for it in ((d.get("data") or {}).get("webPages") or {}).get("value") or []:
        out.append({"name": it.get("name") or "", "url": it.get("url") or "",
                    "snippet": it.get("summary") or it.get("snippet") or "",
                    "siteName": it.get("siteName") or "",
                    "date": it.get("dateLastCrawled") or ""})
    return out


# ---------------------------------------------------------------- 智谱搜索
async def zhipu_search(query: str, *, n: int = 10, engine: str | None = None,
                       recency: str | None = None, domain: str | None = None
                       ) -> list[dict]:
    """智谱 Web Search API → 与 bocha_search 同构 [{name,url,snippet,siteName,date}]。

    engine：search_std（0.01 元）/ search_pro（默认，0.03）/ search_pro_sogou
    （腾讯/知乎生态）/ search_pro_quark（垂直）。recency 如 noLimit/oneHour/oneDay/
    oneWeek/oneYear；domain 限定站点。key 无效/未配抛错。
    """
    r = _res()
    if not _sec("zhipu_key"):
        raise RuntimeError("未配置智谱 key（resources.zhipu_key）")
    body: dict = {"search_engine": engine or r.zhipu_engine,
                  "search_query": query, "count": n, "content_size": "medium"}
    if recency:
        body["search_recency_filter"] = recency
    if domain:
        body["search_domain_filter"] = domain
    resp = await _post(ZHIPU_WEB_SEARCH_API,
                       headers={"Authorization": f"Bearer {_sec('zhipu_key')}"},
                       json=body, timeout=20.0)
    d = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(f"智谱搜索失败 ({resp.status_code}): "
                           f"{str(d.get('error') or d)[:150]}")
    out = []
    for it in d.get("search_result") or []:
        # search_std 常整体不带 link（内容全但无 URL），保留之——title/snippet 仍有用
        out.append({"name": it.get("title") or "", "url": it.get("link") or "",
                    "snippet": it.get("content") or "",
                    "siteName": it.get("media") or "",
                    "date": it.get("publish_date") or ""})
    return out


# ---------------------------------------------------------------- 短信
async def sms_recent(kw: str = "", n: int = 20) -> dict:
    """查询真机最近短信 → {"count","items":[{id,ts,receive_time,sender,body,...}]}。"""
    r = _res()
    if not _sec("sms_token"):
        raise RuntimeError("未配置短信服务 token（resources.sms_token）")
    resp = await _get(f"{r.sms_url.rstrip('/')}/recent",
                      params={"token": _sec("sms_token"), "n": str(n), "kw": kw},
                      timeout=20.0)
    if resp.status_code != 200:
        raise RuntimeError(f"短信查询失败 ({resp.status_code}): {resp.text[:150]}")
    return resp.json()


async def sms_wait(kw: str, *, timeout_s: float = 180.0, interval: float = 5.0,
                   since_ts: float | None = None) -> dict:
    """等一条含关键字的新短信（默认等 3 分钟）。命中返回该条，超时抛错。

    since_ts 为 epoch 秒；None=取当前已有最新一条的时间戳之后才算新。
    """
    if since_ts is None:
        try:
            cur = await sms_recent(kw, n=1)
            items = cur.get("items") or []
            since_ts = _parse_ts(items[0]["ts"]) if items else 0.0
        except Exception:  # noqa: BLE001 - 查不到基线就从头等
            since_ts = 0.0
    t0 = time.monotonic()
    while True:
        d = await sms_recent(kw, n=50)
        for it in d.get("items") or []:
            if _parse_ts(it.get("ts") or "") > since_ts:
                return it
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"{timeout_s:.0f}s 内未收到匹配短信 kw={kw!r}")
        await asyncio.sleep(interval)


def _parse_ts(s: str) -> float:
    """'2026-09-02T15:17:08+08:00' → epoch 秒；解析失败返回 0。"""
    from datetime import datetime
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------- 邮箱（126）
# 平台公共邮箱：注册/登录要邮箱验证码时用（skill: email-inbox）。126/163 系
# IMAP 登录后必须先发 ID 命令再 SELECT，否则报 Unsafe Login——imaplib 没有
# 原生 ID，走 _simple_command。授权码不是登录密码，只存 config。

_MAIL_CODE_KW = ("验证码", "校验码", "动态码", "激活码", "code", "verification",
                 "verify", "passcode", "one-time", "otp")


def _extract_codes(text: str, limit: int = 3) -> list[str]:
    """邮件文本 → 验证码候选：关键词 ±60 字符窗口内的 4-8 位数字（去重保序）。

    前后紧邻 - / . 的数字段视为日期/版本号片段排除；候选仅供参考，body
    始终原样返回由 agent 终判。
    """
    import re
    if not text:
        return []
    code_re = re.compile(r"(?<!\d)(?<!\d[-/.])(\d{4,8})(?!\d)(?![-/.]\d)")
    out: list[str] = []
    for m in code_re.finditer(text):
        s, e = m.span(1)
        ctx = text[max(0, s - 60):e + 60].lower()
        if any(k in ctx for k in _MAIL_CODE_KW) and m.group(1) not in out:
            out.append(m.group(1))
    return out[:limit]


def _all_mailboxes() -> list[dict]:
    """全部邮箱配置：单邮箱字段永远排第 1（设置页编辑的就是它），其后接
    config resources.mailboxes 追加项；同地址条目以单邮箱字段为准。"""
    r = _res()
    boxes = [{"user": r.mail_user, "auth_code": _sec("mail_auth_code"),
              "imap": r.mail_imap, "smtp": r.mail_smtp}]
    for b in r.mailboxes or []:
        if not isinstance(b, dict) or not b.get("user") or b["user"] == r.mail_user:
            continue
        boxes.append({k: str(b.get(k) or "") for k in ("user", "auth_code",
                                                       "imap", "smtp")})
    return boxes


def _mailbox(box: str = "") -> dict:
    """选邮箱 → {user,auth_code,imap,smtp}。box 空=主邮箱；否则在地址+服务器
    域名上做唯一子串匹配（如 163 / qq / king——qq 命中 imap.qq.com 的域名邮箱），
    多义/未知都报错并列出可选。"""
    boxes = _all_mailboxes()
    if not box:
        return boxes[0]
    hits = [b for b in boxes
            if box.lower() in f"{b['user']} {b['imap']} {b['smtp']}".lower()]
    if not hits:
        raise RuntimeError(f"未知邮箱 {box!r}（可选: "
                           f"{', '.join(b['user'] for b in boxes)}）")
    if len(hits) > 1:
        raise RuntimeError(f"--box {box!r} 匹配到多个邮箱（"
                           f"{', '.join(b['user'] for b in hits)}），请写全地址")
    return hits[0]


def _imap_open(box: dict, folder: str = "INBOX"):
    """按邮箱配置连 IMAP（SSL + 授权码 + ID）→ 已只读 SELECT 的客户端。"""
    import imaplib
    if not box.get("auth_code"):
        raise RuntimeError(f"未配置邮箱授权码（{box.get('user')}）")
    # imaplib 的 Commands 白名单没有 ID（RFC 2971），先注册才能 _simple_command
    imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED", "LOGOUT")
    host, _, port = (box.get("imap") or "imap.126.com:993").partition(":")
    m = imaplib.IMAP4_SSL(host, int(port or 993), timeout=20)
    try:
        m.login(box["user"], box["auth_code"])
        try:    # 126/163 必须发 ID 否则 Unsafe Login；qq 等不支持的服务器忽略失败
            m._simple_command("ID", '("name" "loadn" "version" "0.3.0")')
        except Exception:  # noqa: BLE001
            pass
        typ, _ = m.select(folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"SELECT {folder} 失败: {typ}（常见可用名："
                               f"INBOX / Junk / 垃圾邮件）")
        return m
    except Exception:
        try:
            m.logout()
        except Exception:  # noqa: BLE001 - 登录中途失败，尽力关连接
            pass
        raise


def _parse_mail(data: bytes) -> dict:
    """RFC822 字节 → {ts,receive_time,from,subject,body,links,codes}。

    body 取第一个 text/plain，没有则 text/html 剥标签；截 1500 字符。html
    里的链接单独收进 links（注册激活链接在 href 里，剥标签会丢）。
    """
    import html as html_mod
    import re
    from datetime import datetime
    from email import policy
    from email.header import decode_header, make_header
    from email.parser import BytesParser
    from email.utils import parsedate_to_datetime
    msg = BytesParser(policy=policy.default).parsebytes(data)

    def _h(name: str) -> str:
        v = msg.get(name)
        if not v:
            return ""
        try:
            return str(make_header(decode_header(str(v))))
        except Exception:  # noqa: BLE001 - 怪编码退原文
            return str(v)

    subject, sender = _h("Subject"), _h("From")
    ts = 0.0
    try:
        ts = parsedate_to_datetime(msg.get("Date")).timestamp()
    except Exception:  # noqa: BLE001 - 无/坏 Date 头只能放弃排序基准
        pass
    plain, html = "", ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001 - 单 part 坏不影响整封
            continue
        if part.get_content_type() == "text/plain" and not plain:
            plain = text
        elif part.get_content_type() == "text/html" and not html:
            html = text
    if not plain.strip() and html:   # 空白 plain 让位 html；先剥 style/script
        t = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", html)
        body = html_mod.unescape(re.sub(r"<[^>]+>", " ", t))
    else:
        body = plain
    body = " ".join(body.split())[:1500]
    links = []
    for u in (re.findall(r'(?i)href=[\'"]([^\'"]+)[\'"]', html or "")
              + re.findall(r'https?://[^\s"\'<>]+', body)):   # 纯文本里的裸链接
        u = html_mod.unescape(u).rstrip(").,;")
        if u.startswith(("http://", "https://")) and u not in links:
            links.append(u)
    return {"ts": ts,
            "receive_time": datetime.fromtimestamp(ts).strftime(
                "%Y-%m-%d %H:%M:%S") if ts else "",
            "from": sender, "subject": subject, "body": body,
            "links": links[:5], "codes": _extract_codes(f"{subject}\n{body}")}


async def mail_recent(kw: str = "", n: int = 10, box: str = "",
                       folder: str = "INBOX") -> dict:
    """拉邮箱最近邮件 → {"count","box","items":[…]}（新在前，字段见 _parse_mail）。

    kw 匹配 subject/from/body（大小写不敏感）。为扛 kw 过滤多拉 4 倍、
    上限 80 封。imaplib 是同步库，整个会话丢线程池跑。
    """
    mb = _mailbox(box)

    def _fetch() -> list[dict]:
        m = _imap_open(mb, folder)
        try:
            _, data = m.search(None, "ALL")
            ids = (data[0] or b"").split()
            items = []
            for num in reversed(ids[-min(max(n * 4, n), 80):]):
                _, d = m.fetch(num, "(RFC822)")
                raw = next((x[1] for x in d if isinstance(x, tuple)), None)
                if raw:
                    items.append(_parse_mail(raw))
            return items
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass
    items = await asyncio.to_thread(_fetch)
    if kw:
        k = kw.lower()
        items = [it for it in items if k in it["subject"].lower()
                 or k in it["from"].lower() or k in it["body"].lower()]
    items = items[:n]
    return {"count": len(items), "box": mb["user"], "items": items}


async def mail_wait(kw: str, *, timeout_s: float = 180.0, interval: float = 8.0,
                    since_ts: float | None = None, box: str = "",
                    folder: str = "INBOX") -> dict:
    """等一封含关键字的新邮件（默认 3 分钟）。命中返回该封，超时抛错。

    since_ts 为 epoch 秒；None=以当前最新一封 Date 之后为新。验证码邮件
    通常 1-2 分钟内到；IMAP 每轮都是完整登录，interval 给到 8s 别打太频。
    """
    if since_ts is None:
        try:
            cur = await mail_recent(kw, n=1, box=box, folder=folder)
            items = cur.get("items") or []
            since_ts = items[0]["ts"] if items else 0.0
        except Exception:  # noqa: BLE001 - 查不到基线就从头等
            since_ts = 0.0
    t0 = time.monotonic()
    while True:
        d = await mail_recent(kw, n=50, box=box, folder=folder)
        for it in d.get("items") or []:
            if (it.get("ts") or 0) > since_ts:
                return it
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"{timeout_s:.0f}s 内未收到匹配邮件 kw={kw!r} "
                               f"box={_mailbox(box)['user']} folder={folder}")
        await asyncio.sleep(interval)


async def mail_send(to: str, subject: str = "", body: str = "",
                    box: str = "") -> dict:
    """SMTP SSL 发信 → {"ok","from","to"}。box 选发件邮箱（默认主邮箱）。"""
    import smtplib
    from email.message import EmailMessage
    mb = _mailbox(box)
    if not mb.get("auth_code"):
        raise RuntimeError(f"未配置邮箱授权码（{mb['user']}）")
    if not to:
        raise ValueError("缺少收件人 --to")
    msg = EmailMessage()
    msg["From"], msg["To"] = mb["user"], to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")

    def _send() -> None:
        host, _, port = (mb.get("smtp") or "smtp.126.com:465").partition(":")
        with smtplib.SMTP_SSL(host, int(port or 465), timeout=20) as s:
            s.login(mb["user"], mb["auth_code"])
            s.send_message(msg)
    await asyncio.to_thread(_send)
    return {"ok": True, "from": mb["user"], "to": to}


# ---------------------------------------------------------------- 2captcha
async def captcha_solve(*, image: str | Path | None = None, sitekey: str = "",
                        pageurl: str = "", timeout_s: float = 180.0,
                        interval: float = 5.0) -> str:
    """过验证码：图形码（本地图片）或 reCAPTCHA（sitekey+pageurl）→ token。"""
    if not _sec("twocaptcha_key"):
        raise RuntimeError("未配置 2captcha key（resources.twocaptcha_key）")
    if image:
        p = Path(image)
        if not p.exists():
            raise FileNotFoundError(f"验证码图片不存在: {p}")
        b64 = base64.b64encode(p.read_bytes()).decode()
        resp = await _post(f"{TWOCAPTCHA_API}/in.php",
                           data={"key": _sec("twocaptcha_key"), "method": "base64",
                                 "body": b64, "json": 1}, timeout=30.0)
    elif sitekey and pageurl:
        resp = await _post(f"{TWOCAPTCHA_API}/in.php",
                           data={"key": _sec("twocaptcha_key"), "method": "userrecaptcha",
                                 "googlekey": sitekey, "pageurl": pageurl, "json": 1},
                           timeout=30.0)
    else:
        raise ValueError("需要 --img（图形码）或 --sitekey + --pageurl（reCAPTCHA）")
    d = resp.json()
    if d.get("status") != 1:
        raise RuntimeError(f"2captcha 提交失败: {d.get('request')}")
    cid = str(d["request"])
    t0 = time.monotonic()
    while True:
        q = await _get(f"{TWOCAPTCHA_API}/res.php",
                       params={"key": _sec("twocaptcha_key"), "action": "get", "id": cid},
                       timeout=20.0)
        body = q.text.strip()
        if body.startswith("OK|"):
            return body[3:]
        if body != "CAPCHA_NOT_READY":
            raise RuntimeError(f"2captcha 求解失败: {body}")
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"2captcha {timeout_s:.0f}s 未出结果（id={cid}）")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------- 网页抓取
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/146.0.0.0 Safari/537.36")
# 反爬挑战页特征（cf/极验/101 等）：命中即认为直接抓失败，转 CDP
_CHALLENGE_MARKS = ("just a moment", "checking your browser", "cf-challenge",
                    "verify you are human", "请完成安全验证", "访问验证")
_FETCH_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


def _title_fallback(html: str) -> str:
    import re
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:20000],
                  re.DOTALL | re.IGNORECASE)
    return " ".join(m.group(1).split()) if m else ""


# --------------------------------------------------------- 页面图片 OCR
# 自 SNB-AI-Helper html_processing_tools 移植的筛选思路：URL 关键字 + URL 内
# 尺寸标识先滤一轮，下载后 PIL 再判一轮（尺寸/长宽比/文件大小/透明度）。
_IMG_FILTER_KEYWORDS = (
    "avatar", "profile", "headshot", "icon", "logo", "brand", "badge", "button",
    "arrow", "bullet", "spacer", "pixel", "divider", "separator", "background",
    "banner", "ad", "ads", "promo", "sponsor", "thumb", "thumbnail", "placeholder",
    "watermark", "emoji", "spinner", "loading", "dot", "share", "follow",
    "头像", "图标", "徽标", "广告", "横幅", "水印", "占位",
)


def _image_url_filtered(src: str) -> bool:
    """URL 层面判断图片大概率无文字（图标/头像/广告位/小尺寸标识）。"""
    import re
    if not src:
        return True
    low = src.lower()
    if any(k in low for k in _IMG_FILTER_KEYWORDS):
        return True
    for m in re.findall(r"[\d]+x[\d]+", low):
        dims = re.findall(r"\d+", m)
        if len(dims) >= 2 and (int(dims[0]) < 100 or int(dims[1]) < 100):
            return True
    return False


def _main_image_srcs(html: str, base_url: str = "") -> list[str]:
    """主内容区图片 URL（保序去重）。导航/页脚已先删；相对路径按 base 解析。

    兼容懒加载属性（data-src/data-original/data-lazy-src）与 data: 内联图。
    """
    from urllib.parse import urljoin
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for el in soup(["script", "style", "noscript", "nav", "header", "footer",
                    "aside"]):
        el.decompose()
    region = None
    for sel in ("article", "main", '[role="main"]', '[role="article"]'):
        region = soup.select_one(sel)
        if region is not None:
            break
    scope = region or soup.body or soup
    out, seen = [], set()
    for img in scope.find_all("img"):
        src = (img.get("src") or img.get("data-src") or img.get("data-original")
               or img.get("data-lazy-src") or "")
        if not src:
            continue
        if not src.startswith(("http://", "https://", "data:image/")):
            src = urljoin(base_url, src)
        if src in seen or _image_url_filtered(src):
            continue
        seen.add(src)
        out.append(src)
    return out


def _image_likely_text(data: bytes) -> bool:
    """PIL 层面判断图片可能承载文字（太小/太扁/调色板小图/高透明 → False）。"""
    from io import BytesIO

    from PIL import Image
    try:
        img = Image.open(BytesIO(data))
        w, h = img.size
        if w < 80 or h < 80:                    # 中文字符需要更大尺寸
            return False
        if max(w, h) / max(min(w, h), 1) > 10:  # 极端长宽比 = 装饰条
            return False
        if len(data) < 1024:
            return False
        if img.mode == "P" and w < 200:
            return False
        if img.mode in ("RGBA", "LA"):
            alpha = img.getchannel("A")
            if alpha.getextrema()[1] < 128:     # 全透明 → 图标/水印类
                return False
        return True
    except Exception:  # noqa: BLE001 - 坏图当无字处理
        return False


async def _ocr_one_image(data: bytes, *, vlm: str) -> str:
    """字节 → 临时文件 → ocr_parse → 文本。vlm: false=RapidOCR 秒级 / true=VLM。"""
    import tempfile
    from io import BytesIO

    from PIL import Image
    kind = (Image.open(BytesIO(data)).format or "png").lower()
    kind = {"jpg": "jpeg"}.get(kind, kind)
    with tempfile.NamedTemporaryFile(suffix=f".{kind}", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        out = await ocr_parse(tmp, vlm=vlm,
                              poll_timeout=240.0 if vlm != "false" else 60.0)
        return (out.get("text") or "").strip()
    finally:
        Path(tmp).unlink(missing_ok=True)


async def _ocr_page_images(html: str, url: str, *, mode: str = "auto",
                           max_images: int = 6, budget_s: float = 60.0,
                           proxy: bool = False) -> tuple[list[dict], int]:
    """抓主内容图片并 OCR → ([{src,text,chars}], 跳过数)。

    mode=auto 用 RapidOCR（秒级/张）；mode=vlm 走 VLM（扫描件/表格/手写，分钟级）。
    预算超限即止损，剩余记入跳过数——OCR 永不拖死整个 fetch。
    """
    import base64
    srcs = _main_image_srcs(html, url)[:max_images]
    results, skipped = [], 0
    t0 = time.monotonic()
    for src in srcs:
        if time.monotonic() - t0 > budget_s:
            skipped = len(srcs) - len(results)
            break
        try:
            if src.startswith("data:image/"):
                raw = base64.b64decode(src.split(",", 1)[-1])
            else:
                resp = await _get(src, headers=_FETCH_HEADERS, timeout=12.0,
                                  proxy=_res().proxy if proxy else None)
                if resp.status_code != 200:
                    skipped += 1
                    continue
                raw = resp.content[:8_000_000]
            if not _image_likely_text(raw):
                continue
            text = await _ocr_one_image(raw, vlm="true" if mode == "vlm" else "false")
            if text:
                results.append({"src": src[:300], "text": text,
                                "chars": len(text)})
        except Exception:  # noqa: BLE001 - 单图失败不拖累整体
            skipped += 1
    return results, skipped


def _extract_main(html: str, url: str = "") -> tuple[str, str]:
    """主内容提取 → (text, title)。trafilatura 精确 → 宽松 → BS4 可见文本兜底。

    精简自 SNB-AI-Helper html_processing_tools 的去噪思路（script/style/nav/
    header/footer/aside + 隐藏元素全删），不做图片 OCR——agent 需要时单独走
    `wd r ocr`。
    """
    title = ""
    try:
        import trafilatura

        def _fld(d, key):          # trafilatura 2.x 返回 Document 对象（属性访问）
            try:
                return d.get(key)
            except AttributeError:
                return getattr(d, key, None)

        for kwargs in ({"favor_precision": True}, {"favor_recall": True}):
            try:
                d = trafilatura.bare_extraction(html, url=url or None,
                                                include_tables=True,
                                                include_comments=False, **kwargs)
            except Exception:  # noqa: BLE001 - 提取器对怪页面可能抛错
                continue
            if not d:
                continue
            title = str(_fld(d, "title") or "") or title
            text = str(_fld(d, "text") or "").strip()
            if len(text) >= 200:
                return text, title or _title_fallback(html)
    except ImportError:
        pass
    # BS4 可见文本兜底（bs4 是 server 依赖级，缺失再退纯正则）
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.get_text(strip=True) if soup.title else "") or title
        for el in soup(["script", "style", "noscript", "nav", "header", "footer",
                        "aside", "svg", "iframe", "form", "template"]):
            el.decompose()
        for el in soup.find_all(True):
            if el.get("hidden") is not None:
                el.decompose()
                continue
            style = (el.get("style") or "").lower().replace(" ", "")
            if any(s in style for s in ("display:none", "visibility:hidden",
                                        "opacity:0")):
                el.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines), title
    except ImportError:
        import re
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        return " ".join(text.split()), title


async def _fetch_via_cdp(url: str, *, wait: float, html_out: Path) -> str:
    """CDP 真浏览器渲染（scripts/fetch_page.py，playwright 在 kaggo venv）→ 渲染后 HTML。

    html_out 落盘渲染 HTML；返回 page title。失败抛 RuntimeError（带 reason）。
    """
    import os
    env = dict(os.environ)
    r = _res()
    env["LOADN_CDP_URL"] = env["WORKDADDY_CDP_URL"] = r.cdp_url           # 配置单一真源（沙箱 Chrome）
    env["LOADN_CDP_TOKEN"] = env["WORKDADDY_CDP_TOKEN"] = _sec("sandbox_api_key")  # /cdp 需 Bearer；9222 留空
    env["LOADN_WEBUI_HOME"] = env["WORKDADDY_HOME"] = str(PATHS["root"])
    proc = await asyncio.create_subprocess_exec(
        str(Path(__file__).resolve().parent.parent.parent / "scripts" / "fetch_page.py"),
        url, "--wait", str(wait), "--html-out", str(html_out),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=wait + 90.0)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"CDP 渲染超时（>{wait + 90:.0f}s）")
    import json as json_mod
    try:
        d = json_mod.loads(out.decode() or "{}")
    except ValueError:
        d = {}
    if proc.returncode != 0 or not d.get("ok"):
        reason = d.get("reason") or f"exit {proc.returncode}"
        raise RuntimeError(f"CDP 渲染失败: {reason}")
    if not html_out.exists() or html_out.stat().st_size < 200:
        raise RuntimeError("CDP 渲染未产出 HTML")
    return str(d.get("title") or "")


async def fetch_page(url: str, *, force: bool = False, wait: float = 5.0,
                     proxy: bool = False, ocr: str = "auto",
                     max_chars: int = 60000) -> dict:
    """抓网页正文：直接 GET + 主内容提取，不足/被反爬时 CDP 真浏览器兜底。

    ocr=auto（默认）对主内容区图片做启发式筛选后走 RapidOCR（秒级/张，限
    max_images 张 + 预算止损）；ocr=vlm 走 VLM（扫描件/表格，慢）；off 关闭。
    结果 markdown 落 var/pages_cache/<sha1(url)[:16]>.md（与 bin/fetch_page.py
    同 key 规则，>500B 命中即返回 cached）。返回
    {ok,url,title,text,chars,method,cached,path,images}。
    """
    import hashlib

    from .config import PATHS
    cache_dir = PATHS["pages_cache"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.md"
    if not force and path.exists() and path.stat().st_size > 500:
        text = path.read_text(encoding="utf-8")
        return {"ok": True, "url": url, "title": "", "text": text,
                "chars": len(text), "method": "cache", "cached": True,
                "path": str(path), "images": []}

    method, html, err = "direct", "", ""
    try:  # 阶段①：直接 GET
        resp = await _get(url, headers=_FETCH_HEADERS, timeout=20.0,
                          follow_redirects=True,
                          proxy=_res().proxy if proxy else None)
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code != 200:
            err = f"HTTP {resp.status_code}"
        elif "html" not in ctype and "xml" not in ctype and ctype:
            err = f"非 HTML 内容: {ctype[:60]}"
        else:
            html = resp.text[:3_000_000]
            low = html[:4000].lower()
            if any(m in low for m in _CHALLENGE_MARKS):
                err, html = "反爬挑战页", ""
    except Exception as e:  # noqa: BLE001 - 网络层任何异常都转 CDP
        err = f"{type(e).__name__}: {e}"[:120] if not str(e) else str(e)[:120]

    text, title = "", ""
    if html:
        text, title = _extract_main(html, url)
        if len(text) < 400:
            err, text, method = f"正文过短（{len(text)} 字符）", "", "direct"

    if not text:  # 阶段②：CDP 真浏览器
        html_path = cache_dir / f"{path.stem}.render.html"
        try:
            cdp_title = await _fetch_via_cdp(url, wait=wait, html_out=html_path)
            rendered = html_path.read_text(encoding="utf-8")[:3_000_000]
            text, title = _extract_main(rendered, url)
            title = cdp_title or title
            method = "cdp"
            html = rendered
            if len(text) < 400:
                raise RuntimeError(f"CDP 渲染后正文仍过短（{len(text)} 字符）")
        except Exception as e:
            cdp_err = str(e) or type(e).__name__
            raise RuntimeError(
                f"抓取失败: {url}（直接: {err or 'ok'}；CDP: {cdp_err[:120]}）") from e
        finally:
            html_path.unlink(missing_ok=True)

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n…（已截断，原文 {len(text)} 字符）"

    # 关键图片 OCR：主内容区图筛选后过本机 OCR 服务，结果追加为独立一节。
    # 任何失败都不影响正文（图挂了/服务挂了 → 静默跳过，计入 skipped）。
    images: list[dict] = []
    if ocr != "off" and html:
        try:
            images, skipped = await _ocr_page_images(
                html, url, mode=ocr, proxy=proxy,
                budget_s=240.0 if ocr == "vlm" else 60.0)
            if images:
                parts = [f"\n\n---\n\n## 关键图片内容（OCR ×{len(images)}"
                         f"{'，另有 ' + str(skipped) + ' 张未处理' if skipped else ''}）\n"]
                for i, im in enumerate(images, 1):
                    parts.append(f"### 图{i}\n\n{im['text']}\n\n"
                                 f"（来源: {im['src']}）\n")
                text += "".join(parts)
        except Exception:  # noqa: BLE001 - OCR 阶段整体失败不影响正文
            pass

    from datetime import datetime
    head = (f"# {title or url}\n\nSource: {url}\nMethod: {method} · "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
    path.write_text(head + text, encoding="utf-8")
    return {"ok": True, "url": url, "title": title, "text": text,
            "chars": len(text), "method": method, "cached": False,
            "path": str(path), "images": images}


# ---------------------------------------------------------------- 连通性
async def _ping_one(name: str) -> dict:
    """单个资源探测；任何异常都收敛为 {"ok":False,"msg":...}。"""
    t0 = time.monotonic()
    r = _res()
    try:
        if name == "ocr":
            resp = await _get(f"{r.ocr_url.rstrip('/')}/health", timeout=8.0)
            ok = resp.status_code == 200
            msg = "ok" if ok else f"HTTP {resp.status_code}"
        elif name == "sandbox":
            if not _sec("sandbox_api_key"):
                return {"ok": False, "msg": "未配置 sandbox_api_key"}
            resp = await _get(f"{r.sandbox_url.rstrip('/')}/health",
                              headers={"Authorization": f"Bearer {_sec('sandbox_api_key')}"},
                              timeout=8.0)
            ok = resp.status_code == 200
            msg = "ok" if ok else f"HTTP {resp.status_code}"
        elif name == "sandbox_mcp":
            if not _sec("sandbox_api_key"):
                return {"ok": False, "msg": "未配置 sandbox_api_key"}
            resp = await _post(
                f"{r.sandbox_url.rstrip('/')}/mcp",
                headers={"Authorization": f"Bearer {_sec('sandbox_api_key')}",
                         "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "loadn-ping",
                                                "version": "0"}}},
                timeout=10.0)
            ok = resp.status_code == 200
            msg = "ok" if ok else f"HTTP {resp.status_code}"
        elif name == "proxy":
            if not r.proxy:
                return {"ok": False, "msg": "未配置"}
            resp = await _get("https://www.google.com/generate_204",
                              proxy=r.proxy, timeout=10.0)
            ok = resp.status_code == 204
            msg = "ok" if ok else f"HTTP {resp.status_code}"
        elif name == "sms":
            if not _sec("sms_token"):
                return {"ok": False, "msg": "未配置 sms_token"}
            d = await sms_recent("", n=1)
            msg = f"ok（库存 {d.get('count', '?')} 条）"
            ok = True
        elif name == "mail":
            if not _sec("mail_auth_code"):
                return {"ok": False, "msg": "未配置 mail_auth_code"}

            def _cnt(mb: dict) -> tuple[str, int]:
                m = _imap_open(mb)
                try:
                    _, data = m.search(None, "ALL")
                    return mb["user"], len((data[0] or b"").split())
                finally:
                    try:
                        m.logout()
                    except Exception:  # noqa: BLE001
                        pass
            try:
                pairs = await asyncio.to_thread(
                    lambda: [_cnt(dict(b)) for b in _all_mailboxes()])
                msg = "ok（" + "；".join(f"{u} {c} 封" for u, c in pairs) + "）"
                ok = True
            except Exception as e:  # noqa: BLE001 - 任一邮箱失败即报
                return {"ok": False, "msg": str(e)[:120]}
        elif name == "vlm":
            # 只查配置，不花钱；真实链路用 `wd r vlm` 验证
            _, key, model = vlm_endpoint()
            ok = bool(key)
            msg = f"已配置（{model}）" if ok else "未配置 key"
        elif name == "twocaptcha":
            if not _sec("twocaptcha_key"):
                return {"ok": False, "msg": "未配置"}
            resp = await _get(f"{TWOCAPTCHA_API}/res.php",
                              params={"key": _sec("twocaptcha_key"), "action": "getbalance"},
                              timeout=15.0)
            body = resp.text.strip()
            ok = not body.startswith("ERROR")
            msg = f"余额 ${body}" if ok else body
        elif name == "bocha":
            if not _sec("bocha_key"):
                return {"ok": False, "msg": "未配置"}
            await bocha_search("connectivity", n=1)
            msg, ok = "ok", True
        elif name == "zhipu":
            if not _sec("zhipu_key"):
                return {"ok": False, "msg": "未配置"}
            hits = await zhipu_search("connectivity", n=1, engine="search_std")
            msg, ok = f"ok（{r.zhipu_engine}，探测 std {len(hits)} 条）", True
        elif name == "adb":
            import subprocess
            addr = r.adb_addr
            if not addr:
                return {"ok": False, "msg": "未配置"}
            out = subprocess.run(["adb", "-s", addr, "get-state"],
                                 capture_output=True, text=True, timeout=8)
            ok = out.returncode == 0 and out.stdout.strip() == "device"
            msg = "ok" if ok else (out.stdout.strip() or out.stderr.strip()
                                   or "未连接")[:80]
        elif name == "notify":
            from . import notify as notify_mod
            return await notify_mod.ping()
        else:
            return {"ok": False, "msg": f"未知资源 {name}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": str(e)[:120]}
    return {"ok": ok, "msg": msg, "ms": int((time.monotonic() - t0) * 1000)}


PING_SERVICES = ["ocr", "sandbox", "sandbox_mcp", "proxy", "sms", "mail", "vlm",
                 "twocaptcha", "bocha", "zhipu", "adb", "notify"]


async def ping_all(only: list[str] | None = None) -> dict[str, dict]:
    names = [n for n in (only or PING_SERVICES) if n in PING_SERVICES]
    results = await asyncio.gather(*(_ping_one(n) for n in names))
    out = {n: r for n, r in zip(names, results)}
    for n in (only or []):          # 请求了但不在名单里的，明确报出来
        if n not in PING_SERVICES:
            out[n] = {"ok": False, "msg": f"未知资源 {n}"}
    return out
