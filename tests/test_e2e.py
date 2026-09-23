"""端到端零 token 测试：fake_claude 驱动，覆盖 P1 核心链路。

- 建会话→发消息→turn done→记账→messages 落库
- 第二条消息 --resume 续连（argv 断言）
- SSE 事件流（tool_use/tool_result/todos/files/turn_done）
- 产物扫描 + 导出（md→html/docx）
- stop / error / 轮换（bigusage）/ resume 秒拒重试
"""
import asyncio
import json

from tests.conftest import wait_turn


async def _create(client, **body):
    resp = await client.post("/api/sessions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]


async def _send(client, sid, text):
    resp = await client.post(f"/api/sessions/{sid}/messages", json={"text": text})
    assert resp.status_code == 200, resp.text
    return resp.json()["turn"]


async def test_basic_turn_and_accounting(client, fake_calls):
    n0 = len(fake_calls())
    sess = await _create(client, title="基础对话", first_message="你好，介绍一下你自己")
    sid = sess["id"]
    assert sess["profile"] == "assistant"       # 关键词全零 → 兜底
    tid = sess and (await client.get(f"/api/sessions/{sid}")).json()["turns"][0]["id"]
    t = await wait_turn(client, sid, tid)
    assert t["status"] == "done"
    assert t["cost_usd"] == 0.05
    assert t["num_turns"] == 2
    # messages：user + assistant
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert "fake claude" in detail["messages"][1]["content"]
    # 假 CLI 收到的是 --session-id（首 turn）
    calls = fake_calls()[n0:]
    assert len(calls) == 1
    assert calls[0]["session"]["flag"] == "--session-id"
    # session 记账累计 + fresh=0
    assert detail["usage"]["cost_usd"] == 0.05
    assert detail["session_fresh"] == 0


async def test_resume_second_message(client, fake_calls):
    sess = await _create(client, title="续连测试")
    sid = sess["id"]
    t1 = await _send(client, sid, "第一条")
    await wait_turn(client, sid, t1["id"])
    n0 = len(fake_calls())
    t2 = await _send(client, sid, "第二条")
    await wait_turn(client, sid, t2["id"])
    calls = fake_calls()[n0:]
    assert len(calls) == 1
    assert calls[0]["session"]["flag"] == "--resume"
    assert calls[0]["session"]["value"] == sess["claude_session_id"]


async def test_sse_event_stream(client, ws_root):
    sess = await _create(client, title="SSE测试", profile="researcher")
    sid = sess["id"]
    # skills symlink 挂载（researcher → deep-research/doc-export）
    sk = ws_root / sid / ".claude" / "skills"
    assert (sk / "deep-research").is_symlink()
    assert (sk / "doc-export").is_symlink()
    # 控制文件：工具 + todos + 产物
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "tools").touch()
    (ctrl / "todos").touch()
    (ctrl / "artifacts").touch()

    events: list[tuple[str, dict]] = []
    sse_task = asyncio.create_task(_drain_sse(client, sid, events, want="turn_done"))
    await asyncio.sleep(0.3)                     # 先订阅
    t = await _send(client, sid, "跑一轮带工具的任务")
    await asyncio.wait_for(sse_task, timeout=20)
    types = [e[0] for e in events]
    assert "turn_queued" in types and "turn_started" in types
    assert "text" in types
    assert "thinking" in types                    # 思考过程下发
    assert types.count("tool_use") >= 3          # TaskCreate + Bash + Write
    assert types.count("tool_result") >= 3
    assert "todos" in types and "files" in types
    assert types[-1] in ("turn_done", "files")   # 终态后可能还有 files/prune 无事件
    # 工具卡片配对
    tu = [e for ty, e in events if ty == "tool_use"]
    tr = [e for ty, e in events if ty == "tool_result"]
    assert {x["id"] for x in tu} == {x["id"] for x in tr}
    # 详情富化：thinking 文本 / tool_use 完整 input / tool_result 结果内容
    think = [e for ty, e in events if ty == "thinking"]
    assert think and "验证环境" in think[0]["text"]
    bash = next(x for x in tu if x["name"] == "Bash")
    assert bash["input"]["command"] == "echo hello-fake"
    bash_r = next(x for x in tr if x["id"] == bash["id"])
    assert bash_r["result"] == "hello-fake"
    # 产物扫描
    arts = (await client.get(f"/api/sessions/{sid}/artifacts")).json()["artifacts"]
    paths = {a["path"] for a in arts}
    assert "artifacts/report.md" in paths and "artifacts/citations-audit.json" in paths
    # blocks_json 持久化富化（刷新页面后思考/输入/结果仍可见）
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    am = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    blocks = json.loads(am["blocks_json"])
    assert any(b.get("type") == "thinking" for b in blocks)
    tb = next(b for b in blocks if b.get("name") == "Bash")
    assert tb["input"]["command"] == "echo hello-fake" and tb["result"] == "hello-fake"
    # 无头模式禁用交互弹窗工具
    st = json.loads((ws_root / sid / ".claude" / "settings.json").read_text())
    assert "AskUserQuestion" in st["permissions"]["disallow"]


