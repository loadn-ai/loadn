"""D1 对抗用例（§8）：执行沙箱文件系统隔离（W2-a 第一步，--share-net 过渡）。

D1 语义：沙箱内写 /etc /usr 失败；读宿主 HOME 凭证/平台 var/vault 失败；
workspace 正常；其他会话档案不可见；turn 受控失败不崩溃。
真 bwrap 实机跑（无 bwrap/非本机环境 skipif）。
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from loadn_webui import sandbox

pytestmark = pytest.mark.skipif(not sandbox.bwrap_available(),
                                reason="bwrap 不可用（CI/受限环境）")


def _engine_home() -> Path:
    import os
    return Path(os.environ.get("LOADN_HOME")
                or os.environ.get("HAHANESS_HOME") or Path.home() / ".loadn")


def _run_in_sandbox(ws: Path, sid_session: str, script: str,
                    env: dict | None = None) -> subprocess.CompletedProcess:
    wrapped = sandbox.wrap_loadn(
        ["bash", "-c", script], env or {"PATH": "/usr/bin:/bin"},
        sid_session=sid_session, cwd=ws)
    assert wrapped is not None
    return subprocess.run(wrapped, capture_output=True, text=True, timeout=60)


def test_d1_write_system_dirs_blocked(tmp_path):
    p = _run_in_sandbox(tmp_path, str(uuid.uuid4()),
                        "touch /etc/pwned 2>&1; touch /usr/pwned 2>&1")
    assert "Read-only file system" in p.stdout


def test_d1_host_secrets_invisible(tmp_path):
    """P5 终态：全部敏感面不可见——~/.ssh、平台 var/vault.enc、两处
    config.yaml、~/.claude 全家（含 settings.json=LLM 凭证零入沙箱）。"""
    p = _run_in_sandbox(
        tmp_path, str(uuid.uuid4()),
        "for f in /home/user/.ssh/id_rsa "
        "/data/code/workdaddy/var/vault.enc /data/code/loadn/config.yaml "
        "/data/code/workdaddy/config.yaml /home/user/.claude/.credentials.json "
        "/home/user/.claude/projects /home/user/.claude/settings.json; "
        "do ls $f 2>&1; done")
    assert "No such file or directory" in p.stdout
    assert p.stdout.count("No such file or directory") == 7


def test_d1_workspace_and_archive_ok(tmp_path):
    """workspace 内正常读写；本会话档案可写；venv python 可跑。"""
    sid_session = str(uuid.uuid4())
    p = _run_in_sandbox(
        tmp_path, sid_session,
        "echo data > out.txt && cat out.txt && "
        "echo arch > " + str(_engine_home() / "sessions" / sid_session)
        + "/t.log && /data/code/loadn/.venv/bin/python --version")
    assert "data" in p.stdout and "Python" in p.stdout
    assert ((_engine_home() / "sessions" / sid_session / "t.log")
            .read_text().strip() == "arch")     # 档案写透回宿主


def test_d1_other_sessions_invisible(tmp_path):
    """档案隔离：sessions/ 下只见本会话。"""
    mine = str(uuid.uuid4())
    _run_in_sandbox(tmp_path, mine, "true")
    p = _run_in_sandbox(tmp_path, mine,
                        f"ls {_engine_home() / 'sessions'}")
    assert p.stdout.strip() == mine


def test_d1_env_cleared(tmp_path):
    """--clearenv：宿主任意 env 不透传（白名单 --setenv 例外）。"""
    import os
    p = subprocess.run(
        sandbox.wrap_loadn(
            ["bash", "-c", "echo DUMMY=$DUMMY_EVIL; echo KEEP=$LOADN_TURN_ID"],
            {"LOADN_TURN_ID": "7"}, sid_session=str(uuid.uuid4()),
            cwd=tmp_path),
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "DUMMY_EVIL": "leak"})
    assert "DUMMY=" in p.stdout and "leak" not in p.stdout
    assert "KEEP=7" in p.stdout


async def test_d1_sandboxed_real_turn(client, ws_root, monkeypatch):
    """W2-a 门禁：sandbox=bwrap 下 loadn 引擎真 turn（fake）全链跑通——
    沙箱不破坏 spawn/事件流/steer 协议通道（同路径 bind）。"""
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    monkeypatch.setattr(CONFIG.engines, "default", "loadn")
    monkeypatch.setenv("LOADN_PROVIDER", "fake")
    r = await client.post("/api/sessions", json={"title": "D1 沙箱 turn"})
    sid = r.json()["session"]["id"]
    ws = ws_root / sid
    (ws / ".fake").mkdir(parents=True, exist_ok=True)
    (ws / ".fake" / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "echo sbx-ok"}}))
    r = await client.post(f"/api/sessions/{sid}/messages",
                          json={"text": "沙箱内干活"})
    tid = r.json()["turn"]["id"]
    from tests.conftest import wait_turn
    t = await wait_turn(client, sid, tid, timeout_s=40)
    assert t["status"] == "done", t.get("error")
    # turn 产物落在宿主侧 workspace（bind 写透）：引擎 transcript 落档
    # （fake 引擎 Bash 不真执行——断言档案+审计，产物执行面由 D1 单测锁定）
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
    ts = Path(os.environ.get("LOADN_HOME") or Path.home() / ".loadn")
    assert (ts / "sessions" / sess["claude_session_id"] / "transcript.jsonl").exists()
    # 审计留痕 mode=bwrap
    from loadn_webui import audit as audit_mod
    rows = [r_ for r_ in audit_mod.tail(30, "snapshot")
            if r_["detail_json"].find('"mode": "bwrap"') >= 0]
    assert rows


def test_d1_unshare_net_direct_blocked(tmp_path, monkeypatch):
    """P3 物理断网：uds 桥在位时 loadn 沙箱 unshare-net——直连失败、
    代理通道（uds→宿主）可达。"""
    import socket as _sock

    from loadn_webui.config import PATHS
    uds = PATHS["run"] / "egress.sock"
    if not uds.exists():                 # 代理未起（纯单元环境）→ 造一个假 socket 文件
        PATHS["run"].mkdir(parents=True, exist_ok=True)
        srv = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        srv.bind(str(uds))
    monkeypatch.setattr(sandbox, "_egress_uds", lambda sid="": uds)
    wrapped = sandbox.wrap_loadn(
        ["bash", "-c",
         'curl -s -o /dev/null -w "%{http_code}" --max-time 4 '
         'https://example.com; echo " direct=$(ip -o link show | grep -c lo)"'],
        {"PATH": "/usr/bin:/bin"},
        sid_session=str(uuid.uuid4()), cwd=tmp_path)
    assert wrapped is not None
    assert "--unshare-net" in wrapped
    p = subprocess.run(wrapped, capture_output=True, text=True, timeout=60)
    assert "000" in p.stdout              # 直连失败（无外联路由）
