"""PermissionEngine（工程详设 §4.6）：deny → allow → ask 短路评估。

模式 default | acceptEdits | plan | bypassPermissions（无头宿主场景恒
bypassPermissions——与 claude CLI --dangerously-skip-permissions 同语义）。
规则来源 .agent/settings.json（项目 cwd 下 > 全局 ~/.agent/settings.json），
格式 {"permissions": {"deny": [...], "allow": [...]}}，条目为工具名或
"Bash:git *" 形式（冒号后是参数匹配，Bash 按命令解析）。
headless 无交互：ask = deny（回填"需确认，已阻止"）。
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path

from hahaness import hahaness_home

MODES = ("default", "acceptEdits", "plan", "bypassPermissions")

# 文件写入类工具（acceptEdits 模式免问；plan 模式全拒）
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


@dataclass
class PermissionEngine:
    mode: str = "default"
    deny: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ 加载
    @classmethod
    def load(cls, cwd: Path, mode: str = "default") -> PermissionEngine:
        """合并序：全局 < 项目（项目同名键覆盖）。文件缺失 = 空规则。"""
        glob_rules = _read_rules(hahaness_home() / "settings.json")
        proj_rules = _read_rules(cwd / ".agent" / "settings.json")
        merged = {**glob_rules, **proj_rules}
        return cls(mode=mode, deny=merged.get("deny") or [],
                   allow=merged.get("allow") or [])

    # ------------------------------------------------------------ 评估
    def check(self, tool: str, args: dict) -> Decision:
        # ① deny 最严优先——bypass 也拦（deny 是显式策略不是询问；宿主平台
        # profile 的 WebSearch/WebFetch 禁用纪律不能被 yolo 冲掉）
        hit = _match_rules(self.deny, tool, args)
        if hit:
            return Decision(False, f"权限规则拒绝：{hit}")
        if self.mode == "bypassPermissions":
            return Decision(True)
        if self.mode == "plan":
            if _is_read_only(tool, args):
                return Decision(True)
            return Decision(False, "plan 模式只读：禁止非只读工具")
        # ② allow
        hit = _match_rules(self.allow, tool, args)
        if hit:
            return Decision(True)
        # ③ ask：acceptEdits 对文件写免问
        if self.mode == "acceptEdits" and tool in EDIT_TOOLS:
            return Decision(True)
        # headless 无交互弹窗：ask 即 deny（回填提示让模型改道）
        return Decision(False, f"工具 {tool} 需要确认（headless 无交互，已阻止）；"
                               "请换无需确认的方案或说明需要用户操作")


def _read_rules(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        perms = (data or {}).get("permissions") or {}
        return {"deny": [str(x) for x in perms.get("deny") or []],
                "allow": [str(x) for x in perms.get("allow") or []]}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _is_read_only(tool: str, args: dict) -> bool:
    if tool in ("Read", "Grep", "Glob", "WebFetch", "WebSearch", "TodoWrite", "Task"):
        return True
    if tool == "Bash":
        # plan 模式的 Bash 只放行明确只读形态（保守白名单）
        cmd = str(args.get("command") or "").strip()
        return cmd.split(" ")[0:1][0] in ("ls", "cat", "head", "tail", "rg", "grep",
                                          "find", "git", "pwd", "echo", "wc", "stat",
                                          "diff", "which", "python3", "node")
    return False


def _match_rules(rules: list[str], tool: str, args: dict) -> str | None:
    """返回命中的规则原文（未命中 None）。支持裸工具名与 "Tool:pattern"。"""
    for r in rules:
        if ":" in r and not r.startswith(":"):
            head, _, pat = r.partition(":")
            if head != tool:
                continue
            probe = _probe_arg(tool, args)
            if probe and fnmatch.fnmatch(probe, pat.strip()):
                return r
        elif r == tool:
            return r
    return None


def _probe_arg(tool: str, args: dict) -> str:
    """规则参数匹配的探针值：Bash 用 command 首行，其余用 file_path/path/url。"""
    if tool == "Bash":
        return str(args.get("command") or "").strip().splitlines()[0] \
            if str(args.get("command") or "").strip() else ""
    for k in ("file_path", "path", "url", "pattern", "query"):
        if args.get(k):
            return str(args[k])
    return ""
