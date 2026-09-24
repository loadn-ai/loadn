"""Edit 工具——精确字符串替换，读后写守卫 + mtime 外部变更守卫。

三重前置：①文件必须在本会话 Read 过（files_touched 有键）；②mtime 与
登记一致（期间被外部改过 → 拒绝，重读再改）；③old_string 精确匹配
（含缩进）。0 命中给 fuzzy 提示（difflib.get_close_matches + 目标行
±3 上下文）；多命中未开 replace_all 列出全部行号；单次 diff 超
EDIT_DIFF_MAX_LINES 拒绝并建议分步——大改用 Write 或多次小步。

_load_guarded/_apply_one/_write_guarded 是 MultiEdit/NotebookEdit 共享的
守卫与替换内核（模块级函数，本文件即唯一真相）。
"""
from __future__ import annotations

import difflib
import unicodedata
from pathlib import Path

from loadn.constants import EDIT_DIFF_MAX_LINES
from loadn.tools.base import Tool, ToolContext, ToolError, with_file_lock
from loadn.tools.read import file_key


# ---------------------------------------------------------------- 共享内核
def _norm_line(s: str) -> str:
    """行归一化（pi fuzzy 空间）：NFKC + 智能引号归一 + 行尾空白 strip。

    行首空白保持显著（缩进是语义）；只用于匹配定位，不改写原文。
    """
    s = unicodedata.normalize("NFKC", s)
    for k, v in _SMART_QUOTES.items():
        s = s.replace(k, v)
    return s.rstrip()


_SMART_QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'",
                 "「": "'", "」": "'"}


def _fuzzy_replace(text: str, old: str, new: str) -> str | None:
    """归一化行序列匹配替换（精确匹配失败后的降级层，pi 同构）。

    - 滑动窗口在归一化行序列上找 old 的唯一命中，替换段用原文行切出、
      new 行填入——**未动行保留原始字节**（含 CRLF/行尾空白）。
    - 空窗守卫：归一后总非空白 <8 字符或无一行 ≥4 字符 → None（防空行/
      常见行退化窗口）；归一后多命中 → None（调用方报不唯一）。
    """
    norm_old = [_norm_line(x) for x in old.split("\n")]
    if not any(norm_old):
        return None
    if sum(len(x.strip()) for x in norm_old) < 8 \
            or not any(len(x.strip()) >= 4 for x in norm_old):
        return None
    src_lines = text.split("\n")
    norm_src = [_norm_line(x) for x in src_lines]
    n = len(norm_old)
    hits = [i for i in range(len(norm_src) - n + 1)
            if norm_src[i:i + n] == norm_old]
    if len(hits) != 1:
        return None
    i = hits[0]
    new_lines = new.split("\n")
    if "\r\n" in text:   # CRLF 文件：替换段跟随原文 EOL（未动行天然保留）
        new_lines = [ln if ln.endswith("\r") else ln + "\r"
                     for ln in new_lines]
    replaced = src_lines[:i] + new_lines + src_lines[i + n:]
    return "\n".join(replaced)


def _load_guarded(raw: str, ctx: ToolContext) -> tuple[Path, str, str]:
    """读后写 + mtime 守卫下的文件读取：返回 (path, key, 文本)。

    Edit/MultiEdit/NotebookEdit 共用。不满足守卫直接 ToolError（零副作用）。
    """
    key = file_key(raw)
    if key not in ctx.files_touched:
        raise ToolError(f"本会话尚未 Read 过 {raw}，先 Read 再编辑（读后写守卫）")
    path = Path(raw)
    if not path.exists():
        raise ToolError(f"文件不存在：{path}（已被外部删除？）")
    st = path.stat()
    if st.st_mtime != ctx.files_touched[key]:
        raise ToolError(f"文件已外部变更，请重读后再编辑：{path}")
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"{path} 不是 UTF-8 文本文件，Edit 只支持文本") from None
    return path, key, text


def _apply_one(text: str, old: str, new: str, replace_all: bool, *,
               label: str = "", allow_fuzzy: bool = True) -> str:
    """在 text 上应用一处替换（含全部校验）；失败抛 ToolError 原文不变。

    label 进错误文案（MultiEdit 报 1-based 序号用）。精确匹配 0 命中时
    走归一化 fuzzy 降级层（.ipynb 等 JSON 源文本调用方应传 allow_fuzzy=False
    ——空白归一替换对 JSON 语义不安全）。
    """
    tag = f"{label}：" if label else ""
    if not old:
        raise ToolError(f"{tag}old_string 不能为空（空串必多命中且易误伤）")
    if old == new:
        raise ToolError(f"{tag}old_string 与 new_string 相同，无需编辑")
    positions: list[int] = []
    start = 0
    while True:
        i = text.find(old, start)
        if i < 0:
            break
        positions.append(i)
        start = i + len(old)          # 非重叠命中
    if not positions:
        hit = _fuzzy_replace(text, old, new) if allow_fuzzy else None
        if hit is not None:
            return hit
        raise ToolError(f"{tag}{_no_hit(text, old)}")
    if len(positions) > 1 and not replace_all:
        line_nos = [text.count("\n", 0, p) + 1 for p in positions]
        raise ToolError(
            f"{tag}old_string 出现 {len(positions)} 次（行 "
            f"{', '.join(str(n) for n in line_nos)}），不唯一；"
            "扩大上下文使其唯一，或 replace_all=true 全量替换")
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


