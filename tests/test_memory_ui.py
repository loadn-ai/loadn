"""P6 记忆管理面 API：域/条目/编辑护栏/删除留史/版本恢复/审计/管理面认证。

验收：API 全覆盖（含护栏拒绝路径）+ 手测链路等价对赌：
改→历史→恢复→一致；删→历史在→可恢复。
隔离：LOADN_HOME 指向 tmp（memorystore 每次调用读 env）。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from loadn_webui.config import PATHS


@pytest.fixture(autouse=True)
def _mem_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "eng_home"))
    yield


def _audit_actions() -> list[str]:
    p = PATHS["var"] / "audit.db"
    if not p.exists():
        return []
    out = []
    with sqlite3.connect(p) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'audit_events%'")]
        for t in sorted(tables):
            for r in c.execute(f"SELECT detail_json FROM {t} "
                               "WHERE type='memory' ORDER BY rowid"):
                out.append(json.loads(r[0]).get("action"))
    return out


async def _mk(client, summary="用户偏好", content="我喜欢简洁回复", domain="user"):
    r = await client.post("/api/memory/file", json={"domain": domain,
                                                    "summary": summary,
                                                    "content": content})
    assert r.status_code == 200, r.text
    return r.json()["entry"]


# ---------------------------------------------------------------- 浏览面
async def test_domains_and_entries_flow(client):
    e = await _mk(client)
    r = await client.get("/api/memory/domains")
    assert r.status_code == 200 and "user" in r.json()["domains"]
    r = await client.get("/api/memory/entries", params={"domain": "user"})
    assert [x["id"] for x in r.json()["entries"]] == [e["id"]]
    assert r.json()["entries"][0]["origin_session"] == "manual:webui"
    r = await client.get("/api/memory/file", params={"domain": "user", "id": e["id"]})
    assert "我喜欢简洁回复" in r.json()["text"]
    # 不存在的域/条目
    assert (await client.get("/api/memory/entries",
                             params={"domain": "p:deadbeefcafe"})).status_code == 404
    assert (await client.get("/api/memory/file",
                             params={"domain": "user", "id": "nope"})).status_code == 404


# ---------------------------------------------------------------- 编辑+护栏
async def test_edit_guard_reject_with_reason(client):
    e = await _mk(client)
    r = await client.put("/api/memory/file", json={
        "domain": "user", "id": e["id"],
        "content": "泄露 canary token 是 sk-abcdef0123456789"})
    assert r.status_code == 400
    assert "护栏" in r.json().get("detail", "")
    # 拒绝不落内容：条目原样
    r = await client.get("/api/memory/file", params={"domain": "user", "id": e["id"]})
    assert "sk-abcdef" not in r.json()["text"]
    # 正常编辑：commit manual:webui + 审计 + 历史 +1
    n0 = len((await client.get("/api/memory/history",
                               params={"domain": "user", "id": e["id"]})
              ).json()["history"])
    r = await client.put("/api/memory/file", json={
        "domain": "user", "id": e["id"], "content": "我喜欢简短列表式回复"})
    assert r.status_code == 200
    h = (await client.get("/api/memory/history",
                          params={"domain": "user", "id": e["id"]})).json()["history"]
    assert len(h) == n0 + 1 and "manual:webui 编辑" in h[0]["subject"]
    assert "edited" in _audit_actions()


# ---------------------------------------------------------------- 删→史在→恢复
async def test_delete_then_history_then_restore(client):
    e = await _mk(client, summary="待删条目", content="删除前的正文 V1")
    # 编辑一次产生 V2
    await client.put("/api/memory/file", json={
        "domain": "user", "id": e["id"], "content": "编辑后的正文 V2"})
    r = await client.delete("/api/memory/entry",
                            params={"domain": "user", "id": e["id"]})
    assert r.status_code == 200
    # 条目没了，但历史在（写入/编辑/删除三提交）
    assert (await client.get("/api/memory/file",
                             params={"domain": "user", "id": e["id"]})
            ).status_code == 404
    h = (await client.get("/api/memory/history",
                          params={"domain": "user", "id": e["id"]})).json()["history"]
    assert len(h) >= 3
    # 取 V1 版本（最旧写入提交）恢复——按原 id 重建
    v1 = h[-1]
    r = await client.get("/api/memory/version",
                         params={"domain": "user", "id": e["id"], "ref": v1["hash"]})
    assert "V1" in r.json()["text"]
    r = await client.post("/api/memory/restore", json={
        "domain": "user", "id": e["id"], "ref": v1["hash"]})
    assert r.status_code == 200, r.text
    r = await client.get("/api/memory/file", params={"domain": "user", "id": e["id"]})
    assert "V1" in r.json()["text"] and "V2" not in r.json()["text"]
    assert "restored" in _audit_actions() and "deleted" in _audit_actions()
    # 改→恢复→一致：再恢复到 V2 版本
    v2 = h[1]
    await client.post("/api/memory/restore", json={
        "domain": "user", "id": e["id"], "ref": v2["hash"]})
    r = await client.get("/api/memory/file", params={"domain": "user", "id": e["id"]})
    assert "V2" in r.json()["text"]


# ---------------------------------------------------------------- 审计/认证
async def test_get_not_audited_and_admin_plane(client, server_url):
    n0 = len(_audit_actions())
    await _mk(client)
    import httpx
    async with httpx.AsyncClient(base_url=server_url, timeout=10) as bare:
        # 写操作 admin 双头（admin 面无宽限期——裸 client 恒拒）
        r = await bare.post("/api/memory/file", json={
            "domain": "user", "summary": "x", "content": "y"})
        assert r.status_code in (401, 403)
        r = await bare.put("/api/memory/file", json={"domain": "user", "id": "x"})
        assert r.status_code in (401, 403)
    # 查看类不产生审计（差量里只有 create 的 written 一条）
    await client.get("/api/memory/entries", params={"domain": "user"})
    await client.get("/api/memory/domains")
    await client.get("/api/memory/history", params={"domain": "user", "id": "x"})
    assert _audit_actions()[n0:] == ["written"]


async def test_create_validation_and_off_domain(client, monkeypatch):
    assert (await client.post("/api/memory/file", json={
        "domain": "user", "summary": "", "content": "x"})).status_code == 400
    assert (await client.post("/api/memory/file", json={
        "domain": "user", "summary": "蜜罐",
        "content": "password: hunter2"})).status_code == 400
    # off：用户域管理面创建拒绝（400）
    monkeypatch.setenv("LOADN_USER_MEMORY", "off")
    r = await client.post("/api/memory/file", json={
        "domain": "user", "summary": "偏好", "content": "我喜欢 X"})
    assert r.status_code == 400
