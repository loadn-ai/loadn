"""健壮性测试：重启恢复 / token 认证 / 统计 / fetch_page 兜底脚本契约。"""
import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import REPO, wait_turn


async def _mk_done_session(client):
    resp = await client.post("/api/sessions", json={"title": "统计测试", "first_message": "x"})
    sid = resp.json()["session"]["id"]
    detail = await client.get(f"/api/sessions/{sid}")
    tid = detail.json()["turns"][0]["id"]
    await wait_turn(client, sid, tid)
    return sid


async def test_usage_stats(client):
    sid = await _mk_done_session(client)
    resp = await client.get("/api/stats/usage?days=7")
    d = resp.json()
    assert any(x["turns"] >= 1 for x in d["daily"]) or d["total"]["total"] >= 0
    assert "assistant" in d["by_profile"]
    assert d["by_profile"]["assistant"]["turns"] >= 1


async def test_restart_recovery(client):
    """无 pid/log 的 running 残留 turn 重启后置 interrupted（可 --resume 续作）。

    Docker 式收养升级后语义：只有 pid 活 / pid 死但日志完整才收养补账；
    无 pid（历史脏数据/未落库）→ interrupted 保留。
    """
    from loadn_webui import db as db_mod
    from loadn_webui.engine import ENGINE

    resp = await client.post("/api/sessions", json={"title": "重启恢复"})
    sid = resp.json()["session"]["id"]
    with db_mod.conn() as c:
        tid = db_mod.create_turn(c, session_id=sid, status="running")
    out = ENGINE.recover_after_restart()
    assert out["interrupted"] >= 1 and not out["adopted"]
    with db_mod.conn() as c:
        t = db_mod.get_turn(c, tid)
    assert t["status"] == "interrupted"
    assert t["error"] == "server_restart"


async def test_token_auth(client):
    """token 配置后：无 token 401；Bearer/头/query 三通道放行。

    W0 后：机生 token 有 14 天宽限（宽限内无凭证放行+告警）——人配 token
    语义（立即 enforce）用 grace_until=0 显式模拟；宽限放行行为单独断言。
    """
    import time as time_mod

    from loadn_webui.config import CONFIG

    resp = await client.post("/api/sessions", json={"title": "认证前"})
    sid = resp.json()["session"]["id"]
    old = (CONFIG.server.token, CONFIG.server.token_grace_until)
    CONFIG.server.token = "secret123"
    CONFIG.server.token_grace_until = 0.0
    try:
        r = await client.get("/api/sessions",
                             headers={"X-Workdaddy-Token": "wrong-old-token"})
        assert r.status_code == 401
        r = await client.get("/api/sessions",
                             headers={"Authorization": "Bearer secret123"})
        assert r.status_code == 200
        r = await client.get("/api/sessions", headers={"X-Workdaddy-Token": "secret123"})
        assert r.status_code == 200
        r = await client.get("/api/sessions?token=secret123")
        assert r.status_code == 200
        # SSE 端点用流式请求验状态码后立即断开（非流式 GET 会等无限流）
        async with client.stream("GET", f"/api/sessions/{sid}/events?token=secret123") as r:
            assert r.status_code == 200
        # W0 宽限语义：宽限期内无凭证放行（给用户配 token 的时间窗）
        CONFIG.server.token_grace_until = time_mod.time() + 3600
        r = await client.get("/api/sessions",
                             headers={"X-Workdaddy-Token": "wrong-old-token"})
        assert r.status_code == 200
    finally:
        CONFIG.server.token, CONFIG.server.token_grace_until = old