async def _drain_sse(client, sid, events, want, timeout_s=20):
    """收集 SSE 事件直到出现 want 类型。"""
    import time
    t0 = time.monotonic()
    async with client.stream("GET", f"/api/sessions/{sid}/events") as resp:
        assert resp.status_code == 200
        etype, eid, data = None, None, None
        async for line in resp.aiter_lines():
            if time.monotonic() - t0 > timeout_s:
                break
            if line.startswith("event: "):
                etype = line[7:]
            elif line.startswith("id: "):
                eid = int(line[4:])
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif not line.strip() and etype:
                events.append((etype, dict(data or {})))
                if etype == want:
                    return
                etype = data = None
            elif line.startswith(": ping"):
                continue


async def test_stop_running_turn(client, ws_root):
    sess = await _create(client, title="停止测试")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "hang").touch()
    t = await _send(client, sid, "长任务")
    await asyncio.sleep(1.0)                     # 让它进入 running
    resp = await client.post(f"/api/turns/{t['id']}/stop")
    assert resp.status_code == 200
    t = await wait_turn(client, sid, t["id"])
    assert t["status"] == "stopped"


async def test_error_turn(client, ws_root):
    sess = await _create(client, title="错误测试")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "fail").touch()
    t = await _send(client, sid, "会失败的任务")
    t = await wait_turn(client, sid, t["id"])
    assert t["status"] == "error"
    assert t["error"] and "exit=1" in t["error"]
    # 零输出报错也要有回显：消息流末尾必须是占位 assistant 消息而非停在 user
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    last = detail["messages"][-1]
    assert last["role"] == "assistant"
    assert "turn 报错退出" in last["content"]
    assert "exit=1" in last["content"]


async def test_rotation_on_big_usage(client, ws_root):
    sess = await _create(client, title="轮换测试", profile="researcher")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "bigusage").touch()
    t = await _send(client, sid, "大上下文任务")
    await wait_turn(client, sid, t["id"])
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["session_fresh"] == 1                     # 轮换：下轮 --session-id
    assert detail["claude_session_id"] != sess["claude_session_id"]
    assert detail["usage"]["in"] == 600000


async def test_resume_fastfail_rotates(client, ws_root, fake_calls):
    sess = await _create(client, title="resume秒拒")
    sid = sess["id"]
    t1 = await _send(client, sid, "先正常跑一轮")
    await wait_turn(client, sid, t1["id"])                  # fresh=0
    n0 = len(fake_calls())
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "fastfail").touch()
    t2 = await _send(client, sid, "这条会秒拒")
    t2 = await wait_turn(client, sid, t2["id"], timeout_s=30)
    calls = fake_calls()[n0:]
    # resume 秒拒×2 → 轮换 → fresh 也秒拒 → error（共 3 次）
    assert len(calls) == 3
    assert t2["status"] == "error"
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["session_fresh"] == 1


async def test_export_html_docx(client, ws_root):
    sess = await _create(client, title="导出测试")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "artifacts").touch()
    t = await _send(client, sid, "写份报告")
    await wait_turn(client, sid, t["id"])
    # md → html
    resp = await client.post(f"/api/sessions/{sid}/export",
                             json={"source_path": "artifacts/report.md", "format": "html"})
    assert resp.status_code == 200, resp.text
    aid_html = resp.json()["artifact_id"]
    prev = await client.get(f"/api/artifacts/{aid_html}/preview")
    assert prev.status_code == 200
    assert "<!DOCTYPE html>" in prev.text and "测试报告" in prev.text
    # md → docx
    resp = await client.post(f"/api/sessions/{sid}/export",
                             json={"source_path": "artifacts/report.md", "format": "docx"})
    assert resp.status_code == 200, resp.text
    aid_docx = resp.json()["artifact_id"]
    dl = await client.get(f"/api/artifacts/{aid_docx}/download")
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"                           # zip magic（docx 即 zip）
    assert len(dl.content) > 4000


