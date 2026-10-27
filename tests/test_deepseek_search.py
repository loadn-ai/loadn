"""DeepSeek Search/Fetch 资源通道对赌 + upgrade 语义版本排序对赌。

零网络：monkeypatch resources._post/_sec 覆盖归一化/鉴权/错误面；
排序对赌直接调 ops._ver_key/_list_releases（tmp releases 目录）。
"""
import pytest


@pytest.fixture()
def ds(monkeypatch):
    """DeepSeek 通道环境：key 在场 + _post 打桩。"""
    from loadn_webui.integrations import resources as R
    calls: list[dict] = []

    async def fake_post(url, *, headers=None, json=None, **kw):
        calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResp(200, {"results": [
            {"title": "DeepSeek V4 发布", "url": "https://example.com/v4",
             "snippet": "短摘要", "summary": "长摘要给模型读",
             "site_name": "Example", "date": "2026-06-01T00:00:00+00:00",
             "rerank_score": 2.4, "content": "正文"},
        ]})

    class _FakeResp:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    monkeypatch.setattr(R, "_sec", lambda n: "sk-test" if n == "svc:deepseek_search" else "")
    monkeypatch.setattr(R, "_post", fake_post)
    return R, calls


@pytest.mark.asyncio
async def test_deepseek_search_normalized(ds):
    R, calls = ds
    hits = await R.deepseek_search("DeepSeek V4", n=5)
    assert len(hits) == 1
    h = hits[0]
    # 同构主键
    assert h["name"] == "DeepSeek V4 发布"
    assert h["url"] == "https://example.com/v4"
    assert h["snippet"] == "短摘要"
    assert h["siteName"] == "Example"
    assert h["date"].startswith("2026-06-01")
    # DeepSeek 特色附加键
    assert h["summary"] == "长摘要给模型读"
    assert h["rerank_score"] == 2.4
    assert "content" not in h                 # need_content 关=不带正文
    # 请求面：端点/Bearer/count 收敛
    c = calls[0]
    assert c["url"].endswith("/v1/tools/search")
    assert c["headers"]["Authorization"] == "Bearer sk-test"
    assert c["json"]["count"] == 5 and c["json"]["need_content"] is False


@pytest.mark.asyncio
async def test_deepseek_search_need_content_and_n_cap(ds):
    R, calls = ds
    await R.deepseek_search("q", n=99, need_content=True)
    assert calls[0]["json"]["count"] == 50     # count≤50 上限
    assert calls[0]["json"]["need_content"] is True


@pytest.mark.asyncio
async def test_deepseek_search_error_raises(ds, monkeypatch):
    R, _ = ds

    class _Err:
        status_code = 402

        def json(self):
            return {"error": {"message": "Insufficient Balance"}}

    async def err_post(url, **kw):
        return _Err()

    monkeypatch.setattr(R, "_post", err_post)
    with pytest.raises(RuntimeError, match="402"):
        await R.deepseek_search("q")


@pytest.mark.asyncio
async def test_deepseek_search_no_key(ds, monkeypatch):
    R, _ = ds
    monkeypatch.setattr(R, "_sec", lambda n: "")
    with pytest.raises(RuntimeError, match="svc:deepseek_search"):
        await R.deepseek_search("q")


@pytest.mark.asyncio
async def test_deepseek_fetch(ds):
    R, calls = ds

    async def fetch_post(url, *, headers=None, json=None, **kw):
        calls.append({"url": url})
        return _F2(200, {"title": "T", "content": "# MD"})

    class _F2:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(R, "_post", fetch_post)
    try:
        page = await R.deepseek_fetch("https://example.com/a")
        assert page == {"title": "T", "content": "# MD"}
        assert calls[0]["url"].endswith("/v1/tools/fetch")
    finally:
        monkeypatch.undo()


def test_seed_card_and_anchor():
    """种子卡在册 + 锚键投影字段存在。"""
    from loadn_webui.config import ANCHOR_SERVICE_KEYS, CONFIG, PRESET_SERVICES
    card = next(s for s in PRESET_SERVICES if s["name"] == "deepseek_search")
    assert card["secrets"][0]["vault_ref"] == "svc:deepseek_search"
    assert card["config"][0]["key"] == "deepseek_search_base"
    assert "deepseek_search_base" in ANCHOR_SERVICE_KEYS
    assert CONFIG.resources.deepseek_search_base == "https://api.deepseek.com"


def test_ping_deepseek_branch(ds, monkeypatch):
    """ping 专支：key 缺=未配置；在=真调一单探测。"""
    import asyncio

    R2, _ = ds
    monkeypatch.setattr(R2, "_sec", lambda n: "")
    out = asyncio.run(R2._ping_one("deepseek_search"))
    assert out["ok"] is False and out["msg"] == "未配置"

    async def probe(q, *, n=1, **kw):
        return [{"name": "x"}]

    monkeypatch.setattr(R2, "deepseek_search", probe)
    monkeypatch.setattr(R2, "_sec", lambda n: "sk-test" if n == "svc:deepseek_search" else "")
    out = asyncio.run(R2._ping_one("deepseek_search"))
    assert out["ok"] is True and "1 条" in out["msg"]


def test_cli_search_via_choices():
    """r search 子命令面：--via deepseek / --content 进 help。"""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "loadn_webui.cli", "r", "search", "--help"],
                       capture_output=True, text=True, timeout=60)
    assert "deepseek" in r.stdout and "--content" in r.stdout


def test_ops_semver_sort():
    """upgrade 版本排序修复：v0.7.53 > v0.7.9（字典序陷阱回归对赌）。"""
    from loadn_webui.ops import _ver_key
    assert _ver_key("v0.7.53") > _ver_key("v0.7.9")
    assert _ver_key("v0.8.0") > _ver_key("v0.7.53")
    assert _ver_key("v0.7.10") > _ver_key("v0.7.9")
