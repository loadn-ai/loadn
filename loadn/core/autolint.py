"""auto-lint 编辑验证环（P3-9，aider auto_lint 同构）：改完自动检查。

Edit/MultiEdit/Write 成功后（写路径统一——_write_guarded/Write critical，
P0-1 锁内已挂 turn_diff 的同一点）对**变更文件**跑 lint，失败摘要回喂
下一 turn（system-reminder 语义——机制化「改完要检查」）。

- 命令来源（**权限面**：auto-lint 只跑配置内命令，不跑任意 shell——
  配置外=不执行。这与 aider 全 shell 自由不同，是我们安全模型的取舍）：
  1. `.loadn/settings.json` 的 `auto_lint_cmd`（显式配置优先）
  2. 未配置 → 探测 ruff（PATH 里有即 `ruff check --output-format=concise`）
  3. 都没有 → 静默跳过（瘦环境不裸奔报错）
- 默认 lint 开（`auto_lint: false` 可关）；**auto_test 默认关**（防测试
  风暴——配置 `auto_test_cmd` 才启用，跑同样过配置门）
- 失败不 block 工具结果（edit 已成功——验证是补充信息）；输出截断
  预算内（MAX_CHARS）
- 结果挂 ctx.extras["auto_lint"]（loop 在下一 turn 组装 system-reminder）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from loadn.util import get_logger

log = get_logger(__name__)

MAX_CHARS = 2_000
_LINT_TIMEOUT_S = 30
_TEST_TIMEOUT_S = 120

# 变更文件后缀 → lint 适用（跳过 .md/.txt/.yaml 等非代码）
_CODE_SUFFIXES = {".py"}


def settings(cwd: Path) -> dict:
    try:
        data = json.loads((cwd / ".loadn" / "settings.json").read_text(
            encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _find(name: str) -> str | None:
    """PATH 优先，其次解释器同目录（venv 里跑引擎但 bin 不在 PATH 的常态）。"""
    found = shutil.which(name)
    if found:
        return found
    cand = Path(sys.executable).parent / name
    return str(cand) if cand.is_file() else None


def lint_command(cfg: dict) -> list[str] | None:
    """配置门：显式 auto_lint_cmd 优先；否则探测 ruff；无则 None。"""
    cmd = str(cfg.get("auto_lint_cmd") or "").strip()
    if cmd:
        return cmd.split()                       # 配置内白名单即此命令本身
    ruff = _find("ruff")
    if ruff:
        return [ruff, "check", "--output-format=concise"]
    pyflakes = _find("pyflakes")                 # 瘦环境次选
    if pyflakes:
        return [pyflakes]
    return None


def test_command(cfg: dict) -> list[str] | None:
    cmd = str(cfg.get("auto_test_cmd") or "").strip()
    return cmd.split() if cmd else None


def run_check(cwd: Path, argv: list[str], timeout_s: float) -> str | None:
    """跑一次检查：None=通过/不可跑；str=失败摘要（截断预算内）。"""
    try:
        r = subprocess.run(argv + [], cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("auto-lint 执行失败（忽略）：%s：%s", argv, e)
        return None
    if r.returncode == 0:
        return None
    out = (r.stdout + r.stderr).strip()
    return out[:MAX_CHARS] + ("…" if len(out) > MAX_CHARS else "") or "(无输出)"


def after_edit(cwd: Path, path: Path, ctx) -> None:
    """写路径钩子（P3-9 主入口——turn_diff 同点调用）。"""
    if path.suffix not in _CODE_SUFFIXES:
        return
    cfg = settings(cwd)
    if cfg.get("auto_lint") is False:            # 显式关
        return
    cmd = lint_command(cfg)
    if cmd is None:
        return                                   # 无工具静默跳过
    summary = run_check(cwd, cmd + [str(path)], _LINT_TIMEOUT_S)
    if summary:
        ctx.extras.setdefault("auto_lint", []).append(
            {"path": str(path), "output": summary})
    # auto_test 默认关：显式配置才跑（且不限定单文件——按项目根）
    tcmd = test_command(cfg)
    if tcmd and cfg.get("auto_test") is True:
        tsum = run_check(cwd, tcmd, _TEST_TIMEOUT_S)
        if tsum:
            ctx.extras.setdefault("auto_lint", []).append(
                {"path": "(test)", "output": tsum})


def pending_reminders(ctx) -> list[dict]:
    """取走积压的验证结果（loop 下一 turn 组 system-reminder）。"""
    return ctx.extras.pop("auto_lint", []) or []
