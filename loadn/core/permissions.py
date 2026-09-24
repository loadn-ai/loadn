"""PermissionEngine（工程详设 §4.6）：deny → allow → ask 短路评估。

模式 default | acceptEdits | plan | bypassPermissions（无头宿主场景恒
bypassPermissions——与 claude CLI --dangerously-skip-permissions 同语义）。
规则来源 .loadn/settings.json（项目 cwd 下 > 全局 ~/.loadn/settings.json；
兼容读旧 .agent/ 同名文件），
格式 {"permissions": {"deny": [...], "allow": [...]}}，条目为工具名或
"Bash:git *" 形式（冒号后是参数匹配，Bash 按命令解析）。
headless 无交互：ask = deny（回填"需确认，已阻止"）。
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path

from loadn import loadn_home

MODES = ("default", "acceptEdits", "plan", "bypassPermissions")

# 文件写入类工具（acceptEdits 模式免问；plan 模式全拒）
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    degraded: bool = False        # P0-4：无 bashlex 的降级标注（policy:degraded）


@dataclass
class PermissionEngine:
    mode: str = "default"
    deny: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    bash_rules: list = field(default_factory=list)     # P0-4 token 规则

    # ------------------------------------------------------------ 加载
    @classmethod
    def load(cls, cwd: Path, mode: str = "default") -> PermissionEngine:
        """合并序：全局 < 项目（项目同名键覆盖）。文件缺失 = 空规则。

        P0-2 信任门：项目级规则未过信任门即不加载——未信任仓库不得用
        permissions.allow 自我放行（deny 同理不加载：降级语义统一，
        宁可少规则不可信规则）。

        P0-4：permissions.bash_rules（有序 token 前缀规则）+ 回写规则
        .loadn/policy.json 合入；任一源解析/自测失败 → 该源全部规则拒载
        （fail-closed，日志显著告警）。
        """
        from loadn.core import trust
        sources = [(loadn_home() / "settings.json", True)]
        ok, why = trust.gate(cwd)
        if ok:
            sources += [(cwd / ".loadn" / "settings.json", False),
                        (cwd / ".agent" / "settings.json", False)]
        elif why != "no-resources":
            from loadn.util import get_logger
            get_logger(__name__).warning("信任门：项目级权限规则已跳过（%s）", why)
        deny: list[str] = []
        allow: list[str] = []
        bash_rules = []
        for path, _glob in sources:
            rules = _read_rules(path)
            deny += rules.get("deny") or []
            allow += rules.get("allow") or []
            raw_bash = rules.get("bash_rules")
            if raw_bash is None:
                continue
            try:
                from loadn.bash_policy import parse_rules
                bash_rules += parse_rules(raw_bash)
            except ValueError as e:
                from loadn.util import get_logger
                get_logger(__name__).error(
                    "bash_rules 配置错误，该源（%s）全部规则拒载：%s", path, e)
        # P0-4b：审批回写规则（.loadn/policy.json，v1 bash_rules 格式）——
        # 同样在信任门后（未信任仓库不得自带回写规则）；坏文件拒载告警
        if ok:
            try:
                from loadn.bash_policy import _read_policy, parse_rules, policy_path
                bash_rules += parse_rules(_read_policy(policy_path(cwd))["bash_rules"])
            except ValueError as e:
                from loadn.util import get_logger
                get_logger(__name__).error(
                    "policy.json 回写规则解析失败，拒载：%s", e)
            except OSError:
                pass
        return cls(mode=mode, deny=deny, allow=allow, bash_rules=bash_rules)

    # ------------------------------------------------------------ 评估
    def check(self, tool: str, args: dict) -> Decision:
        # P0-4：Bash 单点裁决（AST 拆解 + token 规则 + 旧串规则逐子命令化）
        if tool == "Bash":
            return self._check_bash(str(args.get("command") or ""))
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

    def _check_bash(self, cmd: str) -> Decision:
        """Bash 裁决：deny 串规则 > bypass/plan 模式位 > token 规则
        （bash_rules 配置了时）> 旧串规则（逐子命令匹配）。"""
        from loadn import bash_policy as bp
        hit = _match_rules(self.deny, "Bash", {"command": cmd})
        if hit:
            return Decision(False, f"权限规则拒绝：{hit}")
        if self.mode == "bypassPermissions":
            return Decision(True)
        if self.mode == "plan":
            if _is_read_only("Bash", {"command": cmd}):
                return Decision(True)
            return Decision(False, "plan 模式只读：禁止非只读工具")
        if self.bash_rules:
            v = bp.evaluate(self.bash_rules, cmd)
            if v.decision == "allow":
                return Decision(True, degraded=v.degraded)
            reason = v.reason or "无规则命中"
            return Decision(False, f"Bash 需要确认（{reason}；headless 无交互"
                                    "已阻止）"
                                    + (" [policy:degraded]" if v.degraded else ""),
                            degraded=v.degraded)
        # 旧串规则：逐子命令全匹配（bashlex 在）；不可拆=不放行（fail-closed）
        hit = _match_rules(self.allow, "Bash", {"command": cmd})
        if hit:
            if bp.HAVE_BASHLEX:
                return Decision(True)
            return Decision(True, "policy:degraded（未装 bashlex，按整串首行"
                                  "匹配——pip install loadn[ast] 升级）",
                            degraded=True)
        if not bp.HAVE_BASHLEX:
            return Decision(False, "工具 Bash 需要确认（headless 无交互，已"
                                    "阻止）[policy:degraded]", degraded=True)
        return Decision(False, "工具 Bash 需要确认（headless 无交互，已阻止）；"
                               "请换无需确认的方案或说明需要用户操作")


def _read_rules(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        perms = (data or {}).get("permissions") or {}
        out = {"deny": [str(x) for x in perms.get("deny") or []],
               "allow": [str(x) for x in perms.get("allow") or []]}
        if perms.get("bash_rules") is not None:
            out["bash_rules"] = perms["bash_rules"]      # P0-4 原样透出（解析在 load）
        return out
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
    """返回命中的规则原文（未命中 None）。支持裸工具名与 "Tool:pattern"。

    P0-4：Bash 的 pattern 匹配改走覆盖语义（_bash_covered）——每个子命令
    都须被**某条**规则覆盖（`git status && curl evil` 不再命中 "Bash:git *"；
    `git status && cargo build` 可由 git/cargo 两条规则分别覆盖）。
    """
    if tool == "Bash":
        pats = [r.partition(":")[2].strip() for r in rules
                if r.startswith("Bash:") and len(r.partition(":")[2].strip()) > 0]
        if any(r == "Bash" for r in rules):
            return "Bash"
        return _bash_covered(pats, str(args.get("command") or ""))
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


def _bash_covered(pats: list[str], cmd: str) -> str | None:
    """Bash 串规则覆盖判定（P0-4）：

    - bashlex 可用：拆子命令，**每个**子命令命中至少一条 pattern 才放行
      （返回命中说明；任一段无覆盖/不可拆 → None=fail-closed 不放行）
    - 无 bashlex（degraded）：旧整串首行 fnmatch（能力降级，标注在上层）
    """
    from loadn.bash_policy import HAVE_BASHLEX, split_subcommands
    if not HAVE_BASHLEX:
        first = cmd.strip().splitlines()[0] if cmd.strip() else ""
        hit = next((p for p in pats if first and fnmatch.fnmatch(first, p)), None)
        return f"Bash:{hit}" if hit else None
    subs = split_subcommands(cmd)
    if subs is None:
        return None
    hits = []
    for tokens in subs:
        joined = " ".join(tokens)
        hit = next((p for p in pats if fnmatch.fnmatch(joined, p)), None)
        if hit is None:
            return None
        hits.append(hit)
    return f"Bash:{'+'.join(dict.fromkeys(hits))}"


def _probe_arg(tool: str, args: dict) -> str:
    """规则参数匹配的探针值：Bash 用 command 首行，其余用 file_path/path/url。"""
    if tool == "Bash":
        return str(args.get("command") or "").strip().splitlines()[0] \
            if str(args.get("command") or "").strip() else ""
    for k in ("file_path", "path", "url", "pattern", "query"):
        if args.get(k):
            return str(args[k])
    return ""
