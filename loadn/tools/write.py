"""Write 工具——整文件写入，带「先读后写」宪法级守卫。

目标已存在且本会话未 Read 过 → 拒绝覆盖（防止盲写毁掉未知内容）；
新建文件不受限。写后更新 files_touched（此后 Edit/再次 Write 放行）。
"""
from __future__ import annotations

from pathlib import Path

from loadn.constants import WRITE_MAX_BYTES
from loadn.tools.base import Tool, ToolContext, ToolError, with_file_lock
from loadn.tools.read import file_key


class WriteTool(Tool):
    """整文件写入（新文件或本会话已读过的文件）。"""

    name = "Write"
    description = (
        "整文件写入（父目录自动创建）。宪法级规则：目标已存在且本会话"
        "未 Read 过时拒绝覆盖——先 Read 再 Write。内容上限 "
        f"{WRITE_MAX_BYTES // 1024}KB，超限请分片写。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件路径"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        raw = args.get("file_path")
        content = args.get("content")
        if not raw or not isinstance(raw, str):
            raise ToolError("缺少必填参数 file_path")
        if not isinstance(content, str):
            raise ToolError("缺少必填参数 content（字符串）")
        path = Path(raw)
        if path.is_dir():
            raise ToolError(f"{path} 是目录，不能作为写入目标")
        payload = content.encode("utf-8")
        if len(payload) > WRITE_MAX_BYTES:
            raise ToolError(
                f"content {len(payload) / 1024:.0f}KB 超过上限 "
                f"{WRITE_MAX_BYTES // 1024}KB（WRITE_MAX_BYTES），拒绝写入；"
                "请拆分内容分多次写，或改用 Bash 写大文件")

        async def _critical() -> str:
            # 守卫（已存在须先 Read）与写入同锁（P0-1）——与 Edit 互斥，
            # 防并行方在「检查通过→落盘」窗口覆盖对方的编辑
            key = file_key(raw)
            if path.exists() and key not in ctx.files_touched:
                raise ToolError(
                    f"文件已存在但本会话未读取过，先用 Read 读取再覆盖：{path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            ctx.files_touched[key] = path.stat().st_mtime
            return f"已写入 {path}（{len(content.splitlines())} 行）"

        return await with_file_lock(raw, _critical)


tool = WriteTool()            # ToolRegistry.default() 收集的模块级实例
