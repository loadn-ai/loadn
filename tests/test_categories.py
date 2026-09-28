"""侧栏分区（置顶/收藏/自定义分类）——任务与项目统一语义。

核心断言：① 分类 CRUD（重名 409 / 空 400 / 404）；② 移动 = 三标记位互斥清位；
③ 纯分区操作不 touch updated_at（不跳「最近」顶）；④ 删分类成员回「最近」；
⑤ 项目同款位；⑥ 子任务移入分区保留 project_id（仅从项目组「抽出」展示）。
"""
from __future__ import annotations

from loadn_webui import db as db_mod


async def _mk_session(client, title="任务") -> str:
    r = await client.post("/api/sessions", json={"title": title})
    assert r.status_code == 200
    return r.json()["session"]["id"]


async def _mk_category(client, name="分类") -> int:
    r = await client.post("/api/categories", json={"name": name})
    assert r.status_code == 200
    return r.json()["category"]["id"]


async def test_category_crud(client):
    cid = await _mk_category(client, "证书")
    assert (await client.post("/api/categories", json={"name": "证书"})).status_code == 409
    assert (await client.post("/api/categories", json={"name": "  "})).status_code == 400
    r = await client.patch(f"/api/categories/{cid}", json={"name": "证书收割"})
    assert r.json()["category"]["name"] == "证书收割"
    # 改成已有名 → 409
    other = await _mk_category(client, "另一类")
    assert (await client.patch(f"/api/categories/{cid}", json={"name": "另一类"})).status_code == 409
    names = [c["name"] for c in (await client.get("/api/categories")).json()["categories"]]
    assert set(names) == {"证书收割", "另一类"}
    assert (await client.delete(f"/api/categories/{cid}")).status_code == 200
    assert (await client.delete(f"/api/categories/{cid}")).status_code == 404
    assert (await client.patch(f"/api/categories/{other}", json={"name": "x"})).status_code == 200


async def test_move_mutual_exclusion_and_no_touch(client):
    """置顶 → 收藏 → 分类逐次移动：落位清另两位；全程不 touch updated_at。"""
    sid = await _mk_session(client)
    with db_mod.conn() as c:
        before = db_mod.get_session(c, sid)["updated_at"]
    cid = await _mk_category(client, "进行中")
    await client.patch(f"/api/sessions/{sid}", json={"pinned": True})
    await client.patch(f"/api/sessions/{sid}", json={"starred": True})
    with db_mod.conn() as c:
        row = db_mod.get_session(c, sid)
        assert row["starred"] == 1 and row["pinned"] == 0
    await client.patch(f"/api/sessions/{sid}", json={"category_id": cid})
    with db_mod.conn() as c:
        row = db_mod.get_session(c, sid)
        assert row["category_id"] == cid and row["starred"] == 0 and row["pinned"] == 0
        assert row["updated_at"] == before      # 纯分区操作不扰动排序键
    assert (await client.patch(f"/api/sessions/{sid}", json={"category_id": 9999})).status_code == 404
    await client.patch(f"/api/sessions/{sid}", json={"category_id": None})
    with db_mod.conn() as c:
        assert db_mod.get_session(c, sid)["category_id"] is None


async def test_delete_category_returns_members(client):
    """删分类：任务与项目成员的 category_id 连坐置空（回「最近」），行本身不动。"""
    cid = await _mk_category(client, "临时")
    sid = await _mk_session(client)
    pid = (await client.post("/api/projects", json={"title": "P"})).json()["project"]["id"]
    await client.patch(f"/api/sessions/{sid}", json={"category_id": cid})
    await client.patch(f"/api/projects/{pid}", json={"category_id": cid})
    assert (await client.delete(f"/api/categories/{cid}")).status_code == 200
    with db_mod.conn() as c:
        assert db_mod.get_session(c, sid)["category_id"] is None
        assert db_mod.get_project(c, pid)["category_id"] is None


async def test_project_partition_flags(client):
    """项目分区位与任务同款：置顶/收藏/分类互斥、纯标记不 touch；改名照常。"""
    pid = (await client.post("/api/projects", json={"title": "P"})).json()["project"]["id"]
    with db_mod.conn() as c:
        before = db_mod.get_project(c, pid)["updated_at"]
    await client.patch(f"/api/projects/{pid}", json={"pinned": True, "starred": False})
    with db_mod.conn() as c:
        row = db_mod.get_project(c, pid)
        assert row["pinned"] == 1 and row["starred"] == 0
        assert row["updated_at"] == before
    r = await client.patch(f"/api/projects/{pid}", json={"title": "P2"})
    assert r.status_code == 200 and r.json()["project"]["title"] == "P2"
    assert (await client.patch("/api/projects/no-such", json={"pinned": True})).status_code == 404


async def test_extracted_subtask_keeps_project(client):
    """子任务移入分区：project_id 保留（工作区共享不动），展示层才「抽出」。"""
    pid = (await client.post("/api/projects", json={"title": "P"})).json()["project"]["id"]
    sid = (await client.post("/api/sessions",
                            json={"project_id": pid, "title": "子"})).json()["session"]["id"]
    await client.patch(f"/api/sessions/{sid}", json={"pinned": True})
    with db_mod.conn() as c:
        row = db_mod.get_session(c, sid)
        assert row["project_id"] == pid and row["pinned"] == 1


async def test_create_in_category(client):
    """分类内直接新建：任务/项目带 category_id 创建即落位；坏 id 404。"""
    cid = await _mk_category(client, "代码")
    sid = (await client.post("/api/sessions",
                             json={"title": "分类内新任务", "category_id": cid})).json()["session"]["id"]
    pid = (await client.post("/api/projects",
                             json={"title": "分类内项目", "category_id": cid})).json()["project"]["id"]
    with db_mod.conn() as c:
        assert db_mod.get_session(c, sid)["category_id"] == cid
        assert db_mod.get_project(c, pid)["category_id"] == cid
    assert (await client.post("/api/sessions",
                              json={"title": "x", "category_id": 9999})).status_code == 404
    assert (await client.post("/api/projects",
                              json={"title": "x", "category_id": 9999})).status_code == 404