def _write_guarded(path: Path, key: str, new_text: str, ctx: ToolContext) -> None:
    """写前 mtime 复查（堵 Read→写窗口内外部改动）后落盘并刷新登记。

    P3-1：写前快照 + 写后 unified diff 旁挂 ctx.extras['turn_diff']
    （list，工具结果尾部拼接；预算/截断语义见 core/turn_diff.py）。
    """
    if path.stat().st_mtime != ctx.files_touched[key]:
        raise ToolError(f"文件在编辑期间被外部变更，已放弃写入（请重读重试）：{path}")
    from loadn.core import turn_diff as td
    before = td.snapshot(path)
    payload = new_text.encode("utf-8")
    path.write_bytes(payload)
    ctx.files_touched[key] = path.stat().st_mtime
    diff_text, diff_hash = td.compute(path, before, payload)
    if diff_text:
        ctx.extras.setdefault("turn_diff", []).append(
            {"path": str(path), "diff": diff_text, "hash": diff_hash})
    try:
        from loadn.core import autolint
        autolint.after_edit(ctx.cwd, path, ctx)
    except Exception:                                  # noqa: BLE001
        pass


def _unified_diff(path: Path, old_text: str, new_text: str) -> list[str]:
    diff = list(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"{path}（旧）", tofile=f"{path}（新）"))
    if len(diff) > EDIT_DIFF_MAX_LINES:
        raise ToolError(
            f"本次编辑 diff {len(diff)} 行超过上限 {EDIT_DIFF_MAX_LINES} 行，"
            "拒绝执行；请拆成多次小步编辑，或整文件重写用 Write")
    return diff


class EditTool(Tool):
    """文件内精确替换（Edit 对位基础是 Read 的 `行号\\t内容` 输出）。"""

    name = "Edit"
    description = (
        "对文件做精确字符串替换（old_string 含缩进精确匹配）。必须先 "
        "Read 目标文件；文件被外部变更过会拒绝并要求重读。old_string "
        "需唯一（多处命中时扩大上下文或 replace_all=true）。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件路径"},
            "old_string": {"type": "string", "description": "要替换的原文（精确匹配含缩进）"},
            "new_string": {"type": "string", "description": "替换后的文本"},
            "replace_all": {"type": "boolean", "default": False,
                            "description": "true 时替换全部命中（默认 false 需唯一）"},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        raw = args.get("file_path")
        old = args.get("old_string")
        new = args.get("new_string")
        if not raw or not isinstance(raw, str):
            raise ToolError("缺少必填参数 file_path")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError("old_string/new_string 需为字符串")
        replace_all = bool(args.get("replace_all", False))

        async def _critical() -> str:
            # 锁在读后写守卫**之前**（P0-1）：排队方等锁期间文件被前一任务
            # 写过，轮到自己时守卫看到的才是新 mtime（否则误杀）
            path, key, text = _load_guarded(raw, ctx)
            # .ipynb 是 JSON 源文本：空白归一替换语义不安全，禁用 fuzzy 层
            new_text = _apply_one(text, old, new, replace_all,
                                  allow_fuzzy=path.suffix != ".ipynb")
            diff = _unified_diff(path, text, new_text)
            _write_guarded(path, key, new_text, ctx)
            return f"{''.join(diff)}已编辑 {path}（建议用 Read 回读确认）"

        return await with_file_lock(raw, _critical)


# ---------------------------------------------------------------- 0 命中
def _no_hit(text: str, old: str) -> str:
    """0 命中提示：fuzzy 相近行 ±3 上下文；无相近则给文件头。"""
    lines = text.splitlines()
    uniq = list(dict.fromkeys(lines))
    close = difflib.get_close_matches(old, uniq, n=3, cutoff=0.6)
    if not close:
        stripped = list(dict.fromkeys(ln.strip() for ln in lines if ln.strip()))
        close = [c for c in (difflib.get_close_matches(
            old.strip(), stripped, n=3, cutoff=0.6))][:3]
    parts = [f"未找到 old_string（精确匹配，含缩进）：{old[:120]!r}"]
    if close:
        parts.append("最接近的行（缩进/空白可能不同，行号±3 上下文）：")
        for target in close:
            for idx, line in enumerate(lines):
                if line == target or line.strip() == target:
                    lo, hi = max(0, idx - 3), min(len(lines), idx + 4)
                    ctx_lines = "\n".join(
                        f"{lo + j + 1:6}\t{lines[lo + j]}"
                        for j in range(hi - lo))
                    parts.append(ctx_lines)
                    break
    else:
        head = "\n".join(f"{i + 1:6}\t{ln}"
                         for i, ln in enumerate(lines[:15]))
        parts.append(f"无相近行；文件前 {min(15, len(lines))} 行如下（核对后重试）：\n{head}")
    return "\n".join(parts)


tool = EditTool()             # ToolRegistry.default() 收集的模块级实例
