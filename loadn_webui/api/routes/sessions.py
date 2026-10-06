"""会话生命周期：建/列/详情/参数覆盖/删 + egress 视图/live 转发/快照回滚/熔断。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from ... import artifacts as art
from ... import db as db_mod
from ... import profile as profile_mod
from ... import skills as skills_mod
from ... import workspace as ws_mod
from ...config import CONFIG
from ...engine import ENGINE

router = APIRouter(prefix="/api")

from ._common import _apply_partition_mutex, _fold_blocks, _get_session_or_404, _job_fields


@router.post("/sessions")
async def create_session(body: dict):
    title = (body.get("title") or body.get("first_message") or "新任务").strip()[:80]
    prof_name = body.get("profile") or "auto"
    text_probe = f"{title} {body.get('first_message') or ''}"
    prof = (profile_mod.get(prof_name) if prof_name != "auto"
            else profile_mod.auto_match(text_probe))
    skills = body.get("skills")
    if skills is None:
        skills = list(prof.skills)
    mcp = body.get("mcp") or None
    project_id = (body.get("project_id") or "").strip() or None
    category_id = body.get("category_id")   # 分类内直接新建：落位即分类
    if category_id is not None:
        with db_mod.conn() as c:
            if not isinstance(category_id, int) or db_mod.get_category(c, category_id) is None:
                raise HTTPException(404, f"category 不存在: {category_id}")
    if project_id:
        with db_mod.conn() as c:
            proj = db_mod.get_project(c, project_id)
        if proj is None or proj["status"] != "active":
            raise HTTPException(404, f"project 不存在或已归档: {project_id}")
        # 子任务的 skills/mcp 缺省取项目存的（create_session 内处理）
        if body.get("skills") is None:
            skills = None
        if body.get("mcp") is None:
            mcp = None
    sid, ws = ws_mod.create_session(title, prof, skills, mcp, project_id=project_id)
    if category_id is not None:
        with db_mod.conn() as c:
            db_mod.update_session(c, sid, touch=False, category_id=category_id)
    with db_mod.conn() as c:
        # 未显式命名 → 允许自动标题（成功生成或手动改名后清标记）
        if not (body.get("title") or "").strip():
            db_mod.kv_set(c, f"title_auto:{sid}", "1")
        # 零选择创建（auto + 无首条消息）→ 首条真实消息时重新匹配角色
        if prof_name == "auto" and not (body.get("first_message") or "").strip():
            db_mod.kv_set(c, f"profile_auto:{sid}", "1")
    if body.get("first_message"):
        await ENGINE.submit(sid, body["first_message"])
    with db_mod.conn() as c:
        sess = db_mod.to_dict(db_mod.get_session(c, sid))
    return {"session": sess}
@router.get("/sessions")
def list_sessions():
    from ...scheduler import next_wake
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_sessions(c)]
        for r in rows:
            act = db_mod.active_turns(c, r["id"])
            r["active_turn"] = db_mod.to_dict(act[-1]) if act else None
            r["usage"] = db_mod.usage_totals(c, r["id"])
            # 聊天框模式下拉读列表（5s 轮询），这里带上自动角色标记
            r["profile_auto"] = bool(db_mod.kv_get(c, f"profile_auto:{r['id']}"))
            r["next_wake"] = next_wake(r["id"])
    return {"sessions": rows}
@router.get("/sessions/{sid}")
def session_detail(sid: str, request: Request):
    sess = _get_session_or_404(sid)
    with db_mod.conn() as c:
        sess["messages"] = [db_mod.to_dict(r) for r in db_mod.list_messages(c, sid)]
        for m in sess["messages"]:
            if m.get("role") == "assistant":          # 附件块在 user 消息里，不动
                m["blocks_json"] = _fold_blocks(m.get("blocks_json"))
        sess["turns"] = [db_mod.to_dict(r) for r in c.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY id", (sid,)).fetchall()]
        sess["schedules"] = [db_mod.to_dict(r) for r in db_mod.list_jobs(c, sid)]
    sess["artifacts"] = art.list_artifacts(sid)
    sess["skills_available"] = [s["name"] for s in skills_mod.available()]
    # 属性面板「模型参数」三件套：覆盖 / 生效值 / profile 侧默认（占位提示用）
    from ... import params as params_mod
    prof = profile_mod.get(sess["profile"])
    sess["params"] = params_mod.load(sess.get("params_json"))
    sess["params_effective"] = params_mod.effective(prof, sess["params"])
    sess["params_profile"] = params_mod.defaults(prof)
    from ...scheduler import next_wake
    sess["next_wake"] = next_wake(sid)
    with db_mod.conn() as c:
        sess["profile_auto"] = bool(db_mod.kv_get(c, f"profile_auto:{sid}"))
    # 摘要回填兜底：单 turn 会话收尾那次若静默失败（模型格式漂移）没有下次
    # 收尾可重试——打开会话（看产物面板）即再触发（NULL 行才做事，幂等）。
    # 本路由是同步 def（线程池），经主循环线程安全投递
    if any(a.get("summary") is None for a in sess["artifacts"]):
        import asyncio
        loop = getattr(request.app.state, "loop", None)
        if loop is not None:
            asyncio.run_coroutine_threadsafe(
                _quiet_summary_backfill(sid), loop)
    return sess


async def _quiet_summary_backfill(sid: str) -> None:
    try:
        await art.ensure_summaries(sid)
    except Exception:  # noqa: BLE001 —— 兜底路径，任何失败不响应用户请求
        pass
@router.get("/sessions/{sid}/egress")
def session_egress(sid: str, n: int = 50):
    """会话外联视图（属性面板「安全与外联」段）：模式/白名单/本会话临时
    授权/最近外联事件。会话面 GET 不经 admin 门（单用户部署双令牌同发）。

    事件只含新版权起的记录（旧 egress_request 行无会话归属，append-only
    不可回填）——UI 文案已注明。
    """
    _get_session_or_404(sid)
    n = max(1, min(200, n))
    from ... import params as params_mod
    from ...security import audit as audit_mod
    from ...security import egress_grants
    events = []
    for r in audit_mod.tail(n, "egress_request", sid=sid):
        d = json.loads(r["detail_json"])
        events.append({"ts": r["ts"], **d})
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
    override = params_mod.session_egress_override(sess["params_json"] if sess else None)
    return {"mode": CONFIG.security.egress_mode,
            "effective": override or CONFIG.security.egress_mode,
            "override": override,              # null=跟随全局（面板 chip 态）
            "on_deny": CONFIG.security.egress_on_deny,
            "ask_wait_s": CONFIG.security.egress_ask_wait_s,
            "allow": CONFIG.security.egress_allow,
            "grants": egress_grants.list_active(sid=sid),
            "events": events}
@router.get("/sessions/{sid}/live")
def session_live(sid: str):
    """进行中 turn 的实时快照（刷新/重进会话后前端据此重建 live：停止按钮+过程流水）。"""
    _get_session_or_404(sid)
    with db_mod.conn() as c:
        act = db_mod.active_turns(c, sid)
    if not act:
        return {"live": None}
    # 优先 running：串行队列里有排队的后续消息时（act[-1] 是最新 queued），
    # live 应报告真正在跑的 turn，否则前端重进会话会把流水挂到排队 turn 上
    t = next((r for r in act if r["status"] == "running"), act[-1])
    at = ENGINE.active.get(t["id"])
    items: list[dict] = []
    if at:
        # blocks 已是穿插时间线（thinking/tool/text 按真实顺序）——直接映射；
        # at.texts 不再尾部追加（与 text 块同源，会重复）
        for b in at.blocks:
            if b.get("type") == "thinking":
                items.append({"kind": "thinking", "text": b.get("text", "")})
            elif b.get("type") == "text":
                items.append({"kind": "text", "text": b.get("text", "")})
            elif b.get("type") == "tool":
                items.append({"kind": "tool", **{k: b.get(k) for k in
                            ("id", "name", "brief", "input", "result", "is_error")}})
    # 截尾：深度研究一小时能积累数百节点（实测 627 条/860KB），全量下发会把
    # 重进会话的首帧渲染压垮；前端 live 只渲染尾部窗口，完整过程结束落库可回看
    items = items[-200:]
    return {"live": {"turnId": t["id"], "status": t["status"], "items": items,
                     "todos": list(at.todos) if at else [],
                     "startedAt": t["started_at"]}}
@router.patch("/sessions/{sid}")
async def patch_session(sid: str, body: dict):
    _get_session_or_404(sid)     # 404 探测（行内容不需要）
    if "project_id" in body:
        # 移动 = 只改 DB 不搬文件 → artifacts/shares 相对路径悬空指向不存在的
        # 目录。v1 禁止；要归组就在目标项目下新建子任务。
        raise HTTPException(400, "暂不支持移动会话到项目（工作区不迁移）；"
                                 "请在项目下新建子任务")
    updates = {}
    if "title" in body:
        updates["title"] = str(body["title"])[:80]
    if "status" in body:
        if body["status"] not in ("active", "archived"):
            raise HTTPException(400, "status 只能是 active|archived")
        updates["status"] = body["status"]
    if "starred" in body:
        # 纯标记位：不扰动 updated_at（否则点星会把会话顶到列表最上）
        updates["starred"] = 1 if bool(body["starred"]) else 0
    if "pinned" in body:
        updates["pinned"] = 1 if bool(body["pinned"]) else 0
    if "category_id" in body:
        # 移动到自定义分类（null = 回「最近」）；纯分区操作同样不 touch
        cid = body.get("category_id")
        if cid is not None:
            with db_mod.conn() as c:
                if not isinstance(cid, int) or db_mod.get_category(c, cid) is None:
                    raise HTTPException(404, f"category 不存在: {cid}")
        updates["category_id"] = cid
    _apply_partition_mutex(body, updates)
    if "profile" in body:
        p = str(body["profile"])
        if p == "auto":
            # 聊天框选「✨ 自动」：保持当前角色，下一条消息重新匹配
            with db_mod.conn() as c:
                db_mod.kv_set(c, f"profile_auto:{sid}", "1")
        else:
            prof = profile_mod.get(p)
            with db_mod.conn() as c:
                db_mod.kv_del(c, f"profile_auto:{sid}")
            updates["profile"] = prof.name
            ws_mod.rerender(sid, prof_name=prof.name)
    skills = body.get("skills")
    if skills is not None:
        updates["skills_json"] = json.dumps([str(s) for s in skills], ensure_ascii=False)
        # v0.6：子任务目录私有（settings/skills 任务级）——rerender 对子任务
        # 只重挂任务目录不碰项目宪法；项目属主会话仍走全量重渲染
        ws_mod.rerender(sid, skills=[str(s) for s in skills])
    if "mcp" in body:
        updates["mcp_json"] = json.dumps(body.get("mcp") or {}, ensure_ascii=False)
        ws_mod.write_mcp_json(ws_mod.ws_of(sid), body.get("mcp") or {})
    if "engine" in body:
        # 聊天框内核切换：会话级覆盖（下一 turn 生效；id 迁移由引擎的
        # _align_engine 在 turn 启动时处理）。null/空串 = 回到跟随配置。
        from ... import engines as engines_mod
        val = body.get("engine")
        if val in (None, "", False):
            updates["engine_override"] = None
        else:
            name = str(val)
            if name not in engines_mod.ENGINES:
                raise HTTPException(400, f"未知引擎：{name}"
                                    f"（可用：{sorted(engines_mod.ENGINES)}）")
            updates["engine_override"] = name
    if "params" in body:
        # 会话级参数覆盖（属性面板「模型参数」）：整体替换语义（与 skills/
        # mcp 一致），null = 全清跟随 profile；下一 turn 生效
        from ... import params as params_mod
        try:
            updates["params_json"] = params_mod.validate(body.get("params"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        # egress 档变更同步进会话快照（hook 门对齐——沙箱内读快照）
        ov = params_mod.load(updates["params_json"])
        ws_mod.write_egress_snapshot(
            ws_mod.ws_of(sid),
            mode=(ov.get("egress") if isinstance(ov, dict) else None))
    if updates:
        # 纯分区标记（收藏/置顶/分类）不 touch：分区操作不该把会话顶到「最近」最上
        pure_partition = {"starred", "pinned", "category_id"}
        with db_mod.conn() as c:
            db_mod.update_session(c, sid, touch=not set(updates) <= pure_partition, **updates)
            if "title" in updates:            # 手动改名优先，停用自动标题
                db_mod.kv_del(c, f"title_auto:{sid}")
    return {"ok": True, "session": _get_session_or_404(sid)}
@router.delete("/sessions/{sid}")
def delete_session(sid: str, purge: bool = False):
    _get_session_or_404(sid)
    if not purge:
        with db_mod.conn() as c:
            db_mod.update_session(c, sid, status="archived")
        return {"ok": True, "archived": sid}
    for t in ENGINE.active.values():
        if t.session_id == sid:
            raise HTTPException(409, "会话有 turn 在跑，先停止")
    import shutil
    ws = ws_mod.ws_of(sid)
    with db_mod.conn() as c:
        # 共享工作区引用计数：兄弟 session（含归档）或项目行还指着该目录时
        # 只删 DB 行——目录属主未清零前 rmtree 会连坐别人的文件
        shared = db_mod.workspace_refcount(c, str(ws), exclude_sid=sid) > 0
        db_mod.delete_session(c, sid)
    if not shared:
        shutil.rmtree(ws, ignore_errors=True)
    return {"ok": True, "deleted": sid}
@router.get("/sessions/{sid}/snapshots")
def get_snapshots(sid: str):
    from ... import workspace as ws_mod
    from ...security.policy import list_snapshots
    _get_session_or_404(sid)
    return {"snapshots": list_snapshots(ws_mod.ws_of(sid))}
@router.post("/sessions/{sid}/rollback")
def post_rollback(sid: str, body: dict):
    from ... import workspace as ws_mod
    from ...security.policy import rollback
    _get_session_or_404(sid)
    return rollback(ws_mod.ws_of(sid), str(body.get("point") or ""))
@router.post("/sessions/{sid}/kill")
def kill_session(sid: str):
    """会话级 kill：停活跃 turn + 熔断（新消息拒绝）。管理面（admin 头）。"""
    _get_session_or_404(sid)
    from ... import db as db_mod
    from ...engine import ENGINE
    from ...security import canary as canary_mod
    stopped = []
    with db_mod.conn() as c:
        rows = c.execute("SELECT id FROM turns WHERE session_id=? AND"
                         " status IN ('running','queued')", (sid,)).fetchall()
    for r in rows:
        if ENGINE.stop_turn(r["id"]):
            stopped.append(r["id"])
    canary_mod.lock_session(sid, "kill switch（用户/自动触发）")
    return {"ok": True, "stopped_turns": stopped, "locked": True}
@router.post("/sessions/{sid}/unlock")
def unlock_session(sid: str):
    from ...security import canary as canary_mod
    return {"ok": canary_mod.unlock_session(sid)}
@router.get("/sessions/{sid}/approvals")
def list_approvals(sid: str):
    _get_session_or_404(sid)
    from ...security import approve as approve_mod
    return {"approvals": approve_mod.list_pending(sid)}
@router.get("/sessions/{sid}/timeline")
def session_timeline(sid: str):
    """P3-7 压缩时间线数据：沿会话的 turn 刻度 + compact 标记（何时裁了
    多少 token，hover=被裁摘要首行）。只读扫 transcript 尾部事件。"""
    _get_session_or_404(sid)
    import json as _json

    from ...engines.loadn import loadn_home
    p = loadn_home() / "sessions" / sid / "transcript.jsonl"
    try:
        lines = p.read_text(encoding="utf-8",
                            errors="replace").splitlines()[-500:]
    except OSError:
        return {"timeline": []}
    out: list[dict] = []
    for ln in lines:
        try:
            ev = _json.loads(ln)
        except ValueError:
            continue
        et = ev.get("type")
        payload = ev.get("payload") or {}
        if et == "result":
            usage = payload.get("usage") or {}
            out.append({"kind": "turn",
                        "turns": payload.get("num_turns"),
                        "tokens": (usage.get("input_tokens") or 0)
                        + (usage.get("output_tokens") or 0)})
        elif et == "compact":
            summary = str(payload.get("summary") or "")
            first = next((ln.strip() for ln in summary.splitlines()
                          if ln.strip()), "")
            out.append({"kind": "compact",
                        "tokens_cropped": payload.get("tokens_cropped"),
                        "summary_first": first[:120]})
    return {"timeline": out}
@router.post("/sessions/{sid}/approvals")
def create_approval(sid: str, body: dict):
    """agent CLI 发起（token 面）：{action_type, params, note} → {id, summary}。"""
    _get_session_or_404(sid)
    from ...engine import ENGINE
    from ...security import approve as approve_mod
    from ...security import target_policy as tp
    action_type = str(body.get("action_type") or "")
    params = dict(body.get("params") or {})
    # P10 决策序（外部副作用动作；bash_allow 等本地动作不进此表）：never→
    # 建+自动否决（全链路留痕）；always→建+自动批准（码随响应给 agent，
    # 即用即 consume——常设决定语义）；ask/无记录→原审批门
    policy = (tp.decide("action", action_type) if action_type != "bash_allow"
              else "ask")
    if params.get("host"):                  # 带目标域名的动作叠加 host 维度
        host_pol = tp.decide("host", str(params["host"]))
        policy = min((policy, host_pol),
                     key=lambda m: {"never": 0, "ask": 1, "always": 2}[m])
    try:
        out = approve_mod.create(sid, action_type, params,
                                 note=str(body.get("note") or ""),
                                 turn_id=body.get("turn_id"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if policy == "never":
        d = approve_mod.decide(out["id"], False, by="target_policy")
        # 二轮修#17：并发已裁决（webui/TG 抢先 Approve）时 decide 返回
        # ok:False——重读真态，approved 即把码带出（fail-open 窗口虽窄，
        # 但 silent deny→approved 的真相不能瞒）
        if not d.get("ok"):
            try:
                real = approve_mod.status(out["id"]).get("status")
            except LookupError:
                real = "gone"
            if real == "approved":
                ENGINE.publish(sid, "approval", {"kind": "decided",
                                                 "id": out["id"],
                                                 "status": "approved"})
                return {**out, "policy": "never",
                        "status": "approved-race",
                        "note": "并发窗口内已被批准（码经审批面发放）"}
        ENGINE.publish(sid, "approval", {"kind": "request", **out})
        ENGINE.publish(sid, "approval", {"kind": "decided", "id": out["id"],
                                         "status": "denied"})
        return {**out, "policy": "never", "status": "denied"}
    if policy == "always":
        d = approve_mod.decide(out["id"], True, by="target_policy")
        ENGINE.publish(sid, "approval", {"kind": "request", **out})
        ENGINE.publish(sid, "approval", {"kind": "decided", "id": out["id"],
                                         "status": "approved"})
        return {**out, "policy": "always", "status": "approved",
                "code": d.get("code")}
    ENGINE.publish(sid, "approval", {"kind": "request", **out})
    return out
@router.post("/sessions/{sid}/schedules")
def create_schedule(sid: str, body: dict):
    """会话内建 job（恒 message 动作——到点向该会话投递/插话）。"""
    _get_session_or_404(sid)
    body = {**body, "kind": "message"}
    fields = _job_fields(body, sid)
    with db_mod.conn() as c:
        jid = db_mod.create_job(c, **fields)
        job = db_mod.to_dict(db_mod.get_job(c, jid))
    return {"ok": True, "job": job}
@router.get("/sessions/{sid}/tree")
def get_tree(sid: str):
    _get_session_or_404(sid)
    return {"tree": art.tree(sid)}
