"""REST API 面（/api 前缀）——按域拆分的路由聚合。

认证（承袭前身 webapp 语义）：默认 127.0.0.1 免认证；token 非空时
强制 Bearer / X-Loadn-Token / ?token=（query 专为 EventSource 无法设头保留）。
各域路由在同名子模块；本 __init__ 只做聚合与兼容面 re-export。
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    activity,
    admin,
    approvals,
    artifacts,
    files,
    hooks,
    memory,
    projects,
    schedules,
    sessions,
    settings,
    skills,
    stats,
    tools,
    turns,
)

router = APIRouter()
for _m in (skills, tools, settings, projects, sessions, turns, admin,
           approvals, schedules, files, artifacts, stats, hooks, memory,
           activity):
    router.include_router(_m.router)

# 兼容面：历史 routes.py 模块级符号（测试/外部脚本仍可从本包寻址）
from ._common import _ARCHIVE_MAX_BYTES  # noqa: E402,F401
from .admin import kill_all_clear  # noqa: E402,F401
from .files import MAX_UPLOAD_BYTES  # noqa: E402,F401
