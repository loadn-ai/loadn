"""auto-commit 影子分支（P3-10，aider auto_commit 同构）。

agent 改动与人工改动隔离，undo 走 git 原生（reset/checkout 零学习成本）：
- **off**（默认）：不动用户仓库——尊重 git 状态是用户资产
- **current**：每 turn 收尾把本会话改动 commit 到当前分支（消息
  `loadn(<sid> turn N)`——aider 轮级自动提交形态）
- **shadow**：commit 到影子引用 `refs/loadn/turns/<sid>`——**不动工作
  分支**（不切换、不污染 log），`git reset` 到影子 tip 即回滚本 turn

- 非 git 目录静默跳过；git 可用性按 turn 探测（便宜：rev-parse 一次）
- 与平台文件快照并存（快照管沙箱内，git 管用户仓——两个恢复面）
- /undo（REPL）= reset --hard 到上一 loadn commit（影子或当前分支同构）
- 配置：.loadn/settings.json `"auto_commit": "off|current|shadow"`
  （会话工作区物化，引擎读 cwd 侧文件）
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loadn.util import get_logger

log = get_logger(__name__)

MODES = ("off", "current", "shadow")
REF_PREFIX = "refs/loadn/turns/"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                         capture_output=True, text=True, timeout=30, check=check)


def mode_of(cwd: Path) -> str:
    """读 cwd/.loadn/settings.json 的 auto_commit（缺省 off）。"""
    try:
        data = json.loads((cwd / ".loadn" / "settings.json").read_text(
            encoding="utf-8"))
        m = str((data or {}).get("auto_commit") or "off")
        return m if m in MODES else "off"
    except (OSError, ValueError):
        return "off"


def _changed(cwd: Path) -> bool:
    return _git(cwd, "status", "--porcelain").stdout.strip() != ""


def commit_turn(cwd: Path, sid: str, turn_no: int) -> str | None:
    """turn 收尾钩子：按档位提交。返回 commit sha（无提交返回 None）。

    非git/无改动/off → None（零副作用）。stage 全部改动（agent 视角
    的 turn 原子性——含新文件；用户半成品人工改动在 shadow 档也被
    带入，这是 aider 同款的取舍：turn 是提交粒度，不猜归因）。
    """
    mode = mode_of(cwd)
    if mode == "off":
        return None
    try:
        _git(cwd, "rev-parse", "--git-dir", check=False)
        if not _git(cwd, "rev-parse", "--git-dir", check=False).returncode == 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None                                    # 非 git 目录静默跳过
    if not _changed(cwd):
        return None
    msg = f"loadn({sid} turn {turn_no})"
    try:
        _git(cwd, "add", "-A")
        _git(cwd, "commit", "-m", msg,
             "--author=loadn <agent@loadn.local>")
        sha = _git(cwd, "rev-parse", "HEAD").stdout.strip()
        if mode == "shadow":
            # 影子引用指向新 commit，然后把工作分支退回提交前——
            # 工作区文件不变（reset --soft 语义），log 无 loadn 痕迹
            parent = _git(cwd, "rev-parse", "HEAD~1").stdout.strip()
            _git(cwd, "update-ref", REF_PREFIX + sid, sha)
            # --mixed：index 回到 parent（无 staged 残留），工作区文件
            # 保留（改动以 unstaged 形态存在——status 可见即诚实）
            _git(cwd, "reset", "--mixed", parent)
        return sha
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("auto-commit(%s) 失败（跳过）：%s", mode, e)
        return None


def undo(cwd: Path, sid: str) -> str:
    """回滚到上一 loadn commit（REPL /undo）。返回用户可读结果。"""
    try:
        if _git(cwd, "rev-parse", "--git-dir", check=False).returncode != 0:
            return "（非 git 目录——无可回滚）"
    except (OSError, subprocess.SubprocessError):
        return "（git 不可用）"
    # 影子优先：refs/loadn/turns/<sid> 存在 → reset 到影子（本会话面）
    ref = REF_PREFIX + sid
    try:
        r = _git(cwd, "rev-parse", "--verify", ref, check=False)
        if r.returncode == 0:
            _git(cwd, "reset", "--hard", r.stdout.strip())
            _git(cwd, "clean", "-fd")
            return f"已回滚到影子 {ref}（{r.stdout.strip()[:12]}）"
        # 当前分支：找倒数第一个 loadn commit
        sha = _git(cwd, "rev-parse", "HEAD").stdout.strip()
        for ln in _git(cwd, "log", "--grep=^loadn(", "-n", "5",
                       "--format=%H").stdout.splitlines():
            if ln and ln != sha:
                _git(cwd, "reset", "--hard", ln)
                _git(cwd, "clean", "-fd")
                return f"已回滚到 {ln[:12]}"
        return "（找不到 loadn 提交——无可回滚）"
    except (OSError, subprocess.SubprocessError) as e:
        return f"回滚失败：{e}"
