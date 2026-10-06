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
    e = mem.remember(tmp_path, "构建用 ruff", "lint 偏好 ruff 而非 flake8",
                     origin_session="20260924_1100-abc12345")
    block = mem.render_block(tmp_path)
    assert f"[memory:{e['id']}|20260924_1100-ab" in block
    assert "构建用 ruff" in block


def test_context_build_includes_memory(tmp_path):
    from loadn.core.context import ContextAssembler
    mem.remember(tmp_path, "回复要简短", "用户偏好简洁回复",
                 origin_session="sess-XYZ")
    asm = ContextAssembler(tmp_path, tools=["Bash"])
    out = asm.build()
    assert "[memory:" in out and "回复要简短" in out
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
    eu = mem.remember(tmp_path, "偏好深色", "用户喜欢深色主题。",
                      origin_session="su", domain="user")
    ep = mem.remember(tmp_path, "用 ruff", "lint 一律用 ruff", origin_session="sp")
    d = mem.memory_dir(tmp_path, "user")
    assert d.name == "_user" and (d / "manifest.json").exists()
    out = mem.render_block(tmp_path)
    assert f"[user-memory:{eu['id']}|su]" in out
    assert f"[memory:{ep['id']}|sp]" in out
    assert out.index("### 用户长期记忆") < out.index("### 项目长期记忆")
    from loadn.core.context import ContextAssembler
    assert "[user-memory:" in ContextAssembler(tmp_path, tools=[]).build()


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


def test_p4_word_file_malformed_lines(tmp_path):
    """词表文件空行/注释不产生空正则（空交替=一切文本命中 user 的 fail-open）。"""
    from loadn import loadn_home
    f = loadn_home() / "memory" / "user-domain-words.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n\n# 注释行\n\n有效词\n\n", encoding="utf-8")
    assert mem.classify_domain("中性描述一句话") == mem.PROJECT_DOMAIN
    assert mem.classify_domain("有效词出现了") == mem.USER_DOMAIN


# ---------------------------------------------------------------- P5 git 版本化
def _git_out(cwd, domain, *args) -> str:
    r = mem._git(mem.memory_dir(cwd, domain), *args)
    assert r is not None and r.returncode == 0, r.stderr if r else "git None"
    return r.stdout


def test_p5_commit_message_has_session(tmp_path):
    """验收①：写入=一次 commit，message 含一句话摘要与 session id。"""
    mem.remember(tmp_path, "git 化条目", "版本化内容", origin_session="sess-p5")
    log = _git_out(tmp_path, "project", "log", "--oneline")
    assert "memory: git 化条目 [session:sess-p5]" in log
    # 双域各自成仓
    mem.remember(tmp_path, "用户域条目", "我喜欢简洁", origin_session="sess-u",
                 domain="user")
    assert len(_git_out(tmp_path, "user", "log",
                       "--oneline").strip().splitlines()) == 1


def test_p5_lru_and_forget_recoverable(tmp_path, monkeypatch):
    """验收②：LRU 淘汰与忘掉都可通过历史找回（父提交仍在）。"""
    import loadn.memorystore as mstore
    monkeypatch.setattr(mstore, "MAX_ENTRIES", 2)
    e1 = mem.remember(tmp_path, "淘汰候选", "被 LRU 淘汰的内容 X", origin_session="s")
    mem.remember(tmp_path, "留下甲", "内容甲", origin_session="s")
    mem.remember(tmp_path, "留下乙", "内容乙", origin_session="s")   # 触发淘汰 e1
    assert all(e1["id"] != x["id"] for x in mem.load_entries(tmp_path))
    # 已删文件用「新增它的提交」找回（工作区已无此路径）
    add_hash = _git_out(tmp_path, "project", "log", "--diff-filter=A",
                        "--format=%H", "-1", "--", f"{e1['id']}.md").strip()
    assert "被 LRU 淘汰的内容 X" in _git_out(tmp_path, "project", "show",
                                             f"{add_hash}:{e1['id']}.md")
    # 忘掉的条目同样可从历史找回
    e2 = mem.remember(tmp_path, "忘掉候选", "将被忘掉的内容 Y", origin_session="s")
    mem.forget(tmp_path, "忘掉候选")
    add2 = _git_out(tmp_path, "project", "log", "--diff-filter=A",
                    "--format=%H", "-1", "--", f"{e2['id']}.md").strip()
    assert "将被忘掉的内容 Y" in _git_out(tmp_path, "project", "show",
                                           f"{add2}:{e2['id']}.md")


