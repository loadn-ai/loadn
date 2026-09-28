"""ToolRegistry——工具注册表与默认装配。

default() 自动 import 本包所有模块，收集模块级 Tool 实例（各工具模块
底部 `tool = XxxTool()` 形态）；AUTO_REGISTER = False 的模块跳过（Task
占位：core/subagent.py 集成阶段手动注册）。read_only_only=True 只收
read_only 工具——Explore 型子代理/plan 模式的放行集合。
"""
from __future__ import annotations

import importlib
import pkgutil

from loadn.tools.base import Tool, ToolContext, ToolError
from loadn.types import ToolDef

__all__ = ["Tool", "ToolContext", "ToolError", "ToolRegistry"]


class ToolRegistry:
    """name → Tool 实例表（默认装配 + 显式增删）。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册：{tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def defs(self, disallow: list[str] | None = None) -> list[ToolDef]:
        """Provider 层工具声明（排除 disallow 名单）。"""
        blocked = set(disallow or [])
        return [tool.def_() for name, tool in sorted(self._tools.items())
                if name not in blocked]

    @classmethod
    def default(cls, disallow: list[str] | None = None,
                read_only_only: bool = False) -> ToolRegistry:
        """自动装配：扫描本包各模块收集 Tool 实例（不含 AUTO_REGISTER=False）。"""
        reg = cls()
        for mod_info in pkgutil.iter_modules(__path__):
            module = importlib.import_module(f"{__name__}.{mod_info.name}")
            if not getattr(module, "AUTO_REGISTER", True):
                continue
            for value in vars(module).values():
                if not isinstance(value, Tool):
                    continue
                if read_only_only and not value.read_only:
                    continue
                if disallow and value.name in disallow:
                    continue
                try:
                    reg.register(value)
                except ValueError:
                    pass              # 同一实例被多个模块 re-export，保留首个
        return reg
