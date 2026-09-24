"""P1-8 Repo Map 验收（零 token；tree-sitter 缺席走 degraded 路径）。

- 预算控制：两档预算下地图字符量受控（预算 0 = 关闭）
- 引用图排序：被引多的符号所在文件排前
- mentioned 提权：Read/Edit 过的文件在地图中权重上升（aider 语义）
- mtime 缓存：改文件才失效（缓存命中不计成本——同输入二次调用走缓存）
- degraded 可用：无 tree-sitter 环境 def/class 正则近似仍出地图
- context 注入：with_repomap 时 build 含「仓库地图」节；冷启动 3 轮后停
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("engine.repomap")]

from pathlib import Path

import pytest

from loadn.core import repomap as rm


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LOADN_REPOMAP_TOKENS", "1024")
    rm._TAG_CACHE.clear()
    yield
    rm._TAG_CACHE.clear()


def _mk_repo(tmp: Path):
    (tmp / "core.py").write_text(
        "def hub(): return 1\ndef spoke_a(): return hub()\n"
        "def spoke_b(): return hub() + hub()\n", encoding="utf-8")
    (tmp / "leaf.py").write_text("def lonely(): pass\n", encoding="utf-8")
    (tmp / "docs.md").write_text("# 说明\n一些文档\n", encoding="utf-8")
    return tmp


# ---------------------------------------------------------------- 预算
def test_budget_controls_size(tmp_path):
    _mk_repo(tmp_path)
    big = rm.get_repo_map(tmp_path)
    import os
    os.environ["LOADN_REPOMAP_TOKENS"] = "30"        # 极小预算
    small = rm.get_repo_map(tmp_path)
    assert len(small) < len(big)
    assert len(small) < 30 * rm.CHARS_PER_TOKEN + 80     # 预算内（+头部余量）
    os.environ["LOADN_REPOMAP_TOKENS"] = "0"
    assert rm.get_repo_map(tmp_path) == ""                # 0=关闭


# ---------------------------------------------------------------- 排序
def test_reference_graph_ranking(tmp_path):
    _mk_repo(tmp_path)
    m = rm.get_repo_map(tmp_path)
    assert m.index("core.py") < m.index("leaf.py")     # 被引多的文件在前
    assert "hub(" in m and "lonely(" in m


def test_mentioned_boost(tmp_path):
    _mk_repo(tmp_path)
    plain = rm.get_repo_map(tmp_path)
    mentioned = rm.get_repo_map(tmp_path, mentioned={"leaf.py"})
    # leaf 提权后应排到 core 前（×3 > core 的 hub 引用数 3）
    assert mentioned.index("leaf.py") < mentioned.index("core.py")


# ---------------------------------------------------------------- 缓存
def test_mtime_cache(tmp_path):
    _mk_repo(tmp_path)
    f = tmp_path / "core.py"
    rm._tags_for(f)
    assert str(f) in rm._TAG_CACHE
    entry = rm._TAG_CACHE[str(f)]
    rm._tags_for(f)                                    # 未改：命中缓存
    assert rm._TAG_CACHE[str(f)] is entry
    f.write_text("def changed(): pass\n", encoding="utf-8")
    import os
    os.utime(f, ns=(0, 0))                             # 强制不同 mtime（同秒粒度）
    rm._tags_for(f)                                    # mtime 变：重算
    assert rm._TAG_CACHE[str(f)] is not entry
    assert rm._TAG_CACHE[str(f)][1] == [("changed", 1)]


# ---------------------------------------------------------------- degraded
def test_degraded_map_without_treesitter(tmp_path):
    """无 tree-sitter（本 venv 即无）：def/class 正则近似可用。"""
    assert rm.HAVE_TS is False
    m = rm.get_repo_map(_mk_repo(tmp_path))
    assert "degraded" in m and "hub(" in m


def test_skip_dirs_and_noncode(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / ".git" / "config").parent.mkdir(parents=True)
    (repo / ".git" / "config").write_text("def ghost(): pass")
    m = rm.get_repo_map(repo)
    assert "ghost" not in m                            # 跳过目录不进图
    assert "docs.md" in m                              # md 以摘要行入图


# ---------------------------------------------------------------- context 注入
def test_context_section(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_REPOMAP_TOKENS", "1024")
    from loadn.core.context import ContextAssembler
    _mk_repo(tmp_path)
    out = ContextAssembler(tmp_path, tools=[], with_repomap=True).build()
    assert "仓库地图" in out and "hub(" in out
    out2 = ContextAssembler(tmp_path, tools=[]).build()
    assert "仓库地图" not in out2                      # 默认不带（冷启动 opt-in）


async def test_cold_start_three_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_REPOMAP_TOKENS", "1024")
    import tests.helpers as H
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk

    def r(t): return [Chunk(kind="text_delta", text=t),
                     _stop_chunk({"input_tokens": 30, "output_tokens": 5},
                                 "end_turn", _fake_model())]
    _mk_repo(tmp_path)
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    provider = H.ScriptedProvider([r("ok")] * 8)
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider, tools={}, session=session,
                     cwd=tmp_path, settings=LoopSettings(max_turns=9))
    calls = []
    orig = core.assembler.build
    core.assembler.build = lambda *a, **k: (calls.append(
        core.assembler.with_repomap), orig(*a, **k))[1]
    for _ in range(4):
        await core.run_turn("干活")
    # 前 3 轮带地图，第 4 轮起不带（冷启动窗口关闭）
    assert calls[:3] == [True, True, True]
    assert calls[3] is False