def test_p5_concurrent_20_no_corruption(tmp_path):
    """验收③：20 并发写入——条目不丢、仓不损坏。flock 域锁 + add -A 补提交。"""
    import concurrent.futures

    def _w(i: int):
        return mem.remember(tmp_path, f"并发条目{i}", f"并发内容{i}",
                            origin_session="s-cc") is not None

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        assert all(ex.map(_w, range(20)))
    entries = mem.load_entries(tmp_path)
    assert len(entries) == 20, f"并发丢更新：{len(entries)}/20"
    log = _git_out(tmp_path, "project", "log", "--oneline")
    assert len(log.strip().splitlines()) >= 20
    assert _git_out(tmp_path, "project", "fsck", "--no-progress") == ""


def test_p5_reject_leaves_empty_commit(tmp_path):
    """验收④：护栏拒绝留 --allow-empty 的 reject 提交（不落被拒内容）。"""
    mem.remember(tmp_path, "正常", "先建仓", origin_session="s")
    n0 = len(_git_out(tmp_path, "project", "log", "--oneline").strip().splitlines())
    assert mem.remember(tmp_path, "坏内容", "canary token 是 sk-abcdef0123456789",
                        origin_session="s-bad") is None
    log = _git_out(tmp_path, "project", "log", "--oneline")
    assert "memory: reject 护栏拦截 [session:s-bad]" in log
    assert "sk-abcdef" not in _git_out(tmp_path, "project", "show", "--stat",
                                       "HEAD")   # 被拒内容不入树
    assert len(log.strip().splitlines()) == n0 + 1


def test_p5_git_absent_degrades(tmp_path, monkeypatch):
    """git 不可用：写入不受影响（降级为无版本记忆），开关短路。"""
    import loadn.memorystore as mstore
    monkeypatch.setattr(mstore, "_GIT_DISABLED", True)
    e = mem.remember(tmp_path, "无 git", "仍然能写", origin_session="s")
    assert e is not None
    assert mem.load_entries(tmp_path)
    assert not (mem.memory_dir(tmp_path) / ".git").exists()


def test_p5_cli_log_and_restore(tmp_path, monkeypatch, capsys):
    """CLI 面：log 列史；restore 恢复单条（禁整仓 reset——历史原样追加）。"""
    from loadn.cli.memory_cli import main
    monkeypatch.chdir(tmp_path)
    e = mem.remember(tmp_path, "可恢复条目", "恢复内容 Z", origin_session="s9")
    assert main(["log"]) == 0
    assert "memory: 可恢复条目 [session:s9]" in capsys.readouterr().out
    mem.forget(tmp_path, "可恢复条目")
    assert not mem.load_entries(tmp_path)
    # 写入提交是 log 第二行（第一行是忘掉提交）
    lines = _git_out(tmp_path, "project", "log", "--oneline").strip().splitlines()
    write_hash = lines[1].split()[0]
    assert main(["restore", write_hash]) == 0
    entries = mem.load_entries(tmp_path)
    assert any("可恢复条目" in x["summary"] for x in entries)
    # 历史只增不减（无 reset）：三提交=写入+忘掉+恢复
    n = len(_git_out(tmp_path, "project", "log", "--oneline").strip().splitlines())
    assert n == 3
    # 用户域 log 分仓
    mem.remember(tmp_path, "用户域", "我喜欢 Z", origin_session="su", domain="user")
    assert main(["log", "--domain", "user"]) == 0
    assert "用户域" in capsys.readouterr().out


# ---------------------------------------------------------------- P7 来源标注
async def test_p7_memory_hits_exact_and_stable_id(tmp_path):
    """验收①：注入 N 条后 assistant 消息 memory_hits 恰为注入清单（id/域/
    reason/哈希全等，transcript 与 stdout 事件双落）；稳定 id 同内容幂等。"""
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    eu = mem.remember(tmp_path, "偏好 A", "我喜欢 A", origin_session="su",
                      domain="user", reason="explicit")
    ep = mem.remember(tmp_path, "事实 B", "入口在 src/b.py", origin_session="sp")
    # 稳定 id：同域同溯源同内容重写 → 同 id 原位更新（不重复入库）
    again = mem.remember(tmp_path, "偏好 A", "我喜欢 A", origin_session="su",
                         domain="user", reason="explicit")
    assert again["id"] == eu["id"]
    eu2 = mem.remember(tmp_path, "偏好 C", "我喜欢 C", origin_session="su2",
                       domain="user", reason="explicit")
    mem.remember(tmp_path, "偏好 A", "我喜欢 A", origin_session="su",
                 domain="user", reason="explicit")     # 多条目下仍只更新自身
    users = mem.load_entries(tmp_path, "user")
    assert len([x for x in users if x["id"] == eu["id"]]) == 1
    assert any(x["id"] == eu2["id"] for x in users)    # 邻居不被误替换
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=H.ScriptedProvider([H.text_round("好")]),
                     tools={}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=2))
    events = []
    await core.run_turn("干活", emit=events.append)
    expect = mem.injected_hits(tmp_path)
    assert {eu["id"], ep["id"]} <= {h["id"] for h in expect}
    got_ev = next(e for e in events
                  if e["type"] == "assistant")["memory_hits"]
    assert got_ev == expect                              # stdout 事件透传
    at = [e for e in session.transcript.read_events()
          if e["type"] == "assistant"][-1]["payload"]
    assert at["memory_hits"] == expect                   # JSONL 扩展字段
    reason = {h["id"]: h["reason"] for h in expect}
    assert reason[eu["id"]] == "explicit"
    assert reason[ep["id"]] == "inferred"                # 未标注回落
    for h in expect:                                     # hash=注入时内容指纹
        assert h["hash"] and len(h["hash"]) == 8


