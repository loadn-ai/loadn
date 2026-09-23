"""确定性权限平面（W1-a）——策略在模型之外，粒度到参数，fail-closed。

单一策略源，三处消费：CLI 网关（执行点 B，本版落地）、PreToolUse 钩子
（执行点 A，`loadn-web policy-check --hook`，W1-b 物化时接入）、
engine 编排层（后续）。任何 AI 评判不参与判定。

分层（v1.1 §6.2：glob 误伤 git show/bash build.sh 的教训）：
- L0 绝对红线：文本+AST 双拦，任何模式（含全自动/定时唤醒）都 block
- L1 模式类：仅 bash AST 参数级匹配（curl/wget 目标域 ∈ egress.allow）
- glob 兜底：文本特征命中只 warn（audit 留痕）不硬拦——误拦率优先

bash 解析：bashlex 保守档（M0）。解析失败 = block（fail-closed）。
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .audit import audit

# ---------------------------------------------------------------- 决策

ACTION_ALLOW, ACTION_BLOCK, ACTION_WARN = "allow", "block", "warn"


@dataclass
class Decision:
    action: str
    reason: str = ""
    matched: str = ""            # 命中的规则（审计用）

    @property
    def ok(self) -> bool:
        return self.action != ACTION_BLOCK

    def __str__(self) -> str:    # deny 理由回模型（钩子 stderr）的形态
        return f"[policy:{self.action}] {self.reason}"


# ---------------------------------------------------------------- L0 红线

# 文本级正则（AST 展平后对单命令再匹配一次；两路都拦）
_L0_TEXT = [
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    (r"\bdd\b[^|;&]*\bof=/dev/(sd|nvme|vd|mmcblk)", "dd 直写块设备"),
    (r"\bchmod\s+(-[a-zA-Z]+\s+)*-?R[a-zA-Z]*\s+777\s+/(?:\s|$)", "全盘开放权限"),
]

def _rm_redline(cmd: str) -> bool:
    """rm 递归强删根/家目录：flag 段含 r+f 且首个目标以 / 或 ~ 开头。

    两步判定（正则单段对 flag 重复形态不可靠）：先抓 rm 的 flag 串+首目标，
    再验 r 与 f（含 --recursive/--force 长形）。
    """
    for m in re.finditer(r"\brm\s+((?:-{1,2}[\w-]+\s+)+)(\S+)", cmd):
        flags, target = m.group(1), m.group(2)
        # 拦：根/家目录本体、家目录下、系统目录（/etc /usr /var /boot /lib…）。
        # 放：系统临时区（/tmp /var/tmp /dev/shm）与工作区相对路径
        # （误删由 W6.2 快照回滚兜底，误拦优先级更高）。
        if target.startswith("~"):
            pass                      # ~ 或 ~/… 一律拦
        elif target.startswith("/"):
            tmp = ("/tmp/", "/var/tmp/", "/dev/shm/")
            if target in ("/tmp", "/var/tmp", "/dev/shm") or target.startswith(tmp):
                continue              # 临时区放行
        else:
            continue                  # 相对路径（工作区内）放行
        f = flags.replace("-", "")
        if ("r" in f or "recursive" in flags) and ("f" in f or "force" in flags):
            return True
    return False


_L0_CMD_NAMES = {"mkfs", "mkfs.ext2", "mkfs.ext3", "mkfs.ext4", "mkfs.xfs",
                 "mkfs.btrfs", "mkfs.vfat", "mkfs.ntfs", "mkswap", "wipefs"}

# glob 兜底（只 warn）：管到 shell 的下载执行形态
_WARN_GLOBS = ["*curl*|*sh*", "*wget*|*bash*", "*curl*|*bash*", "*wget*|*sh*",
               "*nc *-e*", "*>/dev/tcp/*"]

# L1：网络类命令的目标域白名单
_NET_CMDS = {"curl", "wget"}


def _egress_allow() -> set[str]:
    from .config import CONFIG
    return {h.lower() for h in (CONFIG.security.egress_allow or [])}


def _host_allowed(url: str, allow: set[str]) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == a or host.endswith("." + a) for a in allow)


# ---------------------------------------------------------------- bash AST

def _iter_command_words(tree) -> list[list[str]]:
    """展平 bashlex 树（node.kind）的 command 节点 → 词列表（逐命令匹配）。"""
    out: list[list[str]] = []
    if tree is None:
        return out
    if getattr(tree, "kind", None) == "command":
        cmd = []
        for w in getattr(tree, "parts", None) or []:
            k = getattr(w, "kind", None)
            if k == "word":
                cmd.append(getattr(w, "word", ""))
            elif k == "literal":
                cmd.append(getattr(w, "value", ""))
            elif k in ("parameter", "commandsubstitution", "tilde"):
                cmd.append(_first_literal(w) or "")
            elif k == "assign":         # VAR=x cmd 形态：跳过赋值词
                continue
        if cmd:
            out.append(cmd)
        return out
    for sub in getattr(tree, "parts", None) or []:
        out.extend(_iter_command_words(sub))
    return out


def _first_literal(node) -> str:
    for attr in ("value", "word", "text"):
        v = getattr(node, attr, None)
        if isinstance(v, str) and v:
            return v
    parts = getattr(node, "parts", None) or []
    for sub in parts:
        v = _first_literal(sub)
        if v:
            return v
    return ""


# ---------------------------------------------------------------- 主判定

def check_command(cmd: str, *, source: str = "cli") -> Decision:
    """命令级策略判定（Bash 工具/CLI 网关共用）。

    source: cli | hook | engine（审计标记判定来源）
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return Decision(ACTION_ALLOW)

    # 1) L0 文本层
    if _rm_redline(cmd):
        d = Decision(ACTION_BLOCK, "递归强删根/家目录（L0 红线）", matched="rm-rf")
        _audit_decision(d, cmd, source)
        return d
    for pat, why in _L0_TEXT:
        if re.search(pat, cmd):
            d = Decision(ACTION_BLOCK, f"{why}（L0 红线）", matched=pat)
            _audit_decision(d, cmd, source)
            return d

    # 1.5) canary 外渗检测（W5.5）：内容命中蜜罐值 → block + 会话熔断
    try:
        from . import canary as _canary
        tok = _canary.hit(cmd)
        if tok:
            d = Decision(ACTION_BLOCK, f"外发内容含 canary 蜜罐值（{tok[:10]}…）"
                         "——疑似数据外渗，会话已熔断", matched="canary")
            _audit_decision(d, cmd, source)
            audit("canary_hit", {"token": tok[:12] + "…", "subject": cmd[:200],
                                 "source": source})
            return d
    except Exception:                                  # noqa: BLE001
        pass                                            # canary 不可用不阻塞判定

    # 2) bash AST（保守档：解析失败 = block）
    try:
        import bashlex
        trees = bashlex.parse(cmd)
    except Exception:                                  # noqa: BLE001
        d = Decision(ACTION_BLOCK, "bash 解析失败（fail-closed）", matched="parse")
        _audit_decision(d, cmd, source)
        return d

    allow = _egress_allow()
    for words in _iter_command_words(trees[0] if trees else None):
        if not words:
            continue
        name = Path(words[0]).name
        # L0 命令名级
        if name in _L0_CMD_NAMES:
            d = Decision(ACTION_BLOCK, f"{words[0]}（L0 红线命令）", matched=name)
            _audit_decision(d, cmd, source)
            return d
        # L1 网络类：目标域 ∈ egress.allow
        if name in _NET_CMDS:
            urls = [w for w in words[1:]
                    if w.startswith(("http://", "https://")) or
                    (not w.startswith("-") and "." in w and "/" in w)]
            for u in urls:
                if not _host_allowed(u, allow):
                    d = Decision(ACTION_BLOCK,
                                 f"{name} 目标域不在出口白名单: {u}", matched=u)
                    _audit_decision(d, cmd, source)
                    return d

    # 3) glob 兜底：只 warn
    for g in _WARN_GLOBS:
        if fnmatch.fnmatch(cmd, "*" + g + "*"):
            d = Decision(ACTION_WARN, f"命中下载执行形态特征（{g}）——放行留痕",
                         matched=g)
            _audit_decision(d, cmd, source)
            return d

    return Decision(ACTION_ALLOW)