async def test_profiles_and_skills_api(client):
    resp = await client.get("/api/profiles")
    names = {p["name"] for p in resp.json()["profiles"]}
    assert {"researcher", "coder", "assistant"} <= names
    resp = await client.get("/api/skills")
    skills = {s["name"] for s in resp.json()["skills"]}
    assert {"deep-research", "doc-export"} <= skills
    # 自动匹配：调研类关键词 → researcher
    resp = await client.post("/api/sessions", json={"title": "新能源汽车市场调研"})
    assert resp.json()["session"]["profile"] == "researcher"
    resp = await client.post("/api/sessions", json={"title": "写个python数据分析脚本"})
    assert resp.json()["session"]["profile"] == "coder"


async def test_zero_choice_auto_profile(client, fake_calls, ws_root):
    """零选择新建：空 body → profile_auto 标记；首条消息重新匹配角色（连 skills 换默认）；
    聊天框选「✨ 自动」可重新武装。"""
    resp = await client.post("/api/sessions", json={})
    sid = resp.json()["session"]["id"]
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["profile_auto"] is True and detail["profile"] == "assistant"

    # 首条消息带研究关键词 → 重新匹配为 researcher + 换该角色 skills
    resp = await client.post(f"/api/sessions/{sid}/messages",
                             json={"text": "调研 2026 固态电池量产进展并出报告"})
    tid = resp.json()["turn"]["id"]
    assert (await wait_turn(client, sid, tid))["status"] == "done"
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["profile"] == "researcher" and detail["profile_auto"] is False
    assert "deep-research" in json.loads(detail["skills_json"])

    # 再选「自动」→ 重新武装，下一条消息又重匹配（研究 → 保持 researcher）
    resp = await client.patch(f"/api/sessions/{sid}", json={"profile": "auto"})
    assert (await client.get(f"/api/sessions/{sid}")).json()["profile_auto"] is True
    resp = await client.post(f"/api/sessions/{sid}/messages",
                             json={"text": "换个任务：写个爬虫脚本实现数据采集"})
    tid = resp.json()["turn"]["id"]
    assert (await wait_turn(client, sid, tid))["status"] == "done"
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["profile"] == "coder" and detail["profile_auto"] is False

    # 显式选具体角色 → 不再自动
    await client.patch(f"/api/sessions/{sid}", json={"profile": "assistant"})
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["profile"] == "assistant" and detail["profile_auto"] is False

    # 带首条消息创建（老路径）不武装 profile_auto（创建时已用真实文本匹配过）
    resp = await client.post("/api/sessions", json={"first_message": "你好"})
    sid2 = resp.json()["session"]["id"]
    assert (await client.get(f"/api/sessions/{sid2}")).json()["profile_auto"] is False


async def test_giant_stream_line_survives(client, ws_root):
    """单行 stream-json >64KB（Read 图片 base64）不再炸 reader → turn 正常 done。"""
    sess = await _create(client, title="超长行测试")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "giantline").touch()
    t = await _send(client, sid, "读一张大截图")
    t = await wait_turn(client, sid, t["id"])
    assert t["status"] == "done", t["error"]           # 老实现：engine_internal_error
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    am = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    assert "图片已读完" in am["content"]               # 巨行之后的事件照常消费


async def test_live_endpoint_rebuild(client, ws_root):
    """/live 快照：跑中可取（含已流出的 items），终态后归 None。"""
    sess = await _create(client, title="live重建测试")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "hang").touch()
    t = await _send(client, sid, "长任务")
    await asyncio.sleep(1.2)                           # 让 turn 进入 running 并流出文本
    live = (await client.get(f"/api/sessions/{sid}/live")).json()["live"]
    assert live and live["turnId"] == t["id"] and live["status"] == "running"
    assert any(i["kind"] == "text" for i in live["items"])
    await client.post(f"/api/turns/{t['id']}/stop")
    await wait_turn(client, sid, t["id"])
    assert (await client.get(f"/api/sessions/{sid}/live")).json()["live"] is None


