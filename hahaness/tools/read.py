"""Read 工具——cat -n 行号格式读取、单行截断、大文件拒读、图片转 base64。

行号格式 `f"{n:6}\t{line}"` 是 Edit 报错上下文与模型对位的基础，两端
（Read 输出 / Edit 提示）保持同一格式。files_touched 登记是 Edit 读后写
守卫的数据源：成功读文本后记 path→mtime，Edit/Write 据此判定「本会话
读过、期间无外部变更」。
"""
from __future__ import annotations

import base64
from pathlib import Path

from hahaness.constants import READ_FILE_MAX_BYTES, READ_LINE_CHARS_MAX, READ_LINES_DEFAULT
from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.tools.truncate import Truncator

# 按扩展名识别图片（读 bytes 直接 base64 成 vision block，不走行号格式）
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}


def file_key(file_path: str | Path) -> str:
    """files_touched 的键：统一 resolve 成绝对规范路径（Read/Write/Edit 共用）。"""
    return str(Path(file_path).resolve())


class ReadTool(Tool):
    """文件读取（文本行号格式 / 图片 base64 block）。"""

    name = "Read"
    description = (
        "读取文件内容：文本按 `行号\\t内容` 格式返回（默认前 "
        f"{READ_LINES_DEFAULT} 行，offset/limit 可选）；图片（png/jpg/jpeg/"
        "gif/webp）返回 base64 数据可直接看。文件不存在时会列出同目录"
        "相邻文件帮助定位。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径（绝对或相对 cwd）"},
            "offset": {"type": "integer", "description": "起始行号（1-based）"},
            "limit": {"type": "integer",
                      "description": f"读取行数（默认 {READ_LINES_DEFAULT}）"},
        },
        "required": ["file_path"],
    }
    read_only = True

    async def execute(self, args: dict, ctx: ToolContext) -> str | list[dict]:
        raw = args.get("file_path")
        if not raw or not isinstance(raw, str):
            raise ToolError("缺少必填参数 file_path")
        path = Path(raw)
        if not path.exists():
            raise ToolError(self._missing_hint(path))
        if path.is_dir():
            names = sorted(p.name for p in path.iterdir())[:15]
            listing = ", ".join(names) if names else "（空目录）"
            raise ToolError(f"{path} 是目录不是文件。目录内容：{listing}")
        st = path.stat()
        if st.st_size > READ_FILE_MAX_BYTES:
            raise ToolError(
                f"文件过大（{st.st_size / 1024 / 1024:.1f}MB > "
                f"{READ_FILE_MAX_BYTES // 1024 // 1024}MB 上限）拒读；"
                "请用 Grep 定位或 Bash head/tail 分段读")
        mime = _IMAGE_MIME.get(path.suffix.lower())
        if mime is not None:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            return [{"type": "image",
                     "source": {"type": "base64", "media_type": mime,
                                "data": b64}}]
        try:
            offset = int(args.get("offset") or 1)
            limit = int(args.get("limit") or READ_LINES_DEFAULT)
        except (TypeError, ValueError):
            raise ToolError("offset/limit 需为整数") from None
        if offset < 1:
            raise ToolError(f"offset 是 1-based 行号，需 >= 1（收到 {offset}）")
        if limit < 1:
            raise ToolError(f"limit 需为正整数（收到 {limit}）")
        lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
        start = offset - 1
        selected = lines[start:start + limit]
        if not selected:
            raise ToolError(f"文件共 {len(lines)} 行，offset={offset} 超出范围")
        out, clipped_lines = [], []
        for i, line in enumerate(selected):
            if len(line) > READ_LINE_CHARS_MAX:
                clipped_lines.append(start + i + 1)
            out.append(f"{start + i + 1:6}\t"
                       f"{Truncator.clip_head(line, READ_LINE_CHARS_MAX)}")
        # 截断即行动（pi 纪律）：有更多行给续读 offset；被裁单行给 sed 取整行命令
        foot: list[str] = []
        if start + len(selected) < len(lines):
            foot.append(f"[文件共 {len(lines)} 行，已显示 "
                        f"{start + 1}-{start + len(selected)}；"
                        f"继续读用 offset={start + len(selected) + 1}]")
        if clipped_lines:
            hints = "; ".join(
                f"sed -n '{n}p' {path.resolve()} | head -c 4000"
                for n in clipped_lines[:5])
            foot.append(f"[有 {len(clipped_lines)} 行超长被截断，取整行：{hints}]")
        # 读后写守卫数据源：文本读成功才登记（图片不参与 Edit 守卫）
        ctx.files_touched[file_key(raw)] = st.st_mtime
        return "\n".join(out + foot)

    @staticmethod
    def _missing_hint(path: Path) -> str:
        """不存在时的提示：列同目录相邻文件（模型常拼错文件名）。"""
        parent = path.parent
        if not parent.is_dir():
            return f"文件不存在：{path}（父目录 {parent} 也不存在）"
        siblings = sorted(p.name for p in parent.iterdir())[:12]
        if not siblings:
            return f"文件不存在：{path}（目录 {parent} 为空）"
        return f"文件不存在：{path}。同目录相邻文件：{', '.join(siblings)}"


tool = ReadTool()             # ToolRegistry.default() 收集的模块级实例
