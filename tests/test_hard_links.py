"""实战链路补测：单元绿 ≠ 真链路通。这批全是未覆盖过的真实路径。

①调度器 20s 真实循环（此前只测手动 tick） ②CLI schedule 真实执行（此前
只冒烟 parser） ③engine error turn 的通知钩子 ④click-option 二分真鼠标
分支（fixture 拦合成事件） ⑤ingest CORS 头（页内跨源直传可确认成败）。
"""
import asyncio
import sys
import types
from pathlib import Path

import pytest

# 依赖本机 legacy skills 资产（web-ops 库）与真浏览器；CI 上无本机资产时跳过
pytestmark = pytest.mark.skipif(
    not Path("/data/code/workdaddy/skills").exists(),
    reason="需要本机 legacy skills 资产（迁移过渡期）")

from loadn_webui import db as db_mod
from loadn_webui.util import iso

HERE = Path(__file__).resolve().parent
import os as _os

OPS_PATH = str(Path(_os.environ.get("LOADN_SKILLS_EXTRA")
                    or HERE.parent / "skills") / "web-ops")


# ---------------------------------------------------------------- ① 真实循环
async def test_scheduler_real_loop(client, monkeypatch):
    """起真 _loop（扫描周期压到 1s）→ due job 被循环自动触发，非手动 tick。"""
    from loadn_webui import scheduler as sched_mod
    from loadn_webui.engine import Engine
    monkeypatch.setattr(sched_mod, "CHECK_INTERVAL", 1.0)

    r = await client.post("/api/sessions", json={"title": "真循环"})
    sid = r.json()["session"]["id"]
    eng = Engine()
    s = sched_mod.Scheduler(eng)
    try:
        with db_mod.conn() as c:
            jid = db_mod.create_job(c, session_id=sid, label="循环验证",
                                    prompt="醒来", due_at=iso(), max_fires=1)
        s.start()                       # 不 tick——等 _loop 自己扫到
        fired = None
        t0 = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - t0 < 15:
            await asyncio.sleep(0.3)
            with db_mod.conn() as c:
                job = db_mod.get_job(c, jid)
            if job["fires"] >= 1:
                fired = job
                break
        assert fired is not None, "15s 内 _loop 未触发 due job"
        assert fired["status"] == "done"
    finally:
        await s.stop()
        for t in list(eng._workers.values()):
            t.cancel()
        for at in list(eng.active.values()):
            at.stop.stop()


# ---------------------------------------------------------------- ② CLI schedule
def test_cli_schedule_real_run(client, capsys):
    from loadn_webui.cli import main
    r = asyncio.get_event_loop().run_until_complete(
        client.post("/api/sessions", json={"title": "CLI 调度"}))
    sid = r.json()["session"]["id"]

    assert main(["schedule", "add", "--sid", sid, "--in", "5m",
                 "--label", "冷却重考", "--prompt", "重考 FAO"]) == 0
    out = capsys.readouterr().out
    assert "冷却重考" in out and "单次" in out
    with db_mod.conn() as c:
        jobs = db_mod.list_jobs(c, sid)
        jid = jobs[0]["id"]
        assert jobs[0]["max_fires"] == 1 and jobs[0]["due_at"] > iso()

    # 递归 add：--every 默认 max_fires=20（防跑飞下限）
    assert main(["schedule", "add", "--sid", sid, "--in", "10m", "--every", "30m",
                 "--prompt", "轮询徽章"]) == 0
    capsys.readouterr()
    with db_mod.conn() as c:
        assert db_mod.list_jobs(c, sid)[-1]["max_fires"] == 20

    assert main(["schedule", "list", "--sid", sid]) == 0
    assert "轮询徽章" in capsys.readouterr().out
    assert main(["schedule", "pause", str(jid)]) == 0
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["status"] == "paused"
    assert main(["schedule", "resume", str(jid)]) == 0
    assert main(["schedule", "del", str(jid)]) == 0
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid) is None
    # 不存在的会话 → 1
    assert main(["schedule", "add", "--sid", "nope", "--in", "5m",
                 "--prompt", "x"]) == 1


