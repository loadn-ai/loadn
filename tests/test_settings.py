"""平台设置 + 自动标题（零 token：monkeypatch 掉外部模型调用）。

设置写入落在 LOADN_WEBUI_HOME（conftest 已切临时目录），不碰仓库 config.yaml；
CONFIG.titlegen 内存态在 fixture 里快照/恢复，不外溢到其他模块。
"""
import asyncio
import time
from dataclasses import asdict

import pytest
import yaml


@pytest.fixture(autouse=True)
def restore_titlegen_cfg():
    from loadn_webui.config import CONFIG
    snap = asdict(CONFIG.titlegen)
    yield
    for k, v in snap.items():
        setattr(CONFIG.titlegen, k, v)


@pytest.fixture()
def fake_model(monkeypatch):
    """把外部模型调用换成确定返回，并计数。"""
    from loadn_webui.integrations import titlegen
    calls = []

    async def fake(text: str) -> str | None:
        calls.append(text)
        return "自动标题测试"

    monkeypatch.setattr(titlegen, "generate_title", fake)
    return calls


def _enable_titlegen(monkeypatch):
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.titlegen, "enabled", True)
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "test-key")


async def _wait_title(client, sid, want, timeout_s=5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        r = await client.get(f"/api/sessions/{sid}")
        if r.json()["title"] == want:
            return True
        await asyncio.sleep(0.1)
    return False


# ---------------------------------------------------------------- 设置 API
async def test_settings_roundtrip(client, monkeypatch):
    # 默认值
    r = await client.get("/api/settings")
    assert r.status_code == 200
    d = r.json()
    assert d["titlegen"]["model"] == "doubao-seed-2-0-mini-260428"
    assert d["titlegen"]["api_key_set"] is False

    # 保存（key 只存不回传明文）
    r = await client.put("/api/settings/titlegen", json={
        "enabled": True, "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-mini-test", "api_key": "sk-secret-abcde"})
    assert r.status_code == 200, r.text
    d = r.json()["titlegen"]
    assert d["api_key_set"] is True and "sk-secret" not in str(d)
    assert d["api_key_hint"].endswith("abcde")

    # 落盘到临时 HOME 的 config.yaml；重启语义（load_config）能读回
    from loadn_webui.config import PATHS
    data = yaml.safe_load((PATHS["root"] / "config.yaml").read_text())
    assert data["titlegen"]["model"] == "doubao-mini-test"
    assert data["titlegen"]["api_key"] == "sk-secret-abcde"

    # key 留空 = 保持不变
    r = await client.put("/api/settings/titlegen", json={"model": "doubao-mini-2"})
    d = r.json()["titlegen"]
    assert d["model"] == "doubao-mini-2" and d["api_key_set"] is True

    # 非法值
    r = await client.put("/api/settings/titlegen", json={"api_base": "not-a-url"})
    assert r.status_code == 400
    r = await client.put("/api/settings/run", json={"max_concurrent_turns": 99})
    assert r.status_code == 400


# ---------------------------------------------------------------- 自动标题
async def test_auto_title_first_message(client, monkeypatch, fake_model, ws_root):
    _enable_titlegen(monkeypatch)
    # 不传 title + 首条消息 → 自动起名
    r = await client.post("/api/sessions", json={"first_message": "帮我调研固态电池产业格局"})
    sid = r.json()["session"]["id"]
    assert await _wait_title(client, sid, "自动标题测试"), "标题未自动生成"
    assert fake_model and "固态电池" in fake_model[0]
    # 只生成一次：再发消息不重复改名
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "继续"})
    await asyncio.sleep(0.8)
    r = await client.get(f"/api/sessions/{sid}")
    assert r.json()["title"] == "自动标题测试"
    assert len(fake_model) == 1


async def test_auto_title_manual_wins(client, monkeypatch, fake_model, ws_root):
    _enable_titlegen(monkeypatch)
    # 显式命名 → 不自动
    r = await client.post("/api/sessions", json={"title": "我的任务", "first_message": "开始"})
    sid = r.json()["session"]["id"]
    await asyncio.sleep(0.5)
    r = await client.get(f"/api/sessions/{sid}")
    assert r.json()["title"] == "我的任务"
    assert len(fake_model) == 0

    # 未命名会话：先建（不发消息）→ 手动改名 → 再发消息也不自动
    r = await client.post("/api/sessions", json={})
    sid2 = r.json()["session"]["id"]
    r = await client.patch(f"/api/sessions/{sid2}", json={"title": "手动名"})
    assert r.status_code == 200
    await client.post(f"/api/sessions/{sid2}/messages", json={"text": "现在开始"})
    await asyncio.sleep(0.6)
    r = await client.get(f"/api/sessions/{sid2}")
    assert r.json()["title"] == "手动名"
    assert len(fake_model) == 0            # 标记已被手动改名清除


async def test_auto_title_disabled(client, monkeypatch, fake_model, ws_root):
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.titlegen, "enabled", False)
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "k")
    r = await client.post("/api/sessions", json={"first_message": "随便"})
    sid = r.json()["session"]["id"]
    await asyncio.sleep(0.5)
    r = await client.get(f"/api/sessions/{sid}")
    assert r.json()["title"].startswith("随便")   # 消息前缀兜底，未调模型
    assert len(fake_model) == 0
