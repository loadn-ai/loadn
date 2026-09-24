"""P3-5a loadn.ext 协议验收。

- 同名 register_tool 整体替换内置工具（build_agent 全链——不改编擎代码）
- on() 三语义：block（PreToolUse reason 回填）/ output override /
  观察者（None 不干预）；closed 事件集（未知事件 ValueError）
- register_provider：EXTRA_PROVIDERS 生效（build_provider 吃工厂）
- 信任门：项目级 .loadn/extensions/ 未过门整目录跳过；overlay env 生效
- 坏扩展（load 抛异常/缺入口）不炸会话不连坐
- examples/ 全部 10 例可加载可冒烟（CI 即此测试）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from loadn.core import ext as ext_mod

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"


def _ext_dir(tmp_path: Path, name: str, code: str) -> Path:
    d = tmp_path / ".loadn" / "extensions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.py"
    p.write_text(code, encoding="utf-8")
    return p


# ---------------------------------------------------------------- 协议单元
def test_closed_event_set():
    from loadn.core.ext import ExtensionAPI, ExtResult
    r = ExtResult()
    api = ExtensionAPI("t", r)
    api.on("PreToolUse", lambda p: None)
    with pytest.raises(ValueError):
        api.on("NotAnEvent", lambda p: None)          # closed set
    with pytest.raises(TypeError):
        api.on("Stop", "not-callable")


async def test_dispatch_block_and_override():
    from loadn.core.ext import dispatch
    hs = [lambda p: None,                                 # 观察者不干预
          lambda p: {"decision": "block", "reason": "拦"}]
    res = await dispatch(hs, {})
    assert res["decision"] == "block"
    res2 = await dispatch([lambda p: {"output": "改写"}], {})
    assert res2 == {"output": "改写"}
    # handler 抛异常不炸链
    def boom(p):
        raise RuntimeError("boom")
    res3 = await dispatch([boom, lambda p: {"input": {"a": 1}}], {})
    assert res3 == {"input": {"a": 1}}


async def test_dispatch_awaits_coroutine_handler():
    async def h(p):
        return {"decision": "block", "reason": "async 拦"}
    res = await ext_mod.dispatch([h], {})
    assert res["reason"] == "async 拦"


# ---------------------------------------------------------------- 加载面
async def test_register_tool_replaces_builtin(tmp_path, monkeypatch):
    """验收主项：同名扩展工具整体替换内置 Grep（零引擎代码改动）。"""
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ext_mod, "loadn_home",
                        lambda: tmp_path / "home")     # cli build 同源
    (tmp_path / "home" / "extensions").mkdir(parents=True)
    (tmp_path / "home" / "extensions" / "mygrep.py").write_text(
        "class T:\n"
        "    name = 'Grep'\n"
        "    description = 'replacement'\n"
        "    input_schema = {}\n"
        "    async def execute(self, args, ctx):\n"
        "        return 'REPLACED-GREP'\n"
        "def load(ext):\n"
        "    ext.register_tool(T())\n", encoding="utf-8")
    from loadn.core.build import build_agent
    bundle = await build_agent(tmp_path, cfg={"provider": "fake"},
                               enable_mcp=False, enable_task=False)
    assert "REPLACED" not in bundle.core.tools["Grep"].description
    out = await bundle.core.tools["Grep"].execute({}, bundle.core.ctx)
    assert out == "REPLACED-GREP"


def test_trust_gate_project_dir(tmp_path, monkeypatch):
    """项目级扩展未过信任门 → 整目录跳过；门内才加载。"""
    from loadn.core import trust
    _ext_dir(tmp_path, "proj", "def load(ext):\n    raise AssertionError('不该被加载')\n")
    monkeypatch.setattr(ext_mod, "loadn_home", lambda: tmp_path / "home")
    # 未 admit：gate 不过 → 项目级不加载
    monkeypatch.setattr(trust, "gate", lambda cwd: (False, "unconfirmed-resources"))
    res = ext_mod.load_extensions(tmp_path)
    assert res.loaded == []
    # 过门：加载（此例 load 抛异常 → 降级跳过，仍不炸）
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    res2 = ext_mod.load_extensions(tmp_path)
    assert res2.loaded == [] and "proj" not in res2.loaded


def test_bad_extension_isolated(tmp_path, monkeypatch):
    from loadn.core import trust
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    monkeypatch.setattr(ext_mod, "loadn_home", lambda: tmp_path / "home")
    _ext_dir(tmp_path, "bad", "raise RuntimeError('坏扩展')\n")
    _ext_dir(tmp_path, "noentry", "X = 1\n")           # 无 load 入口
    _ext_dir(tmp_path, "good", "def load(ext):\n    pass\n")
    res = ext_mod.load_extensions(tmp_path)
    assert res.loaded == ["good"]                       # 坏的不连坐


def test_overlay_env(tmp_path, monkeypatch):
    from loadn.core import trust
    monkeypatch.setattr(trust, "gate", lambda cwd: (False, "no-resources"))
    monkeypatch.setattr(ext_mod, "loadn_home", lambda: tmp_path / "home")
    ov = tmp_path / "ov"
    ov.mkdir()
    (ov / "ovl.py").write_text("def load(ext):\n    pass\n", encoding="utf-8")
    monkeypatch.setenv("LOADN_EXT_EXTRA", str(ov))
    res = ext_mod.load_extensions(tmp_path)
    assert res.loaded == ["ovl"]


def test_register_provider_wired(tmp_path, monkeypatch):
    from loadn.core import trust
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    monkeypatch.setattr(ext_mod, "loadn_home", lambda: tmp_path / "home")
    _ext_dir(tmp_path, "prov",
             "def _f(cfg):\n"
             "    from loadn.providers.fake import FakeProvider\n"
             "    return FakeProvider()\n"
             "def load(ext):\n"
             "    ext.register_provider('mine', _f)\n")
    res = ext_mod.load_extensions(tmp_path)
    assert "mine" in res.providers
    from loadn import providers as prov_mod
    for name, factory in res.providers.items():
        prov_mod.register_provider(name, factory)
    try:
        p = prov_mod.build_provider({"provider": "mine"})
        assert type(p).__name__ == "FakeProvider"
    finally:
        prov_mod.EXTRA_PROVIDERS.pop("mine", None)


# ---------------------------------------------------------------- 全链
async def test_pre_tool_use_block_full_chain(tmp_path, monkeypatch):
    """扩展 block：Edit 被拦，reason 回填模型（钩子阻止文案）。"""
    import tests.helpers as H
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    from loadn.core import trust
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    monkeypatch.setattr(ext_mod, "loadn_home", lambda: tmp_path / "home")
    _ext_dir(tmp_path, "gate",
             "def _h(p):\n"
             "    if p.get('tool') == 'Edit':\n"
             "        return {'decision': 'block', 'reason': '扩展禁止编辑'}\n"
             "def load(ext):\n"
             "    ext.on('PreToolUse', _h)\n")
    ext = ext_mod.load_extensions(tmp_path)
    assert "PreToolUse" in ext.handlers

    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk
    from loadn.tools.base import ToolContext
    from loadn.tools.edit import EditTool
    from loadn.tools.read import ReadTool

    f = tmp_path / "a.txt"
    f.write_text("x\n", encoding="utf-8")
    provider = H.ScriptedProvider([
        H.tool_round("t1", "Read", {"file_path": str(f)}),
        H.tool_round("t2", "Edit", {"file_path": str(f),
                                     "old_string": "x", "new_string": "y"}),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 10, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider,
                     tools={"Read": ReadTool(), "Edit": EditTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=6),
                     ctx=ToolContext(cwd=tmp_path))
    ext_mod.apply_handlers(core.hooks, ext.handlers)
    await core.run_turn("编辑")
    ts = (tmp_path / "home" / "sessions" / session.session_id
          / "transcript.jsonl").read_text()
    assert "扩展禁止编辑" in ts                     # block reason 回填留痕
    assert f.read_text() == "x\n"                    # 写入被拦


async def test_post_tool_use_override_full_chain(tmp_path, monkeypatch):
    import tests.helpers as H
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    from loadn.core import trust
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    monkeypatch.setattr(ext_mod, "loadn_home", lambda: tmp_path / "home")
    _ext_dir(tmp_path, "upper",
             "def _h(p):\n"
             "    if p.get('tool') == 'Read':\n"
             "        return {'output': str(p.get('output','')).upper()}\n"
             "def load(ext):\n"
             "    ext.on('PostToolUse', _h)\n")
    ext = ext_mod.load_extensions(tmp_path)

    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk
    from loadn.tools.read import ReadTool

    f = tmp_path / "b.txt"
    f.write_text("hello ext\n", encoding="utf-8")
    provider = H.ScriptedProvider([
        H.tool_round("t1", "Read", {"file_path": str(f)}),
        [Chunk(kind="text_delta", text="ok"),
         _stop_chunk({"input_tokens": 10, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider, tools={"Read": ReadTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=4))
    ext_mod.apply_handlers(core.hooks, ext.handlers)
    await core.run_turn("读")
    # 下一轮模型看到的 tool_result 是大写改写
    second = [c for c in provider.calls]
    assert second, "provider 未被调用"
    text = " ".join(
        str(getattr(b, "text", "") or "")
        if isinstance(getattr(b, "text", ""), str) and getattr(b, "text", "")
        else str(getattr(b, "content", "") or "")
        for m in second[-1] for b in m.content)
    assert "HELLO EXT" in text


# ---------------------------------------------------------------- examples 冒烟
def test_all_examples_load_and_smoke():
    """CI 冒烟（验收：示例全可运行）——examples/*.py 全部经真协议加载。"""
    import os

    from loadn.core import trust
    # overlay 指到仓内 examples/（trust 门 mock 过——examples 自身无害）
    old = os.environ.get("LOADN_EXT_EXTRA")
    os.environ["LOADN_EXT_EXTRA"] = str(EXAMPLES)
    try:
        gate_orig = trust.gate
        trust.gate = lambda cwd: (False, "no-resources")
        try:
            res = ext_mod.load_extensions(REPO)
        finally:
            trust.gate = gate_orig
    finally:
        if old is None:
            os.environ.pop("LOADN_EXT_EXTRA", None)
        else:
            os.environ["LOADN_EXT_EXTRA"] = old
    names = sorted(p.stem for p in EXAMPLES.glob("*.py"))
    assert names == ["audit_log", "block_secret_write", "custom_provider",
                     "git_checkpoint", "hello_tool", "permission_gate",
                     "replace_grep", "todo_guard", "turn_notifier",
                     "uppercase_read"]
    assert sorted(res.loaded) == names                 # 10 例全部加载成功
    # 注册面产物健全
    assert "Grep" in res.tools and res.tools["Hello"].name == "Hello"
    assert "shout" in res.providers
    assert set(res.handlers) == {"PreToolUse", "PostToolUse", "Stop"}
    # block 语义抽检（permission_gate——按序问全部 PreToolUse handler）
    payload = {"tool": "Bash", "input": {"command": "rm -rf / else"}}
    verdict = next((v for v in (h(payload) for h in
                                res.handlers["PreToolUse"])
                    if isinstance(v, dict)), None)
    assert verdict and verdict["decision"] == "block"


async def test_replace_grep_example_behavior(tmp_path):
    """examples/replace_grep：同名替换后行为=内置转发+留痕。"""
    import os

    from loadn.core import trust
    from loadn.tools.base import ToolContext
    os.environ["LOADN_EXT_EXTRA"] = str(EXAMPLES)
    gate_orig = trust.gate
    trust.gate = lambda cwd: (False, "no-resources")
    try:
        res = ext_mod.load_extensions(tmp_path)
    finally:
        trust.gate = gate_orig
        os.environ.pop("LOADN_EXT_EXTRA", None)
    grep = res.tools["Grep"]
    (tmp_path / "h.txt").write_text("needle here\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    out = await grep.execute({"pattern": "needle", "path": str(tmp_path)}, ctx)
    assert "h.txt" in str(out)
    assert (tmp_path / ".loadn" / "ext-grep.log").exists()   # 留痕生效
