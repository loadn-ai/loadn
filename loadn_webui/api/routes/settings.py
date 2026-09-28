"""平台设置面：titlegen/run/convergence/resources/notify/engines/claude/share/pricing。"""
from __future__ import annotations

from fastapi import APIRouter

from ... import settings_admin

router = APIRouter(prefix="/api")

from ._common import _http_err


@router.get("/settings")
def get_settings():
    return settings_admin.get_settings()
@router.put("/settings/titlegen")
def put_titlegen(body: dict):
    try:
        return settings_admin.put_titlegen(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/settings/run")
def put_run(body: dict):
    try:
        return settings_admin.put_run(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/settings/convergence")
def put_convergence(body: dict):
    try:
        return settings_admin.put_convergence(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.post("/settings/titlegen/test")
async def test_titlegen():
    try:
        return await settings_admin.test_titlegen()
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/settings/resources")
def put_resources(body: dict):
    try:
        return settings_admin.put_resources(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/settings/notify")
def put_notify(body: dict):
    try:
        return settings_admin.put_notify(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.post("/settings/notify/test")
async def test_notify():
    try:
        return await settings_admin.test_notify()
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/settings/engines")
def put_engines(body: dict):
    try:
        return settings_admin.put_engines(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/settings/claude")
def put_claude(body: dict):
    try:
        return settings_admin.put_claude(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/settings/share")
def put_share(body: dict):
    try:
        return settings_admin.put_share(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/settings/pricing")
def put_pricing(body: dict):
    try:
        return settings_admin.put_pricing(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.post("/settings/resources/test")
async def test_resources(only: str = ""):
    try:
        names = [x for x in only.split(",") if x] or None
        return await settings_admin.test_resources(names)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
