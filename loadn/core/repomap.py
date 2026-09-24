"""Repo Map（P1-8，aider repomap 同构）：token 预算的仓库符号地图。

陌生仓库冷启动不再盲搜——系统提示词携带预算内的符号地图。

- 提取：tree-sitter（**可选 extras `loadn[repomap]`**，依赖红线唯一豁免
  先例同 bashlex）抽 def/class 定义与标识符引用；缺省降级「目录树+文件头
  docstring 摘要」廉价地图（仍按预算截断）。
- 排序（aider get_ranked_tags 同构）：引用图——标识符被引用次数越多其
  定义文件权重越高；**mentioned 文件**（本会话 Read/Edit 过）额外 ×3
  提升（aider mentioned_fnames 语义）。
- 预算：默认 1024 token（env LOADN_REPOMAP_TOKENS；1.6 字/token 粗估，
  与 context 预算同口径）；0=关闭。
- 缓存：文件 mtime 失效（模块级 {path: (mtime, tags)}），不每 turn 重算。
- 渲染：紧凑树（aider get_ranked_tags_map 同构）：`path: ident(行号), …`。
- 仅本地文件，无网络。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from loadn.util import get_logger

log = get_logger(__name__)

DEFAULT_BUDGET_TOKENS = 1024
CHARS_PER_TOKEN = 1.6
MENTIONED_BOOST = 10          # 提权幅度压过常规引用计数（aider mentioned 强提升）
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
             ".loadn", ".claude", "dist", "build", ".mypy_cache",
             ".ruff_cache", "target", "work/vendor-refs", "vendor-refs"}
MAX_FILES_SCAN = 2000
_CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
                  ".c", ".h", ".cpp", ".hpp", ".rb", ".sh", ".md"}

# tree-sitter 可选（extras loadn[repomap]）
try:
    from tree_sitter_language_pack import get_parser
    HAVE_TS = True
except ImportError:                                  # pragma: no cover
    HAVE_TS = False

_TAG_CACHE: dict[str, tuple[float, list]] = {}


def budget_tokens() -> int:
    return int(os.environ.get("LOADN_REPOMAP_TOKENS",
                              str(DEFAULT_BUDGET_TOKENS)))


def _iter_code_files(cwd: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(cwd.rglob("*")):
        if len(out) >= MAX_FILES_SCAN:
            break
        rel = str(p.relative_to(cwd))
        if any(rel == s or rel.startswith(s + "/") for s in SKIP_DIRS):
            continue
        if p.is_file() and (p.suffix in _CODE_SUFFIXES):
            out.append(p)
    return out


# ---------------------------------------------------------------- 提取
_DEF_RE = re.compile(
    r"^(?:async\s+)?(?:def|class|func|fn|type|struct|interface|enum)\s+"
    r"([A-Za-z_][\w]*)", re.M)


def _tags_for(path: Path) -> list:
    """[(ident, line)]——定义符号。mtime 缓存。

    tree-sitter 在：走 AST（定义查询，引用计数在 rank 阶段做文本扫描）；
    不在：正则 def/class 形态（对 Python/JS/Go/Rust 大体命中，标注 degraded）。
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _TAG_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    tags: list[tuple[str, int]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if HAVE_TS and path.suffix == ".py":
        try:
            parser = get_parser("python")
            tree = parser.parse(text.encode())
            def_q = "(function_definition name: (identifier) @d)"
            cls_q = "(class_definition name: (identifier) @d)"
            from tree_sitter import Query
            q = Query(parser.language, def_q + cls_q)
            for m in q.matches(tree.root_node):
                cap = m[1] if isinstance(m, tuple) else m.captures
                for node, tag in (cap if isinstance(cap, list) else [cap]):
                    if tag == "d" and node.text:
                        tags.append((node.text.decode(), node.start_point[0] + 1))
        except Exception:                                  # noqa: BLE001
            tags = _regex_tags(text)
    else:
        tags = _regex_tags(text)
    _TAG_CACHE[str(path)] = (mtime, tags)
    return tags


def _regex_tags(text: str) -> list[tuple[str, int]]:
    out = []
    for m in _DEF_RE.finditer(text):
        out.append((m.group(1), text[:m.start()].count("\n") + 1))
    return out[:80]                    # 每文件符号上限（防大文件撑爆）


def _docstring_head(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r'"""(.{1,120}?)"""', text, re.S)
    if m:
        return m.group(1).strip().splitlines()[0][:100]
    m2 = re.search(r"^(?:#|//)[ \t]*(.{4,100})$", text, re.M)
    return m2.group(1).strip() if m2 else ""


# ---------------------------------------------------------------- 排序
def get_repo_map(cwd: Path, mentioned: set[str] | None = None) -> str:
    """预算内符号地图（aider get_repo_map + get_ranked_tags_map 同构）。"""
    budget = budget_tokens()
    if budget <= 0:
        return ""
    mentioned = mentioned or set()
    files = _iter_code_files(cwd)
    if not files:
        return ""

    # 引用图：全库标识符出现次数（含定义处）；被引多者优先
    ref_counts: dict[str, int] = {}
    per_file: dict[Path, list[tuple[str, int]]] = {}
    for f in files:
        tags = _tags_for(f)
        per_file[f] = tags
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for ident, _ in tags:
            ref_counts[ident] = ref_counts.get(ident, 0) + \
                len(re.findall(r"\b" + re.escape(ident) + r"\b", text))

    # 文件权重 = Σ(标识符引用数) × mentioned 提升
    def file_weight(f: Path) -> float:
        w = sum(ref_counts.get(i, 0) for i, _ in per_file[f])
        rel = str(f.relative_to(cwd))
        if rel in mentioned or str(f) in mentioned:
            w *= MENTIONED_BOOST
        return w

    ranked = sorted(files, key=file_weight, reverse=True)

    # 渲染到预算：`rel/path.py: Ident(12), other(40)`；不足时用 docstring
    # 摘要行补（aider 无文件入聊给全景的 padding 语义）
    lines: list[str] = []
    used = 0
    for f in ranked:
        tags = per_file[f]
        rel = str(f.relative_to(cwd))
        if tags:
            body = ", ".join(f"{i}({ln})" for i, ln in tags[:8])
        else:
            head = _docstring_head(f)
            body = f"— {head}" if head else "—"
        line = f"{rel}: {body}"
        cost = int(len(line) / CHARS_PER_TOKEN)
        if used + cost > budget:
            break
        lines.append(line)
        used += cost
    if not lines:                      # 预算太小：至少给 top1 截断
        f = ranked[0]
        rel = str(f.relative_to(cwd))
        lines.append(rel[: int(budget * CHARS_PER_TOKEN) - 8])
    header = "### 仓库地图" + ("" if HAVE_TS else "（degraded：无 tree-sitter，"
                              "def/class 正则近似）")
    return header + "\n" + "\n".join(lines)
