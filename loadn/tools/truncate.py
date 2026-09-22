"""集中式文本截断——各工具输出纪律（constants.py 里的上限值）的公共实现。

超限不整段丢弃：clip_middle 保首尾去中间（Bash 输出 30k 上限等场景，
错误信息多在两端）并带 Warning 头（codex 纪律：原尺寸可见，模型知道丢
了多少），clip_head 保头部去尾部（Read 单行截断 / WebFetch 正文截断等
场景）。截断即行动：各工具在截断时附下一步操作（Read 给续读 offset、
Bash 给全文落盘路径——见各工具实现）。
"""
from __future__ import annotations


class Truncator:
    """无状态截断工具（静态方法集合，各工具模块直接引用）。"""

    @staticmethod
    def clip_middle(text: str, limit: int) -> str:
        """超限截中间保首尾 + Warning 头；len(text) <= limit 时原样返回。

        头与标注自身占额度，保证结果总长不超 limit。
        """
        if len(text) <= limit:
            return text
        header = f"Warning: 输出已截断（原文 {len(text)} 字符，已保首尾）\n"
        keep = max(1, (limit - len(header) - 24) // 2)
        dropped = len(text) - 2 * keep
        marker = f"…[截断 {dropped} 字符]…"
        return header + text[:keep] + marker + text[len(text) - keep:]

    @staticmethod
    def clip_head(text: str, limit: int) -> str:
        """超限保头部去尾部（单行截断 / 正文截断用）。"""
        if len(text) <= limit:
            return text
        return text[:limit] + f"…[截断 {len(text) - limit} 字符]…"
