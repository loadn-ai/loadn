"""打包下载（zip）：目录收集过滤 / 穿越防护 / 容量闸 / 子目录与整包。"""
from __future__ import annotations

import io
import zipfile


async def _mk_ws(client, ws_root, title="打包测试"):
    r = await client.post("/api/sessions", json={"title": title})
    sid = r.json()["session"]["id"]
    ws = ws_root / sid
    return sid, ws


async def test_archive_filters_and_naming(client, ws_root):
    """整包：正常文件进包；隐藏/node_modules/chrome*/符号链接不进；
    zip 顶层目录 = 会话 sid（解压不散一地）。"""
    sid, ws = await _mk_ws(client, ws_root)
    (ws / "reports").mkdir(parents=True, exist_ok=True)
    (ws / "reports" / "r1.md").write_text("报告一", encoding="utf-8")
    (ws / "notes.txt").write_text("根文件", encoding="utf-8")
    (ws / ".fake").mkdir(exist_ok=True)
    (ws / ".fake" / "tools").write_text("{}", encoding="utf-8")
    (ws / ".steer.jsonl").write_text('{"ts":1,"text":"x"}\n', encoding="utf-8")
    (ws / "node_modules").mkdir(exist_ok=True)
    (ws / "node_modules" / "big.js").write_text("x" * 100, encoding="utf-8")
    (ws / "chrome-profile").mkdir(exist_ok=True)
    (ws / "chrome-profile" / "Cookies").write_text("secret", encoding="utf-8")

    r = await client.get(f"/api/sessions/{sid}/archive")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    n_files = int(r.headers["X-Archive-Files"])
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert f"{sid}/reports/r1.md" in names and f"{sid}/notes.txt" in names
    assert z.read(f"{sid}/reports/r1.md").decode() == "报告一"
    assert n_files == len(names) >= 2          # 脚手架文件（CLAUDE.md 等）照常进包
    assert not any(".fake" in n or "node_modules" in n or "chrome" in n
                   or ".steer" in n for n in names), names


async def test_archive_subdir(client, ws_root):
    """子目录打包：?path=reports/ → 顶层目录名 = reports，只含子树。"""
    sid, ws = await _mk_ws(client, ws_root)
    (ws / "reports").mkdir()
    (ws / "reports" / "sub").mkdir()
    (ws / "reports" / "a.md").write_text("A", encoding="utf-8")
    (ws / "reports" / "sub" / "b.md").write_text("B", encoding="utf-8")
    (ws / "outside.md").write_text("不该进包", encoding="utf-8")
    r = await client.get(f"/api/sessions/{sid}/archive",
                         params={"path": "reports/"})
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(names) == ["reports/a.md", "reports/sub/b.md"]


async def test_archive_path_safety(client, ws_root):
    """穿越与不存在：404；空目录（全被过滤）：400。"""
    sid, ws = await _mk_ws(client, ws_root)
    (ws / "reports").mkdir()
    (ws / ".only-hidden").mkdir()
    r = await client.get(f"/api/sessions/{sid}/archive",
                         params={"path": "../../etc"})
    assert r.status_code == 404
    r = await client.get(f"/api/sessions/{sid}/archive",
                         params={"path": "no-such-dir"})
    assert r.status_code == 404
    r = await client.get(f"/api/sessions/{sid}/archive",
                         params={"path": ".only-hidden/"})
    assert r.status_code == 400


async def test_archive_size_gate(client, ws_root, monkeypatch):
    """总大小闸：上限压到 10B → 超 400（防内存 zip 被大目录打爆）。"""
    from loadn_webui.api import routes as routes_mod
    monkeypatch.setattr(routes_mod, "_ARCHIVE_MAX_BYTES", 10)
    sid, ws = await _mk_ws(client, ws_root)
    (ws / "big.txt").write_text("x" * 100, encoding="utf-8")
    r = await client.get(f"/api/sessions/{sid}/archive")
    assert r.status_code == 400
    assert "2GB" in r.json()["detail"] or "大小" in r.json()["detail"]
