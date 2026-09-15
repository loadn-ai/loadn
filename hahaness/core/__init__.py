"""core 层公共出口（循环/会话/压缩/权限/钩子/子代理/装配）。"""
from hahaness.core.compactor import Compactor
from hahaness.core.loop import AgentCore, LoopSettings, StopFlag, TurnSummary
from hahaness.core.session import SessionManager

__all__ = ["AgentCore", "Compactor", "LoopSettings", "SessionManager",
           "StopFlag", "TurnSummary"]