# ---------------------------------------------------------------- 路径判定

_SENSITIVE_PATH_PATTERNS = [
    "var/vault.json", "var/vault.enc", "var/loadn.db", "var/audit.db",
    "config.yaml", "config.yaml.example",
    ".ssh/*", ".aws/*", ".claude/.credentials.json", ".loadn/config.json",
]
_SENSITIVE_ABS = ("/proc/self/environ",)


def check_path(path: str) -> Decision:
    """敏感路径黑名单（W3.4 双保险的无沙箱侧；沙箱物理不挂载为主）。

    相对平台数据根与用户家目录的关键路径：读/写目标命中即 block。
    """
    p = (path or "").strip()
    if not p:
        return Decision(ACTION_ALLOW)
    if p in _SENSITIVE_ABS:
        d = Decision(ACTION_BLOCK, f"敏感路径（{p}）", matched=p)
        _audit_decision(d, p, "path")
        return d
    norm = p.replace("\\", "/")
    parts = [x for x in norm.split("/") if x not in ("", ".")]
    for pat in _SENSITIVE_PATH_PATTERNS:
        segs = [x for x in pat.split("/") if x]
        n = len(segs)
        for i in range(len(parts) - n + 1):
            window = parts[i:i + n]
            if all(fnmatch.fnmatch(w, s) for w, s in zip(window, segs)):
                d = Decision(ACTION_BLOCK, f"敏感路径（{pat}）", matched=pat)
                _audit_decision(d, p, "path")
                return d
    return Decision(ACTION_ALLOW)