async def test_reap_orphans_stale_pid_file():
    """崩溃遗留的死进程 pid 登记不再炸启动（NameError 回归：误调 _unregister）。"""
    from loadn_webui.claude_runner import reap_orphans
    from loadn_webui.config import PATHS
    PATHS["pid_dir"].mkdir(parents=True, exist_ok=True)
    stale = PATHS["pid_dir"] / "9999999"          # 不存在的 pid
    stale.write_text("")
    assert reap_orphans() == 0
    assert not stale.exists()                     # 顺带被清掉


async def test_file_tree_and_read(client, ws_root):
    sess = await _create(client, title="文件测试")
    sid = sess["id"]
    (ws_root / sid / "work" / "demo.py").write_text("print('hi')\n")
    resp = await client.get(f"/api/sessions/{sid}/tree")
    paths = [x["path"] for x in resp.json()["tree"]]
    assert "work/demo.py" in paths and "CLAUDE.md" in paths
    resp = await client.get(f"/api/sessions/{sid}/file", params={"path": "work/demo.py"})
    assert resp.json()["text"] == "print('hi')\n"
    # 路径穿越防护
    resp = await client.get(f"/api/sessions/{sid}/file", params={"path": "../../etc/passwd"})
    assert resp.json().get("error") == "not_found"


async def test_max_turns_reaches_cli(client, fake_calls):
    """收敛度轮次上限贯通到 claude CLI argv（--max-turns）。"""
    from loadn_webui import profile as profile_mod
    prof = profile_mod.get("researcher")
    assert prof.max_turns is None            # 默认不限制 → argv 不带 --max-turns
    sess = await _create(client, title="收敛默认", profile="researcher")
    t = await _send(client, sess["id"], "深度研究")
    await wait_turn(client, sess["id"], t["id"])
    assert "--max-turns" not in fake_calls()[-1]["argv"]

    prof.max_turns = 7                       # 内存态生效即透传（registry roundtrip 见 test_admin）
    try:
        sess = await _create(client, title="收敛限制", profile="researcher")
        t = await _send(client, sess["id"], "再深度研究")
        await wait_turn(client, sess["id"], t["id"])
        argv = fake_calls()[-1]["argv"]
        i = argv.index("--max-turns")
        assert argv[i + 1] == "7"
    finally:
        prof.max_turns = None


async def _collect_sse(client, sid, seconds=1.5, params=None):
    """收集 SSE 流 seconds 秒内的事件（fresh connect 回放断言用）。"""
    events: list[tuple[str, dict]] = []

    async def run():
        async with client.stream("GET", f"/api/sessions/{sid}/events",
                                 params=params or {}) as resp:
            assert resp.status_code == 200
            etype, data = None, None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    etype = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                elif not line.strip() and etype:
                    events.append((etype, dict(data or {})))
                    etype = data = None
                elif line.startswith(": ping"):
                    continue

    task = asyncio.create_task(run())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return events


async def test_precise_replay_fresh_connect(client, monkeypatch):
    """精准回放：终态会话 fresh connect 零回放；活跃 turn 只回放尾部 N 条；
    Last-Event-ID 断线补发语义不变。"""
    from loadn_webui import db as db_mod
    from loadn_webui.config import CONFIG
    sess = await _create(client, title="精准回放", first_message="你好")
    sid = sess["id"]
    tid = (await client.get(f"/api/sessions/{sid}")).json()["turns"][0]["id"]
    await wait_turn(client, sid, tid)

    # ① 无活跃 turn → fresh connect 零回放
    ev = await _collect_sse(client, sid, 1.2)
    assert not ev, f"终态会话 fresh connect 不应回放，收到 {ev[:3]}"

    # ② 活跃 turn + 追加 5 条事件，窗口 3 → 只回放尾部 3 条（不含 fake turn 原生 text）
    with db_mod.conn() as c:
        c.execute("UPDATE turns SET status='running' WHERE id=?", (tid,))
        for i in range(5):
            db_mod.add_event(c, sid, tid, "text", {"turn_id": tid, "text": f"m{i}"})
    monkeypatch.setattr(CONFIG.run, "replay_max_events", 3)
    ev = await _collect_sse(client, sid, 1.2)
    texts = [d["text"] for t, d in ev if t == "text"]
    assert texts == ["m2", "m3", "m4"], texts
    # turn_queued/started（旧事件）不在尾窗内也不回放——live meta 由 /live 播种
    assert "turn_queued" not in [t for t, _ in ev]

    # ③ Last-Event-ID 断线补发：窗口外也全补（events_after 语义不变）
    with db_mod.conn() as c:
        rows = c.execute("SELECT id FROM session_events WHERE session_id=? ORDER BY id",
                         (sid,)).fetchall()
        mid = rows[2]["id"]                      # 全部现存事件中第 3 条
    ev = await _collect_sse(client, sid, 1.5, params={"last_event_id": mid})
    got_ids = ...  # _collect 不记 id；以内容断言：应包含窗口外的 m0/m1
    texts = [d["text"] for t, d in ev if t == "text" and str(d.get("text", "")).startswith("m")]
    assert "m0" in texts and "m1" in texts, texts


