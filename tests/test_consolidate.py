"""P12 经验→技能固化闭环（fake provider，零 token）：验收五件。

①教学句式触发（teach/correct 两类；普通对话不触发）
②普通对话不触发
③确认后技能存在且下轮索引可见（.agents/skills/ 过扫描、frontmatter source）
④拒绝负样本生效（同类指纹 7 天抑制，引擎侧同文件）
⑤反思产物进记忆域 draft（带标记不转正；转正=编辑保存去 draft）
外加：每会话 ≤2 次防自激；扫描红线拒写；审计入账。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import tests.helpers as H
from loadn import consolidate as cs
from loadn.core.loop import AgentCore, LoopSettings
from loadn.core.session import SessionManager


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "eng_home"))
    monkeypatch.setenv("LOADN_WEBUI_HOME", str(tmp_path / "web_home"))
    yield


def _audit_actions() -> list[str]:
    from loadn_webui.config import PATHS
    p = PATHS["var"] / "audit.db"
    if not p.exists():
        return []
    out = []
    with sqlite3.connect(p) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'audit_events%'")]
        for t in sorted(tables):
            for r in c.execute(f"SELECT detail_json FROM {t} "
                               "WHERE type='consolidate' ORDER BY rowid"):
                out.append(json.loads(r[0]).get("action"))
    return out


async def _turn(tmp_path, user_text: str, rounds=None):
    session = SessionManager.create(tmp_path, home=tmp_path / "eng_home")
    core = AgentCore(provider=H.ScriptedProvider(rounds or [H.text_round("好")]),
                     tools={}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=2))
    await core.run_turn(user_text, emit=lambda e: None)
    for _ in range(10):
        import asyncio
        await asyncio.sleep(0)      # 让 _consolidate_check 任务跑完
    return session


# ---------------------------------------------------------------- ①② 检测
async def test_teach_triggers_and_plain_does_not(tmp_path):
    sess = await _turn(tmp_path, "以后都先跑测试再提交代码")
    card = json.loads((tmp_path / ".loadn" / "skill-suggest.json").read_text())
    assert card["kind"] == "teach" and card["fingerprint"]
    assert card["body"].startswith("# 用户教导（teach）")
    assert "先跑测试再提交代码" in card["body"]     # 用户原话不改写
    assert any(e["type"] == "skill_suggest"
               for e in sess.transcript.read_events())
    # ② 普通对话不触发（新会话）
    tmp2 = tmp_path / "plain"
    tmp2.mkdir()
    sess2 = await _turn(tmp2, "今天天气不错，帮我看看日程")
    assert not (tmp2 / ".loadn" / "skill-suggest.json").exists()
    # 纠错类也触发
    tmp3 = tmp_path / "corr"
    tmp3.mkdir()
    await _turn(tmp3, "这样不对，重来一遍")
    card3 = json.loads((tmp3 / ".loadn" / "skill-suggest.json").read_text())
    assert card3["kind"] == "correct" and card3["name"].startswith("avoid-")


async def test_max_two_per_session_and_single_slot(tmp_path):
    session = await _turn(tmp_path, "以后都先跑测试再提交",
                          [H.text_round("好"), H.text_round("好")])
    from loadn import consolidate as c2
    assert c2.suggest_count(session) == 1
    # 单槽保护（复查修#7）：pending 未决策时同类再触发不覆盖、不再计数
    assert c2.maybe_suggest(tmp_path, session) is None
    assert c2.suggest_count(session) == 1
    # 决策（清 pending）后第二次可触发
    c2.clear_pending(tmp_path)
    session.append_user("以后都用 ruff")
    assert c2.maybe_suggest(tmp_path, session) is not None
    assert c2.suggest_count(session) == 2
    # 计数达 2 → 第三次被拒（防自激上限）
    c2.clear_pending(tmp_path)
    session.append_user("记住要写日志")
    assert c2.maybe_suggest(tmp_path, session) is None


# ---------------------------------------------------------------- ③ 确认→技能
async def test_accept_creates_skill_visible_next_turn(client):
    from loadn_webui.config import PATHS
    r = await client.post("/api/sessions", json={"title": "P12"})
    sid = r.json()["session"]["id"]
    ws = PATHS["workspace"] / sid
    card = {"kind": "teach", "fingerprint": "fp123", "name": "run-tests-first",
            "description": "先测后交", "body": "# 规则\n先跑测试",
            "origin_session": sid, "created_at": "now"}
    (ws / ".loadn").mkdir(parents=True, exist_ok=True)
    (ws / ".loadn" / "skill-suggest.json").write_text(json.dumps(card))
    r = await client.post(f"/api/sessions/{sid}/skill-suggest/decide",
                          json={"accept": True})
    assert r.status_code == 200, r.text
    assert r.json()["skill"] == "run-tests-first"
    md = (ws / ".agents" / "skills" / "run-tests-first" / "SKILL.md").read_text()
    assert "name: run-tests-first" in md and "先跑测试" in md
    assert not (ws / ".loadn" / "skill-suggest.json").exists()   # pending 清
    # 下轮索引可见：引擎 discover 认 .agents/skills（P1）
    from loadn.core import trust
    from loadn.core.skills import discover_skills
    trust.admit(ws)
    assert "run-tests-first" in discover_skills(ws)
    assert "accepted" in _audit_actions()
    # 坏名/空正文拒
    card["name"], card["body"] = "Bad Name", ""
    (ws / ".loadn" / "skill-suggest.json").write_text(json.dumps(card))
    assert (await client.post(f"/api/sessions/{sid}/skill-suggest/decide",
                              json={"accept": True})).status_code == 400


# ---------------------------------------------------------------- ④ 拒绝负样本
async def test_reject_suppresses_same_fingerprint(client, tmp_path):
    from loadn_webui.config import PATHS
    r = await client.post("/api/sessions", json={"title": "P12 拒"})
    sid = r.json()["session"]["id"]
    ws = PATHS["workspace"] / sid
    card = {"kind": "teach", "fingerprint": cs.fingerprint("以后都写日志"),
            "name": "keep-log", "description": "d", "body": "b",
            "origin_session": sid, "created_at": "now"}
    (ws / ".loadn").mkdir(parents=True, exist_ok=True)
    (ws / ".loadn" / "skill-suggest.json").write_text(json.dumps(card))
    r = await client.post(f"/api/sessions/{sid}/skill-suggest/decide",
                          json={"accept": False})
    assert r.status_code == 200 and r.json()["rejected"]
    assert cs.is_rejected(cs.fingerprint("以后都写日志"))       # 引擎侧同文件
    assert not cs.is_rejected("unrelated-fp")
    # 同类句式再来 → 引擎 maybe_suggest 直接 None（7 天抑制）
    plain = tmp_path / "suppressed"
    plain.mkdir()
    session = SessionManager.create(plain, home=tmp_path / "eng_home")
    session.append_user("以后都写日志")
    assert cs.maybe_suggest(plain, session) is None
    assert "rejected" in _audit_actions()


# ---------------------------------------------------------------- ⑤ 反思 draft
async def test_reflection_draft_not_auto_promoted(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_REFLECT_AFTER_COMPACT", "on")
    assert cs.reflection_enabled()
    from loadn.providers import Chunk
    lessons = ('[{"summary": "提交前先跑测试", "content": "同类任务先 pytest 再提交"},'
               ' {"summary": "日志用中文", "content": "运维日志写中文"}]')
    provider = H.ScriptedProvider([
        [Chunk(kind="text_delta", text=lessons),
         Chunk(kind="stop", usage={"input_tokens": 20, "output_tokens": 10},
               stop_reason="end_turn", model="fake")],
    ])
    session = SessionManager.create(tmp_path, home=tmp_path / "eng_home")
    n = await cs.reflect_after_compact(provider, tmp_path, session,
                                       "会话压缩摘要：干了 A/B/C")
    assert n == 2
    from loadn import memorystore as ms
    entries = ms.load_entries(tmp_path)
    assert len(entries) == 2
    assert all(e.get("draft") for e in entries)      # ⑤ draft 标记不转正
    assert any(e["type"] == "lesson_draft"
               for e in session.transcript.read_events())
    # 转正=编辑保存（draft 去除）——经 confirm API
    d = ms.memory_dir(tmp_path)
    eid = entries[0]["id"]
    ms.edit_entry(d, eid, content=entries[0]["content"])
    after = ms.load_entries(tmp_path)
    target = next(e for e in after if e["id"] == eid)
    assert not target.get("draft")                   # 保存即转正
    # 反思默认 off（防自激的另一半：off 时不跑）
    monkeypatch.setenv("LOADN_REFLECT_AFTER_COMPACT", "off")
    assert not cs.reflection_enabled()


def test_mutation_blind_spots(tmp_path, monkeypatch):
    """突变盲区补杀：长文本(>2000)不触发；空 assistant 事件不炸；
    反思条数上限 3（>3 截断）；超长 slug 截断到合法长度。"""
    assert cs.detect_correction("好" * 2001) is None        # 长文本守卫
    assert cs.detect_correction("") is None                  # 空文本
    # 反思 >3 条只写 3
    from loadn.providers import Chunk
    monkeypatch.setenv("LOADN_REFLECT_AFTER_COMPACT", "on")
    lessons = json.dumps([
        {"summary": f"教训{i}", "content": f"行为{i}"} for i in range(6)])
    provider = H.ScriptedProvider([
        [Chunk(kind="text_delta", text=lessons),
         Chunk(kind="stop", usage={"input_tokens": 20}, stop_reason="end_turn",
               model="fake")]])
    session = SessionManager.create(tmp_path, home=tmp_path / "eng_home")
    import asyncio
    n = asyncio.run(cs.reflect_after_compact(provider, tmp_path, session, "S"))
    assert n == 3                                            # 上限截断
    from loadn import memorystore as ms
    assert len(ms.load_entries(tmp_path)) == 3
    # slug 长截断仍合法
    name = cs._slug("teach", " ".join(f"词{i}字" for i in range(30)))
    import re as _re
    assert _re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", name)


# ---------------------------------------------------------------- 复查修复对赌
async def test_fix_detection_with_tool_results(tmp_path):
    """复查修#2 对赌：教学句后跟 tool_result 事件（真实带工具 turn 形态）
    仍能命中——原实现 users[-1] 恒为 tool_result 导致检测失明。"""
    session = SessionManager.create(tmp_path, home=tmp_path / "eng_home")
    session.append_user("以后都先跑测试再提交代码")
    session.append_event("user", {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]})
    out = cs.maybe_suggest(tmp_path, session)
    assert out is not None and out["kind"] == "teach"


async def test_fix_skill_dual_write_to_library(client):
    """复查修#5 对赌：确认后双写——会话工作区（本会话立即生效）+ 平台
    技能库（跨会话持久，管理面可见）。"""
    from loadn_webui import skills as platform_skills
    from loadn_webui.config import PATHS
    r = await client.post("/api/sessions", json={"title": "双写"})
    sid = r.json()["session"]["id"]
    ws = PATHS["workspace"] / sid
    card = {"kind": "teach", "fingerprint": "fpdw", "name": "dual-write-skill",
            "description": "d", "body": "内容", "origin_session": sid,
            "created_at": "now"}
    (ws / ".loadn").mkdir(parents=True, exist_ok=True)
    (ws / ".loadn" / "skill-suggest.json").write_text(json.dumps(card))
    r = await client.post(f"/api/sessions/{sid}/skill-suggest/decide",
                          json={"accept": True})
    assert r.status_code == 200 and r.json().get("library")
    # ①工作区
    assert (ws / ".agents" / "skills" / "dual-write-skill" / "SKILL.md").exists()
    # ②平台技能库（available 可见——跨会话）
    names = {s["name"] for s in platform_skills.available()}
    assert "dual-write-skill" in names
