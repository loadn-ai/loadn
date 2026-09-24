"""P3-10 auto-commit 影子分支验收。

- 三档行为：off（不动仓库）/ current（主分支多 loadn commit）/
  shadow（refs/loadn/turns/<sid> 引用链有 commit，主分支 log 无污染）
- shadow + undo：/undo 路径 reset 到影子 tip → 文件回滚
- current + undo：找倒数第一个 loadn commit 回滚
- 非 git 目录静默跳过（无异常、无副作用）
- turn 集成：fake run_turn（shadow 档）后影子引用存在
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadn.core import autocommit as ac


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _set_mode(repo: Path, mode: str) -> None:
    (repo / ".loadn").mkdir(exist_ok=True)
    (repo / ".loadn" / "settings.json").write_text(
        f'{{"auto_commit": "{mode}"}}', encoding="utf-8")


# ---------------------------------------------------------------- 三档
def test_off_leaves_repo_untouched(repo):
    _set_mode(repo, "off")
    (repo / "x.txt").write_text("x", encoding="utf-8")
    assert ac.commit_turn(repo, "s1", 1) is None
    assert _git(repo, "log", "--oneline").count("\n") == 1      # 只有 init
    assert ac.mode_of(repo) == "off"


def test_current_mode_commits_to_branch(repo):
    _set_mode(repo, "current")
    (repo / "a.txt").write_text("agent edit", encoding="utf-8")
    sha = ac.commit_turn(repo, "s1", 1)
    assert sha and sha == _git(repo, "rev-parse", "HEAD").strip()
    assert "loadn(s1 turn 1)" in _git(repo, "log", "-1")
    assert (repo / "a.txt").exists()


def test_shadow_mode_no_branch_pollution(repo):
    _set_mode(repo, "shadow")
    head_before = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "b.txt").write_text("shadow edit", encoding="utf-8")
    sha = ac.commit_turn(repo, "sX", 2)
    assert sha
    # 主分支零污染：HEAD 不动、log 无 loadn、无 staged 残留（工作区
    # 文件以 untracked/unstaged 保留——诚实可见，提交历史无痕迹）
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    assert "loadn" not in _git(repo, "log", "--oneline")
    staged = [l for l in _git(repo, "status", "--porcelain").splitlines()
              if l[:2] in ("A ", "M ")]
    assert staged == [], staged
    # 影子引用链有 commit 且内容在
    shadow = _git(repo, "rev-parse", "--verify",
                  "refs/loadn/turns/sX").strip()
    assert shadow == sha
    blob = _git(repo, "show", f"{sha}:b.txt")
    assert blob.strip() == "shadow edit"


# ---------------------------------------------------------------- undo
def test_shadow_undo_rolls_back(repo):
    _set_mode(repo, "shadow")
    (repo / "c.txt").write_text("v1", encoding="utf-8")
    sha1 = ac.commit_turn(repo, "sU", 1)
    assert sha1
    # 再改一轮（影子引用推进）
    (repo / "c.txt").write_text("v2-bad", encoding="utf-8")
    (repo / "extra.txt").write_text("junk", encoding="utf-8")
    sha2 = ac.commit_turn(repo, "sU", 2)
    assert sha2 and sha2 != sha1
    # undo：reset 到影子 tip（v2 状态）——注意影子=最新 turn 的快照，
    # undo 语义=回到「上一 loadn commit」（这里影子链就两个，reset 到 tip）
    out = ac.undo(repo, "sU")
    assert "回滚" in out
    # reset --hard 到影子 tip：v2 文件在、干净
    assert (repo / "c.txt").read_text().startswith("v2")


def test_current_undo_finds_loadn_commit(repo):
    _set_mode(repo, "current")
    (repo / "d.txt").write_text("good", encoding="utf-8")
    sha1 = ac.commit_turn(repo, "sC", 1)
    assert sha1
    # turn 2 的坏改动 + loadn commit
    (repo / "d.txt").write_text("bad", encoding="utf-8")
    ac.commit_turn(repo, "sC", 2)
    out = ac.undo(repo, "sC")
    assert "回滚" in out
    assert (repo / "d.txt").read_text() == "good"   # 回到 turn 1（上一 loadn）


def test_non_git_dir_silent(tmp_path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    _set_mode(tmp_path, "current")
    assert ac.commit_turn(tmp_path, "sN", 1) is None  # 静默跳过
    assert "非 git" in ac.undo(tmp_path, "sN")


# ---------------------------------------------------------------- turn 集成
async def test_turn_triggers_shadow_commit(repo, monkeypatch):
    import tests.helpers as H
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk
    monkeypatch.setenv("LOADN_HOME", str(repo / "home"))
    _set_mode(repo, "shadow")
    provider = H.ScriptedProvider([
        H.tool_round("t1", "Write", {"file_path": str(repo / "gen.txt"),
                                      "content": "made by agent"}),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 40, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    session = SessionManager.create(repo, home=repo / "home")
    from loadn.tools.write import WriteTool
    core = AgentCore(provider=provider, tools={"Write": WriteTool()},
                     session=session, cwd=repo,
                     settings=LoopSettings(max_turns=4))
    await core.run_turn("生成文件")
    # 影子引用存在且含生成文件
    sha = _git(repo, "rev-parse", "--verify",
               f"refs/loadn/turns/{session.session_id}").strip()
    assert sha
    assert "made by agent" in _git(repo, "show", f"{sha}:gen.txt")
    # 主分支 log 仍无 loadn
    assert "loadn" not in _git(repo, "log", "--oneline")
