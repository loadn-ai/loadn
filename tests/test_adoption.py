"""Docker 式 daemon 重启的收养链测试（零 token，engine 级模拟停机）。

关键道具：fake 的 `pause` 旋钮——CLI init 后睡 WORKDADDY_FAKE_PAUSE_S 再写完
结果。测试在此窗口 cancel 掉 engine 的 worker task（等价 uvicorn 退场 cancel，
不杀子进程），再调 recover_after_restart() 走收养。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from loadn_webui import db as db_mod
from loadn_webui.claude_runner import LogTail, reap_orphans
from loadn_webui.config import CONFIG, PATHS
from loadn_webui.engine import ENGINE


@pytest.fixture(autouse=True)
def _pause_env(monkeypatch):
    monkeypatch.setenv("WORKDADDY_FAKE_PAUSE_S", "3")


def _fake_dir(ws_root, sid, **knobs):
    fake = ws_root / sid / ".fake"
    fake.mkdir(parents=True, exist_ok=True)
    for name, content in knobs.items():
        (fake / name).write_text(content if isinstance(content, str) else "")
    return fake


async def _wait_status(tid: int, want: set[str], timeout_s: float = 20) -> dict:
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout_s:
        await asyncio.sleep(0.2)
        with db_mod.conn() as c:
            t = db_mod.get_turn(c, tid)
        if t and t["status"] in want:
            return dict(t)
    raise TimeoutError(f"turn {tid} 未到 {want}")


async def _drive_turn(ws_root, sid: str, text: str = "干活") -> int:
    """直接经 ENGINE.submit 起 turn（本 loop 的 worker，可被 cancel 模拟停机）。"""
    _fake_dir(ws_root, sid, reply=f"{text}的完整回复", pause="")
    return await ENGINE.submit(sid, text)


async def _cancel_session_workers(sid: str) -> None:
    """模拟 daemon 退场：cancel 该 session 的 worker task（run_turn 的
    CancelledError 路径 = 不杀子进程）。等价 uvicorn _cancel_all_tasks。"""
    w = ENGINE._workers.get(sid)
    assert w is not None and not w.done()
    w.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w
    ENGINE._workers.pop(sid, None)
    # 队列丢弃（重启后 requeue 重建）
    ENGINE._queues.pop(sid, None)
    # active 句柄清场（新实例启动时是空的）
    for tid in list(ENGINE.active):
        if ENGINE.active[tid].session_id == sid:
            ENGINE.active.pop(tid)


# ---------------------------------------------------------------- LogTail
def test_logtail_unit(tmp_path: Path):
    p = tmp_path / "x.out"
    p.write_bytes(b'{"a":1}\n{"b":')
    t = LogTail(p)
    assert t.poll() == [b'{"a":1}']            # 半行留缓冲
    with p.open("ab") as f:
        f.write(b'2}\n{"c":3}\n')
    assert t.poll() == [b'{"b":2}', b'{"c":3}']
    assert t.poll() == []
    with p.open("ab") as f:
        f.write(b'{"tail":9}')
    t.poll()
    assert t.flush_pending() == b'{"tail":9}'
    assert t.flush_pending() == b""
    t.close()


# ---------------------------------------------------------------- 核心链路
async def test_adopt_running_completes(client, ws_root):
    """停机窗口内 CLI 照跑 → 新 daemon 收养 → 正常记账零丢失零重复。"""
    r = await client.post("/api/sessions", json={"title": "收养主链"})
    sid = r.json()["session"]["id"]
    tid = await _drive_turn(ws_root, sid)

    # 等 CLI 落 pid/log 进 turns 行（spawn 后立即写）
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < 5:
        with db_mod.conn() as c:
            t = db_mod.get_turn(c, tid)
        if t and t["pid"] and t["log_out"]:
            break
        await asyncio.sleep(0.1)
    with db_mod.conn() as c:
        t = db_mod.get_turn(c, tid)
    assert t["status"] == "running" and t["pid"]
    pid = t["pid"]
    # 停机前已发的 SSE 事件数（收养静默重放不得重复）
    with db_mod.conn() as c:
        pre = c.execute("SELECT COUNT(*) n FROM session_events WHERE turn_id=?",
                        (tid,)).fetchone()["n"]
    assert pre >= 1                                    # 至少 turn_queued/started

    # ---- 模拟 daemon 死（cancel worker；run_turn 不杀子进程）
    await _cancel_session_workers(sid)
    await asyncio.sleep(0.3)
    assert Path(f"/proc/{pid}").exists(), "cancel 不应杀掉 CLI 子进程"

    # ---- 新 daemon 启动恢复：收养
    out = ENGINE.recover_after_restart()
    assert tid in out["adopted"] and pid in out["claimed_pids"]

    fin = await _wait_status(tid, {"done", "error"})
    assert fin["status"] == "done"
    assert fin["usage_json"] and json.loads(fin["usage_json"])["input_tokens"] > 0
    assert fin["log_out"]                                # 记账完整
    # assistant 消息恰一条（幂等护栏）
    with db_mod.conn() as c:
        msgs = c.execute(
            "SELECT content FROM messages WHERE session_id=? AND turn_id=? AND role='assistant'",
            (sid, tid)).fetchall()
    assert len(msgs) == 1 and "完整回复" in msgs[0]["content"]
    # SSE 事件不重复：停机前 pre 条 + 收养后新增 ≥1（turn_done），但同类
    # init/text 不重复出现
    with db_mod.conn() as c:
        rows = c.execute("SELECT type, COUNT(*) n FROM session_events WHERE turn_id=?"
                         " GROUP BY type", (tid,)).fetchall()
    counts = {r["type"]: r["n"] for r in rows}
    assert counts.get("turn_done") == 1
    assert counts.get("text", 0) <= 1                    # 无重放重复


async def test_dead_pid_with_result_backfills(client, ws_root):
    """CLI 在停机期间自然跑完（pid 死 + 日志有 result）→ 启动补记账零丢失。"""
    r = await client.post("/api/sessions", json={"title": "补记账"})
    sid = r.json()["session"]["id"]
    tid = await _drive_turn(ws_root, sid, "补记账任务")
    fin = await _wait_status(tid, {"done"})
    assert fin["status"] == "done"

    # 伪造「_finish 没跑、进程已死」的 running 残留：把终态改回 running、
    # 删掉 assistant 消息（模拟 daemon 死在 _finish 前）
    with db_mod.conn() as c:
        c.execute("DELETE FROM messages WHERE session_id=? AND turn_id=? AND role='assistant'",
                  (sid, tid))
        db_mod.update_turn(c, tid, status="running", pid=fin["pid"],   # pid 已死
                           log_out=fin["log_out"], log_err=fin["log_err"],
                           finished_at=None)
    out = ENGINE.recover_after_restart()
    assert tid in out["finished"] and not out["adopted"]
    fin2 = await _wait_status(tid, {"done", "error"})
    assert fin2["status"] == "done"
    with db_mod.conn() as c:
        msgs = c.execute(
            "SELECT content FROM messages WHERE session_id=? AND turn_id=? AND role='assistant'",
            (sid, tid)).fetchall()
    assert len(msgs) == 1                                # 补回且不重复


async def test_dead_pid_no_result_interrupted(client, ws_root, monkeypatch):
    """pid 死 + 无 result 行（hang 被杀等）→ interrupted（旧行为语义保留）。"""
    monkeypatch.setenv("WORKDADDY_FAKE_HANG_S", "1")
    r = await client.post("/api/sessions", json={"title": "无结果中断"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, hang="")
    tid = await ENGINE.submit(sid, "挂起")
    await _wait_status(tid, {"running"})
    await _cancel_session_workers(sid)
    # 手工补 pid 死的形态：等 hang 1s 自然结束（rc=0 无 result 行）
    await asyncio.sleep(1.5)
    out = ENGINE.recover_after_restart()
    assert tid not in out["adopted"]
    fin = await _wait_status(tid, {"interrupted", "done", "error"})
    assert fin["status"] == "interrupted"


# ---------------------------------------------------------------- 串行闸
async def test_serial_gate_blocks_new_turns(client, ws_root, monkeypatch):
    """收养期间该 session 的新 turn 被闸住，收养完成才依次执行（串行保持）。"""
    monkeypatch.setenv("WORKDADDY_FAKE_PAUSE_S", "6")
    r = await client.post("/api/sessions", json={"title": "串行闸"})
    sid = r.json()["session"]["id"]
    tid1 = await _drive_turn(ws_root, sid, "第一个")
    await _wait_status(tid1, {"running"})
    await _cancel_session_workers(sid)

    out = ENGINE.recover_after_restart()
    assert tid1 in out["adopted"]
    # 收养进行中（CLI 还在 pause）：新消息 submit（走 HTTP 面也经 submit）
    tid2 = await ENGINE.submit(sid, "排队的第二个")
    await asyncio.sleep(1.0)
    with db_mod.conn() as c:
        t2 = db_mod.get_turn(c, tid2)
    assert t2["status"] == "queued", "收养期间新 turn 必须仍排队"

    fin1 = await _wait_status(tid1, {"done"})
    fin2 = await _wait_status(tid2, {"done", "error"}, timeout_s=30)
    assert fin1["status"] == "done" and fin2["status"] == "done"


# ---------------------------------------------------------------- 收养期控制
async def test_adopt_stop(client, ws_root, monkeypatch):
    """收养态的 stop 照常：killpg 幸存进程 → stopped 终态。"""
    monkeypatch.setenv("WORKDADDY_FAKE_PAUSE_S", "30")
    r = await client.post("/api/sessions", json={"title": "收养停止"})
    sid = r.json()["session"]["id"]
    tid = await _drive_turn(ws_root, sid, "长跑")
    await _wait_status(tid, {"running"})
    await _cancel_session_workers(sid)
    out = ENGINE.recover_after_restart()
    assert tid in out["adopted"]
    await asyncio.sleep(0.5)                             # 确保收养监督已起
    assert await ENGINE.stop_turn(tid)
    fin = await _wait_status(tid, {"stopped"})
    assert fin["status"] == "stopped"


# ---------------------------------------------------------------- reap 让位
async def test_reap_skips_claimed(tmp_path, monkeypatch):
    """reap_orphans(skip=claimed)：被收养认领的 pid 不清，未认领的照清。"""
    import subprocess
    claimed = subprocess.Popen(["sleep", "30"], start_new_session=True)
    orphan = subprocess.Popen(["sleep", "30"], start_new_session=True)
    _dir = PATHS["pid_dir"]
    _dir.mkdir(parents=True, exist_ok=True)
    (_dir / str(claimed.pid)).touch()
    (_dir / str(orphan.pid)).touch()
    try:
        # orphan 的父进程不是 serve → 会被清；claimed 在 skip 集里 → 放行
        # （不真跑 reap 杀 orphan——改为直接验证 skip 分支行为）
        n = reap_orphans(skip={claimed.pid})
        assert n >= 1
        assert Path(f"/proc/{claimed.pid}").exists()
    finally:
        for p in (claimed, orphan):
            try:
                p.kill()
                p.wait(timeout=3)
            except Exception:
                pass
        for p in (claimed.pid, orphan.pid):
            try:
                (_dir / str(p)).unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------- opencode
async def test_opencode_adopt(client, ws_root, monkeypatch):
    """opencode 收养：无 rc 可言——pid 消失 + 无 error 按 rc=0 合成 success。"""
    monkeypatch.setattr(CONFIG.engines.opencode, "bin",
                        str(Path(__file__).parent / "fake_opencode.py"))
    r = await client.post("/api/sessions", json={"title": "oc 收养"})
    sid = r.json()["session"]["id"]
    await client.patch(f"/api/sessions/{sid}", json={"engine": "opencode"})
    _fake_dir(ws_root, sid, pause="")

    tid = await ENGINE.submit(sid, "opencode 活")
    await _wait_status(tid, {"running"})
    await _cancel_session_workers(sid)
    out = ENGINE.recover_after_restart()
    assert tid in out["adopted"]
    fin = await _wait_status(tid, {"done", "error"}, timeout_s=30)
    assert fin["status"] == "done", fin.get("error")
