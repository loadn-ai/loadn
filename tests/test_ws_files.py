"""工作区文件管理写面对赌：PUT 编辑/POST 新建/DELETE 删除/PATCH 重命名。

用户需求：临时调整工作区内容不再只能靠 prompt。安全面：路径穿越 403、
工作区根保护、目录非空拒删、文本大小上限、树缓存写后失效。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
async def ws(client):
    """建临时会话，返回 (sid, client)。"""
    r = await client.post("/api/sessions", json={"title": "ws-files-e2e",
                                                 "profile": "assistant"})
    assert r.status_code == 200, r.text
    yield r.json()["session"]["id"]


async def test_put_and_readback(ws, client):
    sid = ws
    r = await client.put(f"/api/sessions/{sid}/file",
                         json={"path": "notes/计划.md", "content": "# 计划\n中文内容 ✓"})
    assert r.status_code == 200 and r.json()["ok"]
    # 读面回读一致（预览端点）
    r2 = await client.get(f"/api/sessions/{sid}/file", params={"path": "notes/计划.md"})
    assert r2.status_code == 200
    assert "中文内容 ✓" in r2.text
    # 树缓存写后立即可见（不等 TTL）
    t = await client.get(f"/api/sessions/{sid}/tree")
    assert any(n["path"] == "notes/计划.md" for n in t.json()["tree"])


async def test_put_guards(ws, client):
    sid = ws
    for bad in ("../../etc/pwned", "/abs/path", ".", "..", ""):
        r = await client.put(f"/api/sessions/{sid}/file",
                             json={"path": bad, "content": "x"})
        assert r.status_code in (400, 403), f"{bad!r} 应被拒，实得 {r.status_code}"
    # 大小上限（>2MB 文本）
    r = await client.put(f"/api/sessions/{sid}/file",
                         json={"path": "big.txt", "content": "x" * (2 * 1024 * 1024 + 1)})
    assert r.status_code == 413


async def test_create_and_conflict(ws, client):
    sid = ws
    r = await client.post(f"/api/sessions/{sid}/file", json={"path": "a.md"})
    assert r.status_code == 200 and r.json()["kind"] == "file"
    r = await client.post(f"/api/sessions/{sid}/file", json={"path": "a.md"})
    assert r.status_code == 409
    r = await client.post(f"/api/sessions/{sid}/file",
                          json={"path": "sub/dir", "kind": "dir"})
    assert r.status_code == 200
    r = await client.post(f"/api/sessions/{sid}/file",
                          json={"path": "../escape", "kind": "dir"})
    assert r.status_code in (400, 403)


async def test_delete_semantics(ws, client):
    sid = ws
    await client.post(f"/api/sessions/{sid}/file", json={"path": "d.md"})
    await client.post(f"/api/sessions/{sid}/file", json={"path": "d/inner.md"})
    # 非空目录拒删（防一键抹掉整棵子树）
    r = await client.delete(f"/api/sessions/{sid}/file", params={"path": "d"})
    assert r.status_code == 400
    # 内文件可删；删后空目录可删；再删 404
    assert (await client.delete(f"/api/sessions/{sid}/file",
                                params={"path": "d/inner.md"})).status_code == 200
    assert (await client.delete(f"/api/sessions/{sid}/file",
                                params={"path": "d"})).status_code == 200
    assert (await client.delete(f"/api/sessions/{sid}/file",
                                params={"path": "d"})).status_code == 404
    # 根保护：path 落到工作区根本身 → 拒
    r = await client.delete(f"/api/sessions/{sid}/file", params={"path": "."})
    assert r.status_code in (400, 403, 404)


async def test_rename_and_move(ws, client):
    sid = ws
    await client.put(f"/api/sessions/{sid}/file",
                     json={"path": "old.md", "content": "v1"})
    r = await client.patch(f"/api/sessions/{sid}/file",
                           json={"path": "old.md", "new_path": "new-name.md"})
    assert r.status_code == 200
    # 旧路径读不回（读面 not_found 以 200+{"error"} 表达——读端点契约）
    assert "error" in (await client.get(f"/api/sessions/{sid}/file",
                                        params={"path": "old.md"})).json()
    # 移动进子目录（父目录自动建）
    r = await client.patch(f"/api/sessions/{sid}/file",
                           json={"path": "new-name.md", "new_path": "deep/dir/x.md"})
    assert r.status_code == 200
    # 目标已存在 → 409；穿越目标 → 403；源缺失 → 404
    await client.put(f"/api/sessions/{sid}/file", json={"path": "a.md", "content": "a"})
    await client.put(f"/api/sessions/{sid}/file", json={"path": "b.md", "content": "b"})
    assert (await client.patch(f"/api/sessions/{sid}/file",
                               json={"path": "a.md", "new_path": "b.md"})).status_code == 409
    assert (await client.patch(f"/api/sessions/{sid}/file",
                               json={"path": "a.md", "new_path": "../x"})).status_code == 403
    assert (await client.patch(f"/api/sessions/{sid}/file",
                               json={"path": "nope.md", "new_path": "y.md"})).status_code == 404