async def test_prune_events_terminal_aware(client):
    """终态感知清理：未超期不删；终态超 retain 天删光；活跃 turn 永不清。"""
    import datetime as dt

    from loadn_webui import db as db_mod
    sess = await _create(client, title="事件清理", first_message="你好")
    sid = sess["id"]
    tid = (await client.get(f"/api/sessions/{sid}")).json()["turns"][0]["id"]
    await wait_turn(client, sid, tid)

    def count(c=None):
        if c is not None:   # 同一连接（事务内读自己的写）
            return c.execute("SELECT COUNT(*) n FROM session_events WHERE session_id=?",
                             (sid,)).fetchone()["n"]
        with db_mod.conn() as c2:
            return count(c2)

    with db_mod.conn() as c:
        n0 = count(c)
        assert n0 > 0
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9)).isoformat()
        assert db_mod.prune_events(c, sid, 7) == 0            # 未超期不删
        c.execute("UPDATE turns SET started_at=? WHERE id=?", (old, tid))
        assert db_mod.prune_events(c, sid, 7) == n0           # 终态超期 → 删光
        assert count(c) == 0
        # started_at 为空的终态 turn（提交即失败）不清——等它有 started_at 再说
        db_mod.add_event(c, sid, tid, "text", {"turn_id": tid, "text": "y"})
        c.execute("UPDATE turns SET started_at=NULL WHERE id=?", (tid,))
        assert db_mod.prune_events(c, sid, 7) == 0
        assert count(c) == 1
        # 活跃 turn 永不清（哪怕 started_at 很老）
        c.execute("UPDATE turns SET status='running', started_at=? WHERE id=?", (old, tid))
        assert db_mod.prune_events(c, sid, 7) == 0
        assert count(c) == 1


async def test_star_archive_purge_lifecycle(client, ws_root):
    """收藏/归档/彻底删除全生命周期：starred 跨归档保留、点星不扰动排序键、
    purge 连 workspace 一起清。"""
    import asyncio
    sess = await _create(client, title="收藏与删除")
    sid = sess["id"]
    ws = ws_root / sid
    assert ws.is_dir()

    # 收藏：starred=1；纯标记位不 touch updated_at（睡 1.1s 保证秒级时间戳可比）
    u0 = (await client.get(f"/api/sessions/{sid}")).json()["updated_at"]
    await asyncio.sleep(1.1)
    r = await client.patch(f"/api/sessions/{sid}", json={"starred": True})
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["starred"] == 1
    assert d["updated_at"] == u0                       # 点星不跳顶
    lst = (await client.get("/api/sessions")).json()["sessions"]
    assert next(x for x in lst if x["id"] == sid)["starred"] == 1

    # 取消收藏
    await client.patch(f"/api/sessions/{sid}", json={"starred": False})
    assert (await client.get(f"/api/sessions/{sid}")).json()["starred"] == 0

    # 归档（侧栏 ×的语义）→ 收藏位保留 → 恢复为进行中
    await client.patch(f"/api/sessions/{sid}", json={"starred": True})
    r = await client.delete(f"/api/sessions/{sid}")
    assert r.json() == {"ok": True, "archived": sid}
    assert (await client.get(f"/api/sessions/{sid}")).json()["status"] == "archived"
    await client.patch(f"/api/sessions/{sid}", json={"status": "active"})
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["status"] == "active" and d["starred"] == 1

    # 彻底删除：DB 行 + workspace 一并清掉，kv 残留也清
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"title_auto:{sid}", "1")
    r = await client.delete(f"/api/sessions/{sid}?purge=true")
    assert r.status_code == 200 and r.json() == {"ok": True, "deleted": sid}
    assert not ws.exists()
    assert (await client.get(f"/api/sessions/{sid}")).status_code == 404
    lst = (await client.get("/api/sessions")).json()["sessions"]
    assert all(x["id"] != sid for x in lst)
    with db_mod.conn() as c:
        assert db_mod.kv_get(c, f"title_auto:{sid}") is None