# ---------------------------------------------------------------- ③ engine 通知钩子
async def test_engine_notify_on_error(client, ws_root, monkeypatch):
    from loadn_webui.integrations import notify as notify_mod
    got = []

    def fake_fire(title, body="", event=""):
        got.append({"title": title, "event": event})
    monkeypatch.setattr(notify_mod, "fire", fake_fire)

    r = await client.post("/api/sessions", json={"title": "通知钩子"})
    sid = r.json()["session"]["id"]
    # 先落失败旋钮再发消息：first_message 会在 POST 内立刻触发 turn，先发后写
    # 存在「fake 已启动、旋钮未落盘」竞态（全量套件负载下偶发 turn 意外成功）
    (ws_root / sid / ".fake" / "fail").parent.mkdir(parents=True, exist_ok=True)
    (ws_root / sid / ".fake" / "fail").write_text("")
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "跑挂它"})
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < 20:   # 等 error 终态
        await asyncio.sleep(0.3)
        with db_mod.conn() as c:
            rows = c.execute("SELECT status FROM turns WHERE session_id=?",
                             (sid,)).fetchall()
        if rows and all(x["status"] in ("done", "error", "stopped", "interrupted")
                        for x in rows) and rows:
            if any(x["status"] == "error" for x in rows):
                break
    errs = [g for g in got if g["event"] == "on_error"]
    assert errs, "error turn 未触发 on_error 通知钩子"
    assert "通知钩子" in errs[0]["title"] or "turn 报错" in errs[0]["title"]


# ---------------------------------------------------------------- ④ click-option 二分
@pytest.mark.skipif(not Path("/usr/bin/google-chrome").exists()
                    and not Path("/usr/bin/chromium").exists(),
                    reason="本机无 chrome")
def test_click_option_bisect_real_mouse():
    """JS click 被 fixture 拦截（isTrusted=false）→ auto 二分走真鼠标坐标命中。"""
    import shutil
    import socket
    import subprocess
    import time as time_mod

    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    profile = Path("/tmp") / f"wd_bisect_{port}"
    proc = subprocess.Popen(
        [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--headless=new", "--no-first-run", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        import urllib.request
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time_mod.sleep(0.25)
        sys.path.insert(0, OPS_PATH)
        import ops
        pg = ops.Page(f"http://127.0.0.1:{port}")
        try:
            pg.page.goto(f"file://{HERE / 'fixtures' / 'web_ops_quiz.html'}")
            pg.page.wait_for_timeout(300)

            # js 模式：被拦 → 未选中 → rc 1
            a = types.SimpleNamespace(sel="label.optx", index=0, mode="js")
            rc = ops.cmd_click_option(pg, a)
            assert rc == 1 and not pg.page.evaluate("document.getElementById('cx').checked")

            # auto 模式：JS 失败自动转真鼠标 → isTrusted=true 放行 → 选中
            pg.page.evaluate("document.getElementById('cx').checked = false")
            a = types.SimpleNamespace(sel="label.optx", index=0, mode="auto")
            rc = ops.cmd_click_option(pg, a)
            assert rc == 0
            assert pg.page.evaluate("document.getElementById('cx').checked") is True
            log = pg.page.evaluate("document.getElementById('guard-log').textContent")
            assert "S" in log and "T" in log    # 合成与真事件都发生过=二分真的走了两步
        finally:
            pg.close()
            sys.path.remove(OPS_PATH)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        import shutil as sh
        sh.rmtree(profile, ignore_errors=True)


# ---------------------------------------------------------------- ⑤ ingest CORS
async def test_ingest_cors_headers(client):
    """页内 JS 跨源直传的前提：响应带 ACAO，gzip 重建 Response 不丢自定义头。"""
    r = await client.post("/api/sessions", json={"title": "CORS"})
    sid = r.json()["session"]["id"]
    r = await client.post(f"/api/sessions/{sid}/ingest",
                          params={"to": "artifacts/"},
                          files={"file": ("a.png", b"\x89PNG-fake", "image/png")},
                          headers={"Accept-Encoding": "gzip"})   # 逼 gzip 路径
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*", \
        f"ACAO 头丢失（gzip 中间件重建 Response 丢弃？实际头: {dict(r.headers)}）"
