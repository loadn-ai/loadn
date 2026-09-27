"""tools 面：全局 MCP servers 与内建开关。"""
from __future__ import annotations

from fastapi import APIRouter

from ...integrations import mcp_admin

router = APIRouter(prefix="/api")

from ._common import _http_err


@router.get("/tools")
def get_tools():
    out = mcp_admin.list_servers()
    out.update(mcp_admin.tools_overview())
    return out
@router.put("/tools/mcp/{name}")
def put_mcp_server(name: str, body: dict):
    try:
        return mcp_admin.put_server(name, body.get("spec") or body or {})
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.delete("/tools/mcp/{name}")
def delete_mcp_server(name: str):
    try:
        return mcp_admin.delete_server(name)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/tools/profile/{profile}")
def put_profile_tools(profile: str, body: dict):
    try:
        return mcp_admin.put_profile_tools(profile, body.get("disallowed_tools") or [])
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