async def test_p7_hits_empty_when_no_memory(tmp_path):
    """无记忆会话：assistant 消息不带 memory_hits 字段（旧格式逐字节一致，
    向后兼容——旧记录无此字段读取不报错）。"""
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=H.ScriptedProvider([H.text_round("好")]),
                     tools={}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=2))
    events = []
    await core.run_turn("干活", emit=events.append)
    assert "memory_hits" not in next(e for e in events if e["type"] == "assistant")
    at = [e for e in session.transcript.read_events()
          if e["type"] == "assistant"][-1]["payload"]
    assert "memory_hits" not in at


async def test_r2_single_read_hits_match_render(tmp_path, monkeypatch):
    """二轮修#8 对赌：memory_project 单读共享——select_injected 每 turn
    只调一次，last_memory_hits 与 render_block 渲染的是同一份 picked
    （原两次独立选采在边界处 ids 不一致=命中清单与实际注入脱节）。"""
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    mem.remember(tmp_path, "事实 X", "X 在 a.py", origin_session="s1")
    calls = []
    orig = mem.select_injected

    def counting(cwd, limit=12):
        calls.append(1)
        return orig(cwd, limit)
    monkeypatch.setattr(mem, "select_injected", counting)
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=H.ScriptedProvider([H.text_round("好")]),
                     tools={}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=2))
    events = []
    await core.run_turn("干活", emit=events.append)
    assert calls == [1], f"select_injected 须单次（实际 {len(calls)}）"
    hits = [e for e in events if e["type"] == "assistant"][-1]["memory_hits"]
    block = mem.render_block(tmp_path)
    assert hits and all(h["id"] in block for h in hits), \
        "hits 与渲染块须同一份 picked（行首 id8 均出现于注入块）"


def test_r2_dup_remember_returns_updated_entry(tmp_path):
    """二轮修#14 对赌：dup 原位更新不挪尾——remember 返回的就是被更新的
    条目本身（原 [-1] 在 manifest 尾部是别的条目时返回错对象）。"""
    a = mem.remember(tmp_path, "偏 A", "内容甲", origin_session="s1")
    mem.remember(tmp_path, "别的", "邻居", origin_session="s1")   # 挠尾
    b = mem.remember(tmp_path, "偏 A", "内容甲", origin_session="s1")
    assert b["id"] == a["id"]
    assert b["summary"] == "偏 A" and b["content"] == "内容甲"
    assert b["origin_session"] == "s1"            # 返回的是自身不是邻居


def test_r2_frontmatter_newlines_flattened(tmp_path):
    """二轮修#15/#16 对赌：summary/origin_session 带换行 → 写入前单行化
    （换行可伪造 frontmatter 键/串键——单行后 manifest 行可解析且 eid
    对空白不敏感）。"""
    n = mem.remember(tmp_path, "行1\n行2: 伪造键", "C", origin_session="s\nX")
    assert "\n" not in n["summary"] and "\n" not in (n["origin_session"] or "")
    import json as _j
    mfile = mem.memory_dir(tmp_path) / "manifest.json"
    data = _j.loads(mfile.read_text(encoding="utf-8"))   # 整体可解析
    row = next(x for x in data["entries"] if x["id"] == n["id"])
    assert "\n" not in row["summary"]
    # 幂等：同内容不同空白重抽 → 同 eid 原位更新（不重复入库）
    m2 = mem.remember(tmp_path, "行1  行2: 伪造键", "C", origin_session="s X")
    assert m2["id"] == n["id"]
    assert len([x for x in data["entries"] if x["id"] == n["id"]]) == 1
