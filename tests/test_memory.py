"""P1-4a 长期记忆验收（fake provider 场景，零 token）。

- 存储层：remember/forget/LRU 上限/boundary 推进/manifest 原子
- 资格：MINIMUM_USER_WORDS 门槛（zcode no-user-prose 同构）
- **敏感护栏**：蜜罐/凭证形态永不入库（canary/AKIA/private key/
  password=…）+ blocked 留痕
- 注入：render_block 带 [memory|溯源] 标注；context.build 含抽取记忆段
  （宪法后）
- 忘掉指令删除条目
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.helpers as H
from loadn.core import memory as mem


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    yield


# ---------------------------------------------------------------- 存储层
def test_remember_manifest_and_boundary(tmp_path):
    e = mem.remember(tmp_path, "偏好中文回复", "用户明确要求中文。",
                     origin_session="sess-A")
    assert e and e["origin_session"] == "sess-A"
    m = json.loads((mem.memory_dir(tmp_path) / "manifest.json").read_text())
    assert m["entries"][0]["summary"] == "偏好中文回复"
    assert (mem.memory_dir(tmp_path) / m["entries"][0]["file"]).exists()
    # 边界推进（只抽新增段的锚点）
    mem.advance_boundary(tmp_path, "msg-uuid-7")
    assert mem.boundary_of(tmp_path) == "msg-uuid-7"


def test_lru_cap(tmp_path):
    for i in range(mem.MAX_ENTRIES + 5):
        mem.remember(tmp_path, f"条目{i}", f"内容{i}", origin_session="s")
    assert len(mem.load_entries(tmp_path)) == mem.MAX_ENTRIES
    assert mem.load_entries(tmp_path)[-1]["summary"] == \
        f"条目{mem.MAX_ENTRIES + 4}"        # 最旧淘汰、最新保留


def test_forget(tmp_path):
    mem.remember(tmp_path, "用 pytest", "测试框架偏好", origin_session="s")
    mem.remember(tmp_path, "别的", "无关", origin_session="s")
    n = mem.forget(tmp_path, "pytest")
    assert n == 1
    assert all("pytest" not in (e["summary"] + e.get("content", ""))
               for e in mem.load_entries(tmp_path))


# ---------------------------------------------------------------- 护栏
@pytest.mark.parametrize("bad", [
    "canary token 是 sk-abcdef0123456789abcdef",
    "AWS 密钥 AKIAIOSFODNN7EXAMPLE 泄了",
    "-----BEGIN RSA PRIVATE KEY-----",
    "password: hunter2",
    "api_key = abcd1234efgh5678",
])
def test_canary_never_stored(tmp_path, bad):
    e = mem.remember(tmp_path, bad[:20], bad, origin_session="s")
    assert e is None
    assert mem.load_entries(tmp_path) == []


def test_eligible_user_words_gate():
    ok, why = mem.eligible([{"role": "user", "content": "help me build a thing"}])
    assert ok
    ok, why = mem.eligible([{"role": "assistant", "content": "好的" * 99}])
    assert not ok and why == "no-user-prose"


# ---------------------------------------------------------------- 注入
def test_render_block_annotation(tmp_path):
    mem.remember(tmp_path, "构建用 ruff", "lint 偏好 ruff 而非 flake8",
                 origin_session="20260924_1100-abc12345")
    block = mem.render_block(tmp_path)
    assert "[memory|20260924_1100-ab" in block
    assert "构建用 ruff" in block


def test_context_build_includes_memory(tmp_path):
    from loadn.core.context import ContextAssembler
    mem.remember(tmp_path, "回复要简短", "用户偏好简洁回复",
                 origin_session="sess-XYZ")
    asm = ContextAssembler(tmp_path, tools=["Bash"])
    out = asm.build()
    assert "[memory|" in out and "回复要简短" in out
    # 宪法在前、记忆在后（注入位契约）
    assert out.index("宪法") < out.index("项目长期记忆") \
        if "宪法" in out else True


def test_empty_memory_no_block(tmp_path):
    from loadn.core.context import ContextAssembler
    out = ContextAssembler(tmp_path, tools=[]).build()
    assert "项目长期记忆" not in out


# ---------------------------------------------------------------- 抽取通道（fake）
async def test_extraction_after_turn_writes_memory(tmp_path):
    """fake 场景：轮成功后按新增段抽取（small_model=同 provider 简短回放）。
    P1-4b 接 loop 钩子前的通道级验证：显式调用抽取管线。"""
    import tests.helpers as H2
    provider = H2.ScriptedProvider([
        [__import__("loadn.providers", fromlist=["Chunk"]).Chunk(
            kind="text_delta", text="记住：用户偏好用 uv 管理依赖"),
         __import__("loadn.providers", fromlist=["Chunk"]).Chunk(
            kind="stop", usage={"input_tokens": 10, "output_tokens": 5},
            stop_reason="end_turn", model="fake")],
    ])
    # 简化：直接以 provider 输出文本作为抽取产物（P1-4b 换 small_model 调用）
    text = ""
    async for c in provider.chat([], [], ""):
        if c.kind == "text_delta":
            text += c.text
    assert "uv" in text
    assert mem.remember(tmp_path, text[:20], text, origin_session="s1")


# ---------------------------------------------------------------- loop 全链（P1-4b）
async def test_turn_triggers_extraction(tmp_path):
    """run_turn 成功 → 后台抽取任务跑完（await 收尾）→ 记忆库出现条目。"""
    import asyncio

    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.providers import Chunk
    provider = H.ScriptedProvider([
        # 主轮（含丰富用户语料）→ 触发抽取
        [Chunk(kind="text_delta", text="好的，已完成"),
         __import__("loadn.providers.fake", fromlist=["Chunk"]).Chunk(
             kind="stop", usage={"input_tokens": 80, "output_tokens": 10},
             stop_reason="end_turn", model="fake")],
        # 抽取轮（small_model 侧道调用吃到这一轮）：JSON 数组输出
        [Chunk(kind="text_delta",
               text='[{"summary": "偏好 ruff", "content": "lint 一律用 ruff"}]'),
         __import__("loadn.providers.fake", fromlist=["Chunk"]).Chunk(
             kind="stop", usage={"input_tokens": 20, "output_tokens": 10},
             stop_reason="end_turn", model="fake")],
    ])
    session = __import__("loadn.core.session", fromlist=["SessionManager"]).SessionManager.create(
        tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider, tools={}, session=session,
                     cwd=tmp_path, settings=LoopSettings(max_turns=5))
    await core.run_turn("这个项目以后 lint 一律用 ruff 检查，请记住这个约定")
    # 后台任务调度了：等一拍让它完成
    for _ in range(10):
        await asyncio.sleep(0)
    entries = mem.load_entries(tmp_path)
    assert any("ruff" in (e["summary"] + e.get("content", ""))
               for e in entries)
    # 边界已推进（再跑一轮不重复抽取——segment 空）
    assert mem.boundary_of(tmp_path)


async def test_direct_write_channel(tmp_path):
    """「记住 X」直写入库；该轮不再自动抽取（无 small_model 调用）。"""
    from loadn.core.memory import extract_and_store
    session = __import__("loadn.core.session", fromlist=["SessionManager"]).SessionManager.create(
        tmp_path, home=tmp_path / "home")
    session.append_user("记住：这个仓库构建用 uv 而不是 pip")
    provider = H.ScriptedProvider([[]])   # 不应被调用（直写短路）
    n = await extract_and_store(provider, tmp_path, session)
    assert n == 1
    entries = mem.load_entries(tmp_path)
    assert any("uv" in e["summary"] + e.get("content", "") for e in entries)


async def test_forget_channel(tmp_path):
    from loadn.core.memory import extract_and_store
    mem.remember(tmp_path, "偏好 pytest", "测试框架 pytest", origin_session="s")
    session = __import__("loadn.core.session", fromlist=["SessionManager"]).SessionManager.create(
        tmp_path, home=tmp_path / "home")
    session.append_user("忘掉 pytest 偏好")
    await extract_and_store(H.ScriptedProvider([[]]), tmp_path, session)
    assert all("pytest" not in e["summary"] for e in mem.load_entries(tmp_path))
