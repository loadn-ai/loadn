"""Kimi 联网搜索 Basic/Pro/Fetch 通道对赌（零网络，monkeypatch _post/_sec）。

K1 Basic 归一化+include_content 门
K2 limit 收敛 ≤20
K3 Pro 请求面（sites≤5/time_window/timeout_seconds）+ chunks/authority 附加键
K4 错误面如实抛
K5 无 key 指引
K6 种子卡+锚键
K7 ping 两态
K8 CLI 参数面（--via kimi_pro/--sites/--tw 进 help）
"""
import pytest


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


@pytest.fixture()
def km(monkeypatch):
    from loadn_webui.integrations import resources as R
    calls: list[dict] = []

    async def fake_post(url, *, headers=None, json=None, **kw):
        calls.append({"url": url, "headers": headers, "json": json})
        return _Resp(200, {"search_results": [
            {"title": "诺贝尔物理学奖 2025", "url": "https://www.nobelprize.org/x",
             "snippet": "摘要行", "site_name": "NobelPrize.org", "date": "2025-10-07",
             "content": "整页正文", "authority": 4,
             "chunks": [{"text": "与查询最相关的正文片段", "score": 1.2432842}]},
        ]})

    monkeypatch.setattr(R, "_sec", lambda n: "sk-k" if n == "svc:kimi_search" else "")
    monkeypatch.setattr(R, "_post", fake_post)
    return R, calls


@pytest.mark.asyncio
async def test_kimi_basic_normalized(km):
    R, calls = km
    hits = await R.kimi_search("2025 诺贝尔物理学奖", n=5)
    h = hits[0]
    assert h["name"] == "诺贝尔物理学奖 2025"
    assert h["siteName"] == "NobelPrize.org" and h["date"] == "2025-10-07"
    assert "content" not in h and "chunks" not in h      # Basic 默认不带正文/片段
    c = calls[0]
    assert c["url"].endswith("/v1/tools/search")
    assert c["headers"]["Authorization"] == "Bearer sk-k"
    assert c["json"]["text_query"] and c["json"]["limit"] == 5
    assert c["json"]["include_content"] is False


@pytest.mark.asyncio
async def test_kimi_basic_content_and_limit_cap(km):
    R, calls = km
    hits = await R.kimi_search("q", n=99, include_content=True)
    assert hits[0]["content"] == "整页正文"
    assert calls[0]["json"]["limit"] == 20               # 官方上限 20


@pytest.mark.asyncio
async def test_kimi_pro_request_and_extras(km):
    R, calls = km
    hits = await R.kimi_search_pro(
        "2025 诺贝尔物理学奖 获奖成果 实验验证", n=5,
        sites=["nobelprize.org", "a.com", "b.com", "c.com", "d.com", "e.com",
               "f.com"],
        time_start="2025-10", time_end="2025-12")
    h = hits[0]
    assert h["authority"] == 4
    assert h["chunks"][0]["text"] == "与查询最相关的正文片段"
    assert abs(h["chunks"][0]["score"] - 1.2432842) < 1e-9
    c = calls[0]
    assert c["url"].endswith("/v1/tools/search_pro")
    assert c["json"]["sites"] == ["nobelprize.org", "a.com", "b.com", "c.com",
                                  "d.com"]               # ≤5 截断（e/f 被截）
    assert c["json"]["time_window"] == {"start": "2025-10", "end": "2025-12"}
    assert c["json"]["timeout_seconds"] == 30


@pytest.mark.asyncio
async def test_kimi_error_raises(km, monkeypatch):
    R, _ = km

    async def err_post(url, **kw):
        return _Resp(429, {"error": {"message": "Rate limited"}})

    monkeypatch.setattr(R, "_post", err_post)
    with pytest.raises(RuntimeError, match="429"):
        await R.kimi_search_pro("q")


@pytest.mark.asyncio
async def test_kimi_no_key(km, monkeypatch):
    R, _ = km
    monkeypatch.setattr(R, "_sec", lambda n: "")
    with pytest.raises(RuntimeError, match="svc:kimi_search"):
        await R.kimi_search("q")


def test_kimi_seed_card_and_anchor():
    from loadn_webui.config import ANCHOR_SERVICE_KEYS, CONFIG, PRESET_SERVICES
    card = next(s for s in PRESET_SERVICES if s["name"] == "kimi_search")
    assert card["secrets"][0]["vault_ref"] == "svc:kimi_search"
    assert card["config"][0]["key"] == "kimi_search_base"
    assert "kimi_search_base" in ANCHOR_SERVICE_KEYS
    assert CONFIG.resources.kimi_search_base == "https://api.moonshot.cn"