# ---------------------------------------------------------------- W6.2 快照

def _snapshot_before_write(target: str) -> None:
    """Write/Edit 写路径前把原文件快照到 <cwd>/.snapshots/<点>/<rel>。

    内容寻址幂等（同点同文件只存一份）；恢复前先快照现状（rollback-pre）。
    Bash 写目标不可静态知——该面靠 .snapshots 挂 tmpfs（沙箱）+ 快照点内
    人工核对（诚实边界，v1.1 §6.7）。
    """
    import shutil as _sh
    from datetime import datetime as _dt
    t = Path(target)
    if not t.is_file():
        return                                   # 新建文件无原版可快照
    try:
        import hashlib as _hl
        ws = Path.cwd()
        rel = t.resolve().relative_to(ws)
        if any(p == ".." for p in rel.parts):
            return                               # 工作区外不快照（沙箱兜底）
        digest = _hl.sha256(t.read_bytes()).hexdigest()[:16]
        point = _dt.now().strftime("%Y%m%d-%H%M%S")
        dst = ws / ".snapshots" / point / f"{rel}::{digest}"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            _sh.copy2(t, dst)
    except (OSError, ValueError):
        pass                                     # 快照失败不阻断写（尽力而为）


def list_snapshots(ws: Path) -> list[dict]:
    """快照点清单（点/文件数/时间），供回滚 UI。"""
    root = ws / ".snapshots"
    if not root.exists():
        return []
    out = []
    for point in sorted(root.iterdir()):
        if not point.is_dir():
            continue
        files = list(point.rglob("*"))
        out.append({"point": point.name, "files": len([f for f in files if f.is_file()])})
    return out


def rollback(ws: Path, point: str) -> dict:
    """把快照点内全部文件恢复回工作区（恢复前现状先快照到 rollback-pre 点）。"""
    import shutil as _sh
    from datetime import datetime as _dt
    src_dir = ws / ".snapshots" / point
    if not src_dir.is_dir():
        return {"ok": False, "error": f"快照点不存在: {point}"}
    pre = ws / ".snapshots" / (_dt.now().strftime("rollback-pre-%Y%m%d-%H%M%S"))
    restored = []
    for f in sorted(src_dir.rglob("*::*")):
        if not f.is_file():
            continue
        rel_with_digest = f.relative_to(src_dir)
        rel = Path(str(rel_with_digest).rsplit("::", 1)[0])
        dst = ws / rel
        if dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            pre_dst = pre / rel
            pre_dst.parent.mkdir(parents=True, exist_ok=True)
            _sh.copy2(dst, pre_dst)               # 现状先快照（可再回滚）
        dst.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy2(f, dst)
        restored.append(str(rel))
    from .audit import audit as _audit
    _audit("rollback", {"point": point, "restored": restored[:50]})
    return {"ok": True, "restored": restored, "pre_snapshot": pre.name}