# ---------------------------------------------------------------- 排队消息撤回 / 停止竞态
async def test_retract_queued_message(client, ws_root, fake_calls):
    """发错的消息在排队中可整条撤回：turn+消息全删，且队列残留 tid 不被执行。"""
    n0 = len(fake_calls())
    sess = await _create(client, title="撤回测试")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "hang").touch()                          # t1 占住会话（hang 120s）
    t1 = await _send(client, sid, "占住会话的长任务")
    await asyncio.sleep(1.0)                         # t1 进入 running
    t2 = await _send(client, sid, "发错的调研消息")    # 会话串行 → t2 排队
    resp = await client.delete(f"/api/turns/{t2['id']}")
    assert resp.status_code == 200, resp.text
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert all(m["content"] != "发错的调研消息" for m in detail["messages"])
    assert all(x["id"] != t2["id"] for x in detail["turns"])
    # 运行中的不可撤（409，须先停止）；不存在的 404
    assert (await client.delete(f"/api/turns/{t1['id']}")).status_code == 409
    assert (await client.delete("/api/turns/999999")).status_code == 404
    # 停 t1 收尾：worker 轮到 t2 的残留 tid 会被状态闸/判空跳过，不发起 claude 调用
    await client.post(f"/api/turns/{t1['id']}/stop")
    t1 = await wait_turn(client, sid, t1["id"])
    assert t1["status"] == "stopped"
    await asyncio.sleep(1.5)
    prompts = [c["prompt"] for c in fake_calls()[n0:]]
    assert "占住会话的长任务" in prompts
    assert "发错的调研消息" not in prompts


async def test_stop_queued_turn_skips_execution(client, ws_root, fake_calls):
    """停止排队中的 turn：立即落 stopped 且不再被执行。

    此前 bug：stop_turn 只改 DB 状态，asyncio.Queue 里的 tid 不移除，
    worker 照样执行（_run_turn 不复查状态）。
    """
    n0 = len(fake_calls())
    sess = await _create(client, title="停止排队")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "hang").touch()
    t1 = await _send(client, sid, "长任务一")
    await asyncio.sleep(1.0)
    t2 = await _send(client, sid, "排队后停止的消息")
    assert (await client.post(f"/api/turns/{t2['id']}/stop")).status_code == 200
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    t2row = next(x for x in detail["turns"] if x["id"] == t2["id"])
    assert t2row["status"] == "stopped"              # 立即终态
    await client.post(f"/api/turns/{t1['id']}/stop")
    await wait_turn(client, sid, t1["id"])
    await asyncio.sleep(1.5)
    prompts = [c["prompt"] for c in fake_calls()[n0:]]
    assert "长任务一" in prompts
    assert "排队后停止的消息" not in prompts


async def test_tree_skips_heavy_dirs_and_caches(client, ws_root):
    """tree() 不进 chrome*/node_modules 重目录（防 AnyIO 线程池被 rglob 占满）且有 TTL 缓存。"""
    import time as _time

    from loadn_webui import artifacts as art
    sess = await _create(client, title="树过滤")
    sid = sess["id"]
    ws = ws_root / sid
    (ws / "work").mkdir(parents=True, exist_ok=True)
    (ws / "work" / "note.md").write_text("x")
    (ws / "work" / "chrome-gh" / "Default").mkdir(parents=True, exist_ok=True)
    (ws / "work" / "chrome-gh" / "Default" / "cache.bin").write_text("y")
    (ws / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
    (ws / "node_modules" / "pkg" / "index.js").write_text("z")
    paths = [x["path"] for x in art.tree(sid)]
    assert "work/note.md" in paths
    assert not any("chrome" in p or "node_modules" in p for p in paths), paths
    # TTL 窗口内命中缓存：塞入哨兵值直接可见
    art._tree_cache[sid] = (_time.monotonic(),
                            [{"path": "CACHED", "dir": False, "size": 1, "mtime": 0}])
    assert art.tree(sid)[0]["path"] == "CACHED"