def test_kimi_ping_branch(km, monkeypatch):
    import asyncio
    R2, _ = km
    monkeypatch.setattr(R2, "_sec", lambda n: "")
    out = asyncio.run(R2._ping_one("kimi_search"))
    assert out["ok"] is False and out["msg"] == "未配置"

    async def probe(q, *, n=1, **kw):
        return [{"name": "x"}]

    monkeypatch.setattr(R2, "kimi_search", probe)
    monkeypatch.setattr(R2, "_sec", lambda n: "sk-k" if n == "svc:kimi_search" else "")
    out = asyncio.run(R2._ping_one("kimi_search"))
    assert out["ok"] is True and "1 条" in out["msg"]


def test_kimi_cli_flags():
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-m", "loadn_webui.cli", "r", "search", "--help"],
        capture_output=True, text=True, timeout=60)
    for token in ("kimi_pro", "--sites", "--tw", "tavily"):
        assert token in r.stdout, token


# ---------------------------------------------------------------- Tavily / ScraperAPI

@pytest.fixture()
def tv(monkeypatch):
    from loadn_webui.integrations import resources as R
    calls: list[dict] = []

    async def fake_post(url, *, headers=None, json=None, **kw):
        calls.append({"url": url, "headers": headers, "json": json})
        return _Resp(200, {"results": [
            {"title": "Tavily doc", "url": "https://docs.tavily.com/x",
             "content": "正文片段", "score": 0.94, "published_date": "2026-01-02"},
        ]})

    monkeypatch.setattr(R, "_sec", lambda n: "tvly-k" if n == "svc:tavily" else "")
    monkeypatch.setattr(R, "_post", fake_post)
    return R, calls


@pytest.mark.asyncio
async def test_tavily_search_normalized(tv):
    R, calls = tv
    hits = await R.tavily_search("agent framework", n=5)
    h = hits[0]
    assert h["name"] == "Tavily doc" and h["url"].endswith("/x")
    assert h["snippet"] == "正文片段"
    assert h["rerank_score"] == 0.94 and h["date"] == "2026-01-02"
    c = calls[0]
    assert c["url"].endswith("/search")
    assert c["headers"]["Authorization"] == "Bearer tvly-k"
    assert c["json"]["search_depth"] == "advanced" and c["json"]["max_results"] == 5


@pytest.mark.asyncio
async def test_tavily_basic_depth_and_no_key(tv, monkeypatch):
    R, calls = tv
    await R.tavily_search("q", advanced=False)
    assert calls[0]["json"]["search_depth"] == "basic"
    monkeypatch.setattr(R, "_sec", lambda n: "")
    with pytest.raises(RuntimeError, match="svc:tavily"):
        await R.tavily_search("q")


@pytest.mark.asyncio
async def test_scraperapi_fetch(monkeypatch):
    from loadn_webui.integrations import resources as R
    calls: list[dict] = []

    class _R2:
        status_code = 200
        text = "<html><body>hi</body></html>"

    async def fake_get(url, *, params=None, **kw):
        calls.append({"url": url, "params": params})
        return _R2()

    monkeypatch.setattr(R, "_sec", lambda n: "sa-k" if n == "svc:scraperapi" else "")
    monkeypatch.setattr(R, "_get", fake_get)
    html = await R.scraperapi_fetch("https://hard-anti-bot.example.com/page")
    assert "<body>hi</body>" in html
    assert calls[0]["url"].startswith("https://api.scraperapi.com/")
    assert calls[0]["params"]["api_key"] == "sa-k"
    assert calls[0]["params"]["url"].endswith("/page")


def test_tavily_scraperapi_seed_cards():
    from loadn_webui.config import ANCHOR_SERVICE_KEYS, CONFIG, PRESET_SERVICES
    names = {s["name"] for s in PRESET_SERVICES}
    assert {"tavily", "scraperapi"} <= names
    assert "tavily_base" in ANCHOR_SERVICE_KEYS
    assert "scraperapi_base" in ANCHOR_SERVICE_KEYS
    assert CONFIG.resources.tavily_base == "https://api.tavily.com"
    assert CONFIG.resources.scraperapi_base == "https://api.scraperapi.com"
