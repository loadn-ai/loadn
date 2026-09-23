"""附件上传链路：upload 加固 + messages attachments + prompt 注入 + env 注入。"""
import json
from pathlib import Path

from tests.conftest import (
    wait_turn,  # 注意：不能 from tests.conftest（会二次执行 conftest 换掉 HOME）
)


async def _mk_session(client) -> str:
    r = await client.post("/api/sessions", json={"title": "附件测试"})
    return r.json()["session"]["id"]


async def _upload(client, sid: str, name: str, content: bytes = b"hello-file"):
    return await client.post(f"/api/sessions/{sid}/upload",
                             files={"file": (name, content, "application/octet-stream")})


# ---------------------------------------------------------------- upload 加固
async def test_upload_hardening(client, ws_root):
    sid = await _mk_session(client)

    r = await _upload(client, sid, "a.png", b"\x89PNG" + b"x" * 2048)
    assert r.status_code == 200
    d = r.json()
    assert d["path"] == "inputs/a.png" and d["is_image"] is True and d["kb"] > 0
    assert (ws_root / sid / "inputs" / "a.png").read_bytes().startswith(b"\x89PNG")

    # 同名冲突 → 自动 -1
    r = await _upload(client, sid, "a.png")
    assert r.json()["path"] == "inputs/a-1.png"

    # 路径穿越被剥成 basename
    r = await _upload(client, sid, "../../evil.sh")
    assert r.json()["path"] == "inputs/evil.sh"
    assert not (ws_root / "evil.sh").exists()

    # 超限 → 413 且不留半截文件
    from loadn_webui.api import routes as routes_mod
    orig = routes_mod.MAX_UPLOAD_BYTES
    routes_mod.MAX_UPLOAD_BYTES = 10
    try:
        r = await _upload(client, sid, "big.bin", b"x" * 4096)
        assert r.status_code == 413
        assert not (ws_root / sid / "inputs" / "big.bin").exists()
    finally:
        routes_mod.MAX_UPLOAD_BYTES = orig

    # 非图片
    r = await _upload(client, sid, "note.txt")
    assert r.json()["is_image"] is False


# ---------------------------------------------------------------- 消息附件 → prompt
async def test_message_with_attachments(client, fake_calls, ws_root):
    sid = await _mk_session(client)
    await _upload(client, sid, "chart.png", b"\x89PNGchart")
    await _upload(client, sid, "spec.pdf", b"%PDF-fake")

    atts = [
        {"path": "inputs/chart.png", "name": "chart.png", "kb": 8.0, "is_image": True},
        {"path": "inputs/spec.pdf", "name": "spec.pdf", "kb": 9.0, "is_image": False},
    ]
    r = await client.post(f"/api/sessions/{sid}/messages",
                          json={"text": "解析这两个文件", "attachments": atts})
    assert r.status_code == 200, r.text
    tid = r.json()["turn"]["id"]
    turn = await wait_turn(client, sid, tid)
    assert turn["status"] == "done"

    # prompt 里注入【附件】块（fake 记录 prompt 字段；路径为绝对定位）
    prompt = fake_calls()[-1]["prompt"]
    assert "解析这两个文件" in prompt and "【附件】" in prompt
    assert "inputs/chart.png（图片，可直接用 Read 查看" in prompt
    assert prompt.count("/inputs/chart.png") == 1   # 绝对路径唯一出现
    assert "spec.pdf（文档" in prompt

    # DB content 干净、blocks_json 存附件
    r = await client.get(f"/api/sessions/{sid}")
    msgs = r.json()["messages"]
    um = next(m for m in msgs if m["role"] == "user")
    assert um["content"] == "解析这两个文件"
    blocks = json.loads(um["blocks_json"])
    assert [b["path"] for b in blocks] == ["inputs/chart.png", "inputs/spec.pdf"]
    assert blocks[0]["type"] == "attachment"

    # 纯附件消息（text 空）→ 兜底文案
    r = await client.post(f"/api/sessions/{sid}/messages", json={
        "text": "", "attachments": [{"path": "inputs/chart.png", "is_image": True}]})
    assert r.status_code == 200

    # 历史消息（无 blocks_json）prompt 不拼附件块
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "普通消息"})
    tid = r.json()["turn"]["id"]
    await wait_turn(client, sid, tid)
    assert "【附件】" not in fake_calls()[-1]["prompt"]


async def test_attachment_validation(client):
    sid = await _mk_session(client)
    r = await client.post(f"/api/sessions/{sid}/messages",
                          json={"text": "x", "attachments": [{"path": "work/a.png"}]})
    assert r.status_code == 400
    r = await client.post(f"/api/sessions/{sid}/messages",
                          json={"text": "x", "attachments": [{"path": "inputs/../etc"}]})
    assert r.status_code == 400
    r = await client.post(f"/api/sessions/{sid}/messages",
                          json={"text": "x", "attachments": [{"path": "/abs"}]})
    assert r.status_code == 400


# ---------------------------------------------------------------- env 注入 + scan 回填
async def test_settings_env_injection(client, ws_root, monkeypatch):
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.resources, "proxy",
                        "http://127.0.0.1:7890")   # 显式注入（默认值已通用化）
    sid = await _mk_session(client)
    p = ws_root / sid / ".claude" / "settings.json"
    env = json.loads(p.read_text())["env"]
    cli = env.get("LOADN_CLI") or env.get("WORKDADDY_CLI")
    assert cli and Path(cli).exists()          # console_script 绝对路径（wd/loadn-web）
    assert env["WORKDADDY_CDP_URL"].endswith("/cdp")
    assert env["WORKDADDY_PROXY"].startswith("http://")

    # 老会话回填：删掉 settings.json 后 scan 重建
    p.unlink()
    from loadn_webui import cli
    assert cli.main(["scan"]) == 0
    env = json.loads(p.read_text())["env"]
    assert "WORKDADDY_CLI" in env

    # 宪法里有附件约定
    md = (ws_root / sid / "CLAUDE.md").read_text()
    assert "【附件】" in md and "inputs/" in md
