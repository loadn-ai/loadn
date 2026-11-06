"""P12 建议卡决策面：会话工作区 .loadn/skill-suggest.json 的读 + 确认/拒绝。

确认 → 写会话工作区 .agents/skills/<name>/SKILL.md（过 skill_scan 八类扫描，
红线拒写）；拒绝 → 负样本（同类指纹 7 天抑制，引擎侧同文件）。审计
type=consolidate（accepted/rejected）。draft 教训的转正走既有记忆编辑 API
（edit_entry 保存即除 draft 标记）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from loadn import consolidate

from ...security.audit import audit

router = APIRouter(prefix="/api")


def _ws_of(sid: str):
    # 二轮修#5：DB 感知（sessions.workspace 列——项目子任务在
    # <项目根>/tasks/，不是默认 workspace/<sid>）
    from ... import workspace as ws_mod
    ws = ws_mod.ws_of(sid)
    if not ws.is_dir():
        raise HTTPException(404, f"会话不存在: {sid}")
    return ws


def _pending(ws) -> dict | None:
    try:
        d = json.loads((ws / ".loadn" / "skill-suggest.json").read_text(
            encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


@router.get("/sessions/{sid}/skill-suggest")
async def get_suggest(sid: str):
    """当前待决策建议卡（无 pending → {pending: null}）。

    ws_root 一并返回（2026-10-12 卡片透明化）：卡片展示「将写入的文件
    完整路径」——相对路径用户无从对照，绝对路径才可核查/事后编辑。

    LLM 精判（2026-11-02 用户定调「字符串判定多不靠谱，找低成本模型」）：
    引擎正则只是粗筛（零成本拦掉绝大多数无关轮），出卡前用火山方舟
    doubao-mini（titlegen 同通道）判定真伪——判 reject / 调用失败 →
    卡不出现（fail-closed，用户诉求「不准就别出」）；判真 → 回写
    adjudicated + LLM 提炼规则（替换 body 规则节）。titlegen 未配置的
    部署自动回落正则行为（不自建依赖）。
    """
    ws = _ws_of(sid)
    card = _pending(ws)
    if card and not card.get("adjudicated"):
        verdict = await _llm_adjudicate(card)
        if verdict is None:                    # 未配置 ark：维持正则行为
            return {"pending": card, "ws_root": str(ws)}
        if verdict.get("verdict") not in ("teach", "correct"):
            consolidate.clear_pending(ws)      # reject 或调用失败：fail-closed
            return {"pending": None, "ws_root": str(ws)}
        card["kind"] = verdict["verdict"]
        rule = (verdict.get("rule") or "").strip()
        if rule:
            old = card.get("body") or ""
            head = "## 应遵循的规则"
            if head in old:
                pre = old.split(head, 1)[0].rstrip() + "\n\n"
                card["body"] = f"{pre}{head}\n\n{rule[:200]}"
            else:
                card["body"] = f"{old.rstrip()}\n\n{head}\n\n{rule[:200]}"
        card["adjudicated"] = True
        # 竞态保护：判定耗时期间用户可能已决策（decide 清了 pending）——
        # 文件已不在就不再回写（防复活已拒绝的卡）
        pp = ws / ".loadn" / "skill-suggest.json"
        if not pp.exists():
            return {"pending": None, "ws_root": str(ws)}
        (ws / ".loadn").mkdir(parents=True, exist_ok=True)
        pp.write_text(
            json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"pending": card, "ws_root": str(ws)}


_ADJUDICATE_SYSTEM = (
    "你判断「用户发给 AI 助手的一条消息」是否是对助手行为的纠正/规则教导，"
    "并评估可复用价值。只输出一行 JSON（不要解释），必须含全部三键：\\n"
    '{"verdict":"teach|correct|reject","rule":"≤40字规则，reject为空","reusable":true|false}\\n'
    "判定规则：\\n"
    "- teach=明确教导持续规则（以后都/记住/今后一律…）\\n"
    "- correct=对助手上一轮行为的明确否定并要求改正（不对/错了/重做…）\\n"
    "- reject=以下任一：转贴的日志/代码/报错文本、巡检或定时任务等系统注入、"
    "对外部事物的问题描述（错的是别的东西不是助手）、与助手行为无关的闲聊、"
    "含纠正字样但无纠正意图的叙述。拿不准判 reject。\\n"
    "- reusable（判 teach/correct 时必须给 false 或 true，不可省略）：该经验"
    "能否泛化到未来其他任务。例：\\n"
    '  用户说「不对，应该先建 venv 再装依赖」→ {"verdict":"correct",'
    '"rule":"先建venv再pip","reusable":true}（通用纪律）\\n'
    '  用户说「我是想让你排查X，不是让你去做Y」→ {"verdict":"correct",'
    '"rule":"","reusable":false}（会话内意图澄清，歧义一次性，新会话'
    "不会再遇同样场景）\\n"
    "  以下均 reusable=false：①意图澄清（「我想让你做 X 不是 Y」）；"
    "②仅本任务专属细节（「这个任务里不要动某文件」）；③未来遇不到的"
    "一次性场景。reusable=false 与 reject 等效（不值得存为技能）。"
)


async def _llm_adjudicate(card: dict) -> dict | None:
    """ark/doubao-mini 精判（titlegen 同配置）。未配置返回 None（回落正则）；
    配置了但调用失败返回 {"verdict": "reject"}（fail-closed：不出卡）。"""
    from ...config import CONFIG
    cfg = CONFIG.titlegen
    if not (cfg.enabled and cfg.api_key):
        return None
    trig = str(card.get("trigger") or "")[:600]
    if not trig.strip():
        return {"verdict": "reject"}
    body = str(card.get("body") or "")
    prev = ""
    if "## 上一轮行为" in body:
        prev = body.split("## 上一轮行为", 1)[1].split("##", 1)[0].strip()[:300]
    user = f"用户消息：\n{trig}\n\n助手上一轮输出（尾部）：\n{prev or '(无)'}"
    import httpx
    base = cfg.api_base.rstrip("/").removesuffix("/chat/completions")
    try:
        async with httpx.AsyncClient(timeout=8) as cl:
            r = await cl.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={"model": cfg.model, "max_tokens": 120, "temperature": 0,
                      "messages": [
                          {"role": "system", "content": _ADJUDICATE_SYSTEM},
                          {"role": "user", "content": user}]})
            r.raise_for_status()
            raw = str((r.json().get("choices") or [{}])[0]
                      .get("message", {}).get("content") or "")
        import re as _re
        m = _re.search(r"\{[^{}]*\}", raw, _re.S)
        if not m:
            return {"verdict": "reject"}
        d = json.loads(m.group(0))
        if d.get("verdict") in ("teach", "correct"):
            # 价值门（VA）：判真还须可复用——意图澄清/一次性任务细节
            # （reusable=false）视同 reject（生产实证：用户纠正「我是想让你
            # 排查名字问题不是召唤子代理」被提炼成 avoid-lesson 存技能——
            # 会话内歧义零跨会话价值，用户「教导完全没有价值」的直接来源）。
            # 缺 reusable 字段亦 fail-closed（真机实测 doubao-mini 会漏键，
            # 默认放行=价值门失效；误杀真教导的代价=少一张卡，可接受）。
            if str(d.get("reusable", "")).lower() not in ("true", "1", "yes"):
                return {"verdict": "reject"}
            return {"verdict": d["verdict"], "rule": str(d.get("rule") or "")}
        return {"verdict": "reject"}
    except Exception:                            # 判定失败 fail-closed（不出卡）
        return {"verdict": "reject"}


@router.post("/sessions/{sid}/skill-suggest/decide")
def decide_suggest(sid: str, body: dict):
    """确认（写 .agents/skills/ 过扫描）/ 拒绝（负样本 7 天抑制）。"""
    ws = _ws_of(sid)
    card = _pending(ws)
    if card is None:
        raise HTTPException(404, "没有待决策的技能建议")
    accept = bool(body.get("accept"))
    if accept:
        name = str(body.get("name") or card.get("name") or "").strip()
        desc = str(body.get("description") or card.get("description") or "").strip()
        body_text = str(body.get("body") or card.get("body") or "").strip()
        import re as _re
        if not _re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", name or ""):
            raise HTTPException(400, "技能名需匹配 ^[a-z0-9][a-z0-9._-]{0,63}$")
        if not body_text:
            raise HTTPException(400, "正文不能为空")
        # 二轮修#16：description/origin 单行化（换行会串键/注入伪造键）
        desc = " ".join((desc or name).split())
        origin = f"user-taught:{card.get('fingerprint')}"
        skill_md = (f"---\nname: {name}\ndescription: {desc}\n"
                     f"origin: {origin}\n---\n\n{body_text}\n")
        skill_dir = ws / ".agents" / "skills" / name
        if skill_dir.exists():
            raise HTTPException(409, f"已存在同名 skill: {name}")
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        # 供应链扫描（W4 八类）——二轮修#4：**先扫后写库副本**（原顺序
        # 库先落盘，红线时只清工作区目录——红线内容留在平台技能库任意
        # 后续会话可挂载执行）
        from ...security import skill_scan
        report = skill_scan.scan_skill(skill_dir)
        if report["level"] == "red":
            import shutil
            shutil.rmtree(skill_dir, ignore_errors=True)
            audit("consolidate", {"action": "scan_rejected", "sid": sid,
                                  "name": name})
            raise HTTPException(400, f"扫描红线拒写："
                                     f"{[x['rule'] for x in report['findings'] if x['level'] == 'red'][:3]}")
        # ②平台技能库（跨会话持久）：扫描已过才写；同名冲突用指纹后缀
        # 循环找空位（-taught 固定后缀二次冲突会静默覆盖第一份——二轮修#4b）
        from ... import skills as platform_skills
        lib_root = platform_skills._writable_root()
        lib_dir = lib_root / name
        fp = str(card.get("fingerprint") or "")[:8]
        for cand in (name, f"{name}-taught", f"{name}-{fp}"):
            lib_dir = lib_root / cand
            if not lib_dir.exists():
                break
        else:
            raise HTTPException(409, f"技能库同名冲突无法落位: {name}")
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        import json as _json
        (lib_dir / ".loadn-source.json").write_text(_json.dumps({
            "via": "user-taught", "sid": sid,
            "fingerprint": card.get("fingerprint")}, ensure_ascii=False),
            encoding="utf-8")
        consolidate.clear_pending(ws)
        audit("consolidate", {"action": "accepted", "sid": sid, "name": name,
                              "kind": card.get("kind"),
                              "fingerprint": card.get("fingerprint")})
        return {"ok": True, "skill": name, "scanned": report["level"],
                "library": lib_dir.name}
    # 误判（「这不是教导」）：只清槽+审计，不记负样本（误判指纹无抑制
    # 语义——2026-10-10 用户反馈：误判走「拒绝」会留下无效 7 天抑制）
    if body.get("misjudge"):
        consolidate.clear_pending(ws)
        audit("consolidate", {"action": "misjudged", "sid": sid,
                              "kind": card.get("kind"),
                              "fingerprint": card.get("fingerprint")})
        return {"ok": True, "misjudged": True}
    # 拒绝 → 负样本
    consolidate.reject(str(card.get("fingerprint") or ""))
    consolidate.clear_pending(ws)
    audit("consolidate", {"action": "rejected", "sid": sid,
                          "kind": card.get("kind"),
                          "fingerprint": card.get("fingerprint")})
    return {"ok": True, "rejected": True}


# ---------------------------------------------------------------- draft 转正
@router.post("/memory/entry/{domain}/{eid}/confirm")
def confirm_draft(domain: str, eid: str, body: dict = None):
    """P12 draft 教训转正（去 draft 标记；正文可同请求更新）。"""
    from loadn import memorystore as mstore

    from ...config import PATHS  # noqa: F401
    d = mstore.domain_dir_by_key(domain)
    if d is None:
        raise HTTPException(404, f"记忆域不存在: {domain}")
    entries = mstore.entries_of(d)
    e = next((x for x in entries if x.get("id") == eid), None)
    if e is None:
        raise HTTPException(404, f"条目不存在: {eid}")
    if not e.get("draft"):
        return {"ok": True, "already": True}
    body = body or {}
    content = str(body.get("content") or e.get("content") or "").strip()
    try:
        mstore.edit_entry(d, eid, content=content + "\n" if content else None,
                          event_sink=lambda t, p: audit(
                              "consolidate", {"action": "lesson_confirmed",
                                              "domain": domain, **p}))
    except mstore.MemoryOpError as ex:
        raise HTTPException(400, str(ex)) from None
    # edit 保存即转正：manifest 里 draft 标记随保存移除
    return {"ok": True, "id": eid}
