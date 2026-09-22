"""E1 对抗用例（§8）：误操作后回滚（W6.2 快照）+ provenance 基础（W5.4）。

E1  删库类误操作后 rollback → 工作区恢复到写前版本 + 审计 rollback
W5.4 宪法安全章节已物化（untrusted 不得作不可逆依据等五条平台强制项）
"""
from __future__ import annotations

import json
from pathlib import Path

from loadn_webui import policy


def test_e1_snapshot_and_rollback_cycle(tmp_path, monkeypatch):
    """全周期：写前快照（hook 路径）→ 改坏 → rollback 恢复 → 现状存 rollback-pre。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "app.py"
    f.write_text("print('v1 好的版本')")

    # 模拟引擎 hook 的写前快照（与 policy_check_hook 同函数）
    policy._snapshot_before_write(str(f))
    points = policy.list_snapshots(tmp_path)
    assert len(points) == 1 and points[0]["files"] == 1

    # 误操作：改坏
    f.write_text("import shutil; shutil.rmtree('/')  # 坏了")
    assert "坏了" in f.read_text()

    # 回滚
    out = policy.rollback(tmp_path, points[0]["point"])
    assert out["ok"] and "src/app.py" in out["restored"]
    assert f.read_text() == "print('v1 好的版本')"
    # 现状（坏版）也存了 rollback-pre 点——可双向回滚
    assert any(p["point"].startswith("rollback-pre-") for p in
               policy.list_snapshots(tmp_path))


def test_e1_hook_end_to_end_snapshot():
    """policy-check --hook 对 Write 工具：路径校验 + 快照一次完成。"""
    import subprocess
    import sys
    import tempfile
    td = Path(tempfile.mkdtemp())
    (td / "notes.md").write_text("原稿")
    p = subprocess.run(
        [sys.executable, "-m", "loadn_webui", "policy-check"],
        input=json.dumps({"tool": "Write",
                          "tool_input": {"file_path": str(td / "notes.md")}}),
        capture_output=True, text=True, timeout=60, cwd=td)
    assert p.returncode == 0
    snaps = list((td / ".snapshots").rglob("*"))
    assert any(s.is_file() and s.read_text() == "原稿" for s in snaps)


async def test_e1_api_flow(client, ws_root):
    sid = (await client.post("/api/sessions", json={"title": "E1 回滚"})
           ).json()["session"]["id"]
    ws = ws_root / sid
    r = await client.get(f"/api/sessions/{sid}/snapshots")
    assert r.status_code == 200 and "snapshots" in r.json()
    # 造一个快照点 + 改坏 + API 回滚
    (ws / "notes").mkdir(exist_ok=True)
    (ws / "notes" / "draft.md").write_text("正文 v1")
    import os
    old_cwd = os.getcwd()
    os.chdir(ws)                                  # hook 语义：cwd=workspace
    try:
        policy._snapshot_before_write(str(ws / "notes" / "draft.md"))
    finally:
        os.chdir(old_cwd)
    (ws / "notes" / "draft.md").write_text("被覆盖的坏内容")
    pts = (await client.get(f"/api/sessions/{sid}/snapshots")).json()["snapshots"]
    assert pts
    r = await client.post(f"/api/sessions/{sid}/rollback",
                          json={"point": pts[-1]["point"]})
    assert r.json()["ok"]
    assert (ws / "notes" / "draft.md").read_text() == "正文 v1"
    from loadn_webui import audit as audit_mod
    assert audit_mod.tail(5, "rollback")


def test_w54_constitution_security_section():
    """宪法模板含平台强制安全五条（provenance/Rule of Two 底座）。"""
    from loadn_webui.config import PATHS
    tmpl = (PATHS["prompts"] / "workspace.md.tmpl").read_text()
    assert "安全机制（平台强制" in tmpl
    assert "--request-approval" in tmpl and "canary" in tmpl
