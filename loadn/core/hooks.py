"""HookRunner（工程详设 §4.8）：外部命令钩子，stdin=JSON、10s 超时。

事件：PreToolUse / PostToolUse / Stop / SessionStart / SessionEnd。
决策：exit 2 = block（stderr 回填给模型）；exit 0 = 放行（stdout 为 JSON 时
可改写——PreToolUse 的 {"input":…} 覆盖工具入参，PostToolUse 的 {"output":…}
覆盖工具输出）。钩子永不炸穿主循环。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from loadn import loadn_home
from loadn.constants import HOOK_TIMEOUT_S
from loadn.util import get_logger

log = get_logger(__name__)


@dataclass
class HookOutcome:
    blocked: bool = False
    block_reason: str = ""      # blocked 时回填给模型的文本（stderr）
    input_override: dict | None = None    # PreToolUse 改写入参
    output_override: str | None = None    # PostToolUse 改写输出


class HookRunner:
    def __init__(self, hooks: dict[str, list[str]] | None = None) -> None:
        # {event: [command, …]}（外部命令钩子）
        self.hooks = hooks or {}
        # P3-5a 总线化：进程内扩展 handler（loadn.ext 的 on()——与外部
        # 命令钩子同语义同优先面；fire() 里先于外部命令跑）
        self.handlers: dict[str, list] = {}

    @classmethod
    def load(cls, cwd: Path) -> HookRunner:
        """钩子声明：全局 < 项目（.loadn/settings.json 的 hooks 段，列表拼接；
        兼容读旧 .agent/ 一版）。

        P0-2 信任门：项目级 settings 未过信任门即降级跳过（hooks 段=
        任意命令执行面，clone 的仓库不得自带可执行钩子）；全局面不受影响。
        """
        from loadn.core import trust
        ok, why = trust.gate(cwd)
        paths = [loadn_home() / "settings.json"]
        if ok:
            paths += [cwd / ".loadn" / "settings.json",
                      cwd / ".agent" / "settings.json"]
        elif why != "no-resources":
            log.warning("信任门：%s", why)
        merged: dict[str, list[str]] = {}
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                hooks = (data or {}).get("hooks") or {}
                for ev, entries in hooks.items():
                    cmds = [e.get("command") for e in entries
                            if isinstance(e, dict) and e.get("command")]
                    merged.setdefault(ev, []).extend(cmds)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        return cls(merged)

    def has(self, event: str) -> bool:
        return bool(self.hooks.get(event) or self.handlers.get(event))

    async def fire(self, event: str, payload: dict) -> HookOutcome:
        """顺序跑该事件的全部钩子；首个 block 即短路返回。

        P3-5a：进程内扩展 handler 先于外部命令（同语义——返回
        {"decision":"block","reason":…} 阻断；{"input":…}/{"output":…}
        覆盖，与命令钩子的 exit 2 / stdout JSON 完全同构）。
        """
        from .ext import dispatch
        out = HookOutcome()
        res = await dispatch(self.handlers.get(event), payload)
        if res is not None:
            if res.get("decision") == "block":
                out.blocked = True
                out.block_reason = str(res.get("reason")
                                        or "extension blocked")[:2000]
                return out
            if isinstance(res.get("input"), dict):
                out.input_override = res["input"]
            if isinstance(res.get("output"), str):
                out.output_override = res["output"]
        for command in self.hooks.get(event) or []:
            try:
                res = await _run_hook(command, payload)
            except Exception as e:  # noqa: BLE001 — 钩子故障不炸会话
                out.block_reason = f"钩子执行异常（忽略）: {e!r}"
                continue
            if res is None:
                continue
            rc, stdout, stderr = res
            if rc == 2:
                out.blocked = True
                out.block_reason = (stderr or "hook blocked").strip()[:2000]
                return out
            if rc == 0 and stdout.strip().startswith("{"):
                try:
                    data = json.loads(stdout)
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict):
                    if isinstance(data.get("input"), dict):
                        out.input_override = data["input"]
                    if isinstance(data.get("output"), str):
                        out.output_override = data["output"]
        return out


async def _run_hook(command: str, payload: dict):
    proc = await asyncio.create_subprocess_shell(
        command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps(payload, ensure_ascii=False).encode()),
            timeout=HOOK_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None   # 超时=放行（不阻断），由日志观察
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")