# ---------------------------------------------------------------- 审计

def _audit_decision(d: Decision, subject: str, source: str) -> None:
    audit("permission_decision",
          {"action": d.action, "reason": d.reason, "rule": d.matched,
           "source": source, "subject": subject[:500]})


# ---------------------------------------------------------------- CLI 网关

def cli_gateway(argv_words: list[str], subcommand: str) -> Decision:
    """资源 CLI（loadn-web r …）执行点 B：全 argv 过 L0 + 网络类目标域。

    irreversible 子命令（mail/sms/pay/...）的确认码门在 W1-2（approvals）
    接入；本版先拦红线与非白名单外联。
    """
    from .config import CONFIG
    sig = " ".join(argv_words)
    d = check_command(sig, source="cli-gateway")
    if not d.ok:
        return d
    if subcommand in ("fetch", "search", "browser"):
        urls = [w for w in argv_words if w.startswith(("http://", "https://"))]
        allow = _egress_allow()
        for u in urls:
            if not _host_allowed(u, allow):
                d = Decision(ACTION_BLOCK, f"{subcommand} 目标域不在白名单: {u}",
                             matched=u)
                _audit_decision(d, sig, "cli-gateway")
                return d
    if subcommand in CONFIG.security.irreversible_tools:
        # W1-2 approvals 落地前的占位告警（fail-closed 版随 approvals 上线）
        audit("permission_decision",
              {"action": "warn", "source": "cli-gateway",
               "reason": f"{subcommand}=不可逆工具，approvals(W1-2) 未上线暂放行",
               "subject": sig[:500]})
    return Decision(ACTION_ALLOW)


def _hook_sidelog(tool: str, inp: dict, d: Decision) -> None:
    """沙箱内 hook 判定侧车：append 到 <cwd>/.loadn-hook-audit.jsonl。

    沙箱不挂平台 var/（设计）——hook 无法直写审计库；宿主侧 engine._finish
    回收本文件入账（_drain_hook_audit）。fail-soft：写失败不影响判定。
    """
    if d.action == ACTION_ALLOW:
        return                                        # 只留 block/warn 痕
    try:
        from datetime import datetime as _dt
        from datetime import timezone as _tz
        entry = {"ts": _dt.now(_tz.utc).isoformat(timespec="seconds"),
                 "tool": tool,
                 "subject": json.dumps(inp, ensure_ascii=False, default=str)[:300],
                 "action": d.action, "reason": d.reason}
        with open(".loadn-hook-audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 钩子执行体

def policy_check_hook(stdin_json: str) -> int:
    """`loadn-web policy-check --hook`：PreToolUse 钩子（W1-b 物化时接入）。

    stdin: {"tool_name": "...", "tool_input": {...}}
    exit 2 = block（stderr 回模型）；exit 0 = 放行。
    """
    import json
    import sys
    try:
        payload = json.loads(stdin_json or "{}")
    except json.JSONDecodeError:
        print("policy-check: 输入非 JSON（fail-closed）", file=sys.stderr)
        return 2
    # 双字段名兼容：claude CLI（tool_name/tool_input）与 loadn 引擎（tool/input）
    tool = (payload.get("tool_name") or payload.get("tool") or "").lower()
    inp = payload.get("tool_input") or payload.get("input") or {}
    if tool == "bash":
        d = check_command(str(inp.get("command") or ""), source="hook")
    elif tool in ("write", "edit", "multiedit"):
        target = str(inp.get("file_path") or inp.get("path") or "")
        d = check_path(target)
        if d.ok:
            _snapshot_before_write(target)       # W6.2 写前快照（回滚底座）
    elif tool == "read":
        d = check_path(str(inp.get("file_path") or inp.get("path") or ""))
    else:
        d = Decision(ACTION_ALLOW)
    _hook_sidelog(tool, inp, d)                  # 沙箱内审计侧车（宿主回收）
    if not d.ok:
        print(f"{d}", file=sys.stderr)
        return 2
    return 0
