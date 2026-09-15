"""集中式文本截断——各工具输出纪律（constants.py 里的上限值）的公共实现。

超限不整段丢弃：clip_middle 保首尾去中间（Bash 输出 30k 上限等场景，
错误信息多在两端），clip_head 保头部去尾部（Read 单行截断 / WebFetch 正文
截断等场景），并在切口处标注 `…[截断 N 字符]…` 让模型明确知道丢了多少。
"""
from __future__ import annotations


class Truncator:
    """无状态截断工具（静态方法集合，各工具模块直接引用）。"""

    @staticmethod
    def clip_middle(text: str, limit: int) -> str:
        """超限截中间保首尾；len(text) <= limit 时原样返回。

        标注自身占额度：按其长度上界（约 24 字符）预留，保证结果总长
        不超 limit。
        """
        if len(text) <= limit:
            return text
        keep = max(1, (limit - 24) // 2)
        dropped = len(text) - 2 * keep
        marker = f"…[截断 {dropped} 字符]…"
        return text[:keep] + marker + text[len(text) - keep:]

    @staticmethod
    def clip_head(text: str, limit: int) -> str:
        """超限保头部去尾部（单行截断 / 正文截断用）。"""
        if len(text) <= limit:
            return text
        return text[:limit] + f"…[截断 {len(text) - limit} 字符]…"
