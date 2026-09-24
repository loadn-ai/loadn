"""P1-7 验收：per-model prompt 变体 + 引擎侧标题生成。

- 变体：glm-5.3 加载调校版（含 GLM 特性段）；[1m] 归一化命中；未知
  slug 回退 generic；ContextAssembler(model=…) 传导
- prompt 外置纪律：代码内无长 prompt 字符串（generic.md 等文件承载）
- 标题：fake provider 首 turn 后 db 出现标题；第二 turn 不再生成（一次性）；
  已有标题不覆盖；平台托管（LOADN_SESSION_ID）跳过
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tests.helpers as H
from loadn.core.context import ContextAssembler, _core_prompt_for
from loadn.core.loop import AgentCore, LoopSettings
from loadn.providers import Chunk
from loadn.providers.fake import _fake_model, _stop_chunk


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOADN_SESSION_ID", raising=False)
    yield


# ---------------------------------------------------------------- 变体
def test_variant_selection():
    assert "GLM 通道特性" in _core_prompt_for("glm-5.3")
    assert "GLM 通道特性" in _core_prompt_for("glm-5.3[1m]")   # slug 归一化
    fallback = _core_prompt_for("never-heard-of-9")
    assert "工程 agent" in fallback and "GLM 通道特性" not in fallback
    assert _core_prompt_for(None) == _core_prompt_for("anything")


def test_assembler_model_param(tmp_path):
    out = ContextAssembler(tmp_path, tools=[], model="glm-5.3").build()
    assert "GLM 通道特性" in out
    out2 = ContextAssembler(tmp_path, tools=[]).build()
    assert "GLM 通道特性" not in out2


def test_core_prompt_externalized():
    """纪律（本卡范围）：CORE_PROMPT 内嵌串已外置——context.py 无
    三引号 prompt。plan/subagent 旧内嵌属相邻问题，记 backlog。"""
    import subprocess
    r = subprocess.run(
        ["grep", "-c", 'CORE_PROMPT = """', "loadn/core/context.py"],
        capture_output=True, text=True)
    assert r.stdout.strip() == "0"
    assert (Path(__file__).resolve().parent.parent
            / "loadn/prompts/models/generic.md").exists()


# ---------------------------------------------------------------- 标题
def _title_round(title: str) -> list[Chunk]:
    return [Chunk(kind="text_delta", text=title),
            _stop_chunk({"input_tokens": 30, "output_tokens": 5},
                        "end_turn", _fake_model())]


async def test_first_turn_generates_title_once(tmp_path):
    # 侧道（记忆抽取/标题）都从同一 ScriptedProvider 取轮——首 turn 后有
    # 两个后台消费，第二 turn 后还有一个；全部轮给标题文本（抽取 JSON
    # 解析失败无害，标题轮命中即写库）
    provider = H.ScriptedProvider([
        _title_round("好的，开始处理部署脚本"),
        _title_round("修复登录超时问题"),
        _title_round("修复登录超时问题"),
        _title_round("再加一个重试的主轮回复"),
        _title_round("修复登录超时问题"),
    ])
    session = __import__("loadn.core.session", fromlist=[
        "SessionManager"]).SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider, tools={}, session=session,
                     cwd=tmp_path, settings=LoopSettings(max_turns=5))
    await core.run_turn("帮我修复登录接口的超时问题并写测试")
    import asyncio
    for _ in range(10):
        await asyncio.sleep(0)
    from loadn.persistence import db as db_mod
    with db_mod.conn(tmp_path / "home") as c:
        row = c.execute("SELECT title FROM sessions WHERE id=?",
                        (session.session_id,)).fetchone()
    assert row and "超时" in (row["title"] or "")
    # 第二 turn：已有标题 → 不再生成（provider 的标题轮不被消费）
    await core.run_turn("再加一个重试逻辑")
    for _ in range(10):
        await asyncio.sleep(0)
    with db_mod.conn(tmp_path / "home") as c:
        row2 = c.execute("SELECT title FROM sessions WHERE id=?",
                         (session.session_id,)).fetchone()
    assert "重试" not in (row2["title"] or "")          # 标题未被第二 turn 覆盖


async def test_platform_managed_skips(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_SESSION_ID", "plat-123")
    provider = H.ScriptedProvider([_title_round("done")])
    session = __import__("loadn.core.session", fromlist=[
        "SessionManager"]).SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider, tools={}, session=session,
                     cwd=tmp_path, settings=LoopSettings(max_turns=5))
    await core.run_turn("platform 托管会话的标题测试输入")
    import asyncio
    for _ in range(5):
        await asyncio.sleep(0)
    from loadn.persistence import db as db_mod
    with db_mod.conn(tmp_path / "home") as c:
        row = c.execute("SELECT title FROM sessions WHERE id=?",
                        (session.session_id,)).fetchone()
    assert not (row and (row["title"] or "").strip())    # 引擎侧未写


def test_set_title_no_overwrite(tmp_path):
    from loadn.persistence import db as db_mod
    with db_mod.conn(tmp_path / "home") as c:
        db_mod.upsert_session(c, "s1", title="用户手改的标题", cwd=str(tmp_path))
        db_mod.set_title(c, "s1", "自动标题")
        row = c.execute("SELECT title FROM sessions WHERE id='s1'").fetchone()
    assert row["title"] == "用户手改的标题"