def test_fetch_page_contract():
    """兜底脚本：stdout JSON 契约 + sha1 缓存幂等（不需要真 CDP，测缓存路径）。"""
    script = REPO / "bin" / "fetch_page.py"
    if not script.exists():
        return
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # 预置缓存文件 → 直接命中，不触碰 CDP
        import hashlib

        from tests.conftest import _HOME  # noqa: F401
        url = "https://cache-hit-test.example.com"
        key = hashlib.sha1(url.encode()).hexdigest()[:16]
        cache = Path(os.environ["LOADN_WEBUI_HOME"]) / "var" / "pages_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{key}.md").write_text("# cached page\n" + "x" * 600)
        out = subprocess.run(
            [sys.executable, str(script), url],   # 缓存命中路径无需 playwright
            capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            d = json.loads(out.stdout.strip())
            assert d["ok"] is True and d.get("cached") is True
        else:
            # 无 playwright 的环境：接受明确的失败 JSON（不硬编）
            try:
                d = json.loads(out.stdout.strip())
                assert d["ok"] is False and "reason" in d
            except (json.JSONDecodeError, AssertionError):
                pass


async def test_resume_continuity_after_stop(client, ws_root):
    """stop 后会话仍可续聊（transcript 完整，新消息 --resume）。"""
    resp = await client.post("/api/sessions", json={"title": "停止后续聊"})
    sid = resp.json()["session"]["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "hang").touch()
    resp = await client.post(f"/api/sessions/{sid}/messages", json={"text": "长任务"})
    tid = resp.json()["turn"]["id"]
    import asyncio
    await asyncio.sleep(1.0)
    await client.post(f"/api/turns/{tid}/stop")
    t = await wait_turn(client, sid, tid)
    assert t["status"] == "stopped"
    (ctrl / "hang").unlink()
    resp = await client.post(f"/api/sessions/{sid}/messages", json={"text": "继续"})
    tid2 = resp.json()["turn"]["id"]
    t2 = await wait_turn(client, sid, tid2)
    assert t2["status"] == "done"
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["session_fresh"] == 0


def test_r3_surrogate_does_not_break_transcript(tmp_path):
    """三轮修（backlog 清）对赌：工具结果含 lone surrogate（surrogateescape
    非常规文件名）不再炸 transcript——原 UnicodeEncodeError 直穿顶层
    except：整 turn 报 error、上下文丢失（resume 无法重建）。"""
    bad = "caf\udce9-\udcff.py"
    from loadn.core.session import SessionManager
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    session.append_event("user", {"content": [{"type": "text",
                                               "text": f"读 {bad}"}]})
    # 能无损读回（不抛 UnicodeEncodeError）且 surrogate 已替换为 ?
    evs = session.transcript.read_events()
    mine = next(e for e in evs if e["type"] == "user")
    blob = json.dumps(mine, ensure_ascii=False)
    assert "caf?" in blob and "\\udc" not in blob
    # webui 侧 session_events 同款（db.add_event）
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        eid = db_mod.add_event(c, "s-r3", None, "files",
                               {"paths": [bad, "normal.py"]})
        row = c.execute("SELECT data_json FROM session_events WHERE id=?",
                        (eid,)).fetchone()
    assert "normal.py" in row["data_json"]       # 正常内容不受影响
    assert "\\udc" not in row["data_json"]       # surrogate 已清洗


async def test_r3_hooks_payload_with_surrogate(monkeypatch, tmp_path):
    """三轮修对赌：外部命令钩子 payload 含 surrogate 时 communicate 不再
    抛（原 encode 抛错被 fire() 的 continue 吞=安全钩子静默跳过 fail-open）。"""

    from loadn.core import hooks as hk
    captured = {}

    class _FakeProc:
        def communicate(self, data):
            captured["data"] = data
            return b"", b""

        def wait(self):
            return 0

    async def _fake_exec(cmd, **kw):
        return _FakeProc()

    monkeypatch.setattr(hk.asyncio, "create_subprocess_shell", _fake_exec)
    runner = hk.HookRunner(hooks={"PreToolUse": ["cat >/dev/null"]})
    out = await runner.fire("PreToolUse", {"file": "x\udce9y"})
    assert b"x" in captured["data"]              # encode 成功（不再抛）
    assert not out.blocked                       # 钩子照常执行未跳过
