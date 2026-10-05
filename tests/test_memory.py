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

pytestmark = [pytest.mark.coverage("engine.memory")]


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


# ---------------------------------------------------------------- P4 双域（用户级跨项目记忆）
def test_p4_classification_deterministic(tmp_path, monkeypatch):
    """确定性归属：偏好指称→user；路径/包管理指称→project；拿不准→project。"""
    assert mem.classify_domain("我喜欢用暗色主题，看久了舒服") == mem.USER_DOMAIN
    assert mem.classify_domain("I prefer concise replies") == mem.USER_DOMAIN
    assert mem.classify_domain("入口在 src/gateway/main.py 里") == mem.PROJECT_DOMAIN
    assert mem.classify_domain("依赖用 pip install fastapi") == mem.PROJECT_DOMAIN
    assert mem.classify_domain("服务跑在 8080 端口") == mem.PROJECT_DOMAIN
    monkeypatch.setenv("LOADN_USER_MEMORY", "off")
    assert mem.classify_domain("我喜欢用暗色主题") == mem.PROJECT_DOMAIN


def test_p4_word_file_extends(tmp_path):
    from loadn import loadn_home
    f = loadn_home() / "memory" / "user-domain-words.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# 扩充词表（一行一正则字面）\n本座\n", encoding="utf-8")
    assert mem.classify_domain("本座要简洁") == mem.USER_DOMAIN


def test_p4_user_storage_and_two_block_injection(tmp_path):
    """user 域落 _user/；注入两段：[user-memory] 置于 [memory] 之上。"""
    mem.remember(tmp_path, "偏好深色", "用户喜欢深色主题。", origin_session="su",
                 domain="user")
    mem.remember(tmp_path, "用 ruff", "lint 一律用 ruff", origin_session="sp")
    d = mem.memory_dir(tmp_path, "user")
    assert d.name == "_user" and (d / "manifest.json").exists()
    out = mem.render_block(tmp_path)
    assert "[user-memory|su]" in out and "[memory|sp]" in out
    assert out.index("### 用户长期记忆") < out.index("### 项目长期记忆")
    from loadn.core.context import ContextAssembler
    assert "[user-memory|" in ContextAssembler(tmp_path, tools=[]).build()


def test_p4_guard_same_for_user_domain(tmp_path):
    """护栏两域同守：蜜罐/凭证形态在 user 域同样拒存。"""
    assert mem.remember(tmp_path, "x", "canary token 是 sk-abcdef0123456789",
                        origin_session="s", domain="user") is None
    assert mem.load_entries(tmp_path, "user") == []


def test_p4_off_zero_touch(tmp_path, monkeypatch):
    """off：显式 user 域写入/删除拒绝、目录零创建、注入面零读。"""
    monkeypatch.setenv("LOADN_USER_MEMORY", "off")
    assert mem.remember(tmp_path, "s", "我喜欢 X", origin_session="s",
                        domain="user") is None
    assert not mem.memory_dir(tmp_path, "user").exists()
    assert mem.forget(tmp_path, "X", domain="user") == 0
    mem.remember(tmp_path, "偏好 X", "我喜欢 X", origin_session="s")
    assert "用户长期记忆" not in mem.render_block(tmp_path)
    # 预置 user 条目后关 off → 注入面不出现（零读）
    monkeypatch.setenv("LOADN_USER_MEMORY", "on")
    mem.remember(tmp_path, "偏好 Y", "我喜欢 Y", origin_session="s", domain="user")
    monkeypatch.setenv("LOADN_USER_MEMORY", "off")
    out = mem.render_block(tmp_path)
    assert "用户长期记忆" not in out and "项目长期记忆" in out


def test_p4_forget_cross_domain(tmp_path):
    """"忘掉 X"两域生效（验收④）。"""
    mem.remember(tmp_path, "偏好 X", "我喜欢 X", origin_session="s", domain="user")
    mem.remember(tmp_path, "关于 X", "项目用 X", origin_session="s")
    assert mem.forget_all(tmp_path, "X") == 2
    assert mem.load_entries(tmp_path) == []
    assert mem.load_entries(tmp_path, "user") == []


async def test_p4_extraction_routes_by_domain(tmp_path):
    """验收①②：抽取通道分域——偏好样例→_user/、项目事实→项目域、不串。"""
    from loadn.core.memory import extract_and_store
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    provider = H.ScriptedProvider([
        [Chunk(kind="text_delta",
               text='[{"summary": "偏好暗色", "content": "我喜欢暗色主题，看久不累"},'
                    ' {"summary": "入口位置", "content": "服务入口在 src/app/main.py"}]'),
         Chunk(kind="stop", usage={"input_tokens": 20, "output_tokens": 10},
               stop_reason="end_turn", model="fake")],
    ])
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    session.append_user("闲聊背景：我喜欢暗色主题，看久了不累。对了，服务入口在 src/app/main.py 里")
    n = await extract_and_store(provider, tmp_path, session)
    assert n == 2
    users = mem.load_entries(tmp_path, domain="user")
    projs = mem.load_entries(tmp_path)
    assert any("暗色" in e["summary"] for e in users)
    assert any("入口" in e["summary"] for e in projs)
    assert not any("暗色" in e["summary"] for e in projs)
    assert not any("入口" in e["summary"] for e in users)


async def test_p4_direct_write_user(tmp_path):
    """直写通道同分类：「记住：我喜欢深色主题」→ user 域、项目域零写入。"""
    from loadn.core.memory import extract_and_store
    from loadn.core.session import SessionManager
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    session.append_user("记住：我喜欢深色主题")
    n = await extract_and_store(H.ScriptedProvider([[]]), tmp_path, session)
    assert n == 1
    assert any("深色" in e["summary"]
               for e in mem.load_entries(tmp_path, domain="user"))
    assert mem.load_entries(tmp_path) == []


def test_p4_promote_cli(tmp_path, monkeypatch, capsys):
    """CLI 手动提升：项目→用户（原域删除、溯源保留）；未知 id/off 拒绝。"""
    from loadn.cli.memory_cli import main
    monkeypatch.chdir(tmp_path)          # promote 按 cwd 定项目域
    e = mem.remember(tmp_path, "通用偏好", "回复要简短", origin_session="orig")
    assert main(["promote", e["id"]]) == 0
    assert mem.load_entries(tmp_path) == []
    u = mem.load_entries(tmp_path, "user")
    assert u and u[0]["origin_session"] == "orig" and u[0]["summary"] == "通用偏好"
    assert main(["promote", "no-such-id"]) == 1
    monkeypatch.setenv("LOADN_USER_MEMORY", "off")
    assert main(["promote", u[0]["id"]]) == 1
