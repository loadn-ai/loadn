"""P9 渠道管理面（admin）：配置读写、token 入 vault、健康探测、绑定清单。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ...config import CONFIG
from ...integrations.channels import VAULT_KEY, TelegramAPI
from ...security import vault

router = APIRouter(prefix="/api/admin")


@router.get("/channels")
def channels_overview():
    """状态卡数据源：配置（token 只报有无）+ 轮询状态 + 绑定。"""
    token_set = bool((vault.get(VAULT_KEY) or {}).get("password"))
    with db_mod.conn() as c:
        binds = [db_mod.to_dict(r) for r in c.execute(
            "SELECT chat_id, session_id, last_turn_id, created_at "
            "FROM channel_bindings ORDER BY created_at")]
    from ...integrations.channels import get_service
    return {"config": {"telegram_enabled": CONFIG.channels.telegram_enabled,
                       "telegram_allow": CONFIG.channels.telegram_allow,
                       "token_set": token_set},
            "status": get_service(None).status,
            "bindings": binds}


@router.put("/channels")
def channels_put(body: dict):
    """启停 + 白名单（写 config.yaml 热生效——重启轮询线程语义见下）。"""
    from ... import settings_admin
    updates: dict = {}
    if "telegram_enabled" in body:
        updates["telegram_enabled"] = bool(body["telegram_enabled"])
    if "telegram_allow" in body:
        allow = body["telegram_allow"]
        if not isinstance(allow, list):
            raise HTTPException(400, "telegram_allow 须为字符串数组")
        updates["telegram_allow"] = [str(x).strip() for x in allow
                                     if str(x).strip()]
    if not updates:
        raise HTTPException(400, "无可更新字段")
    settings_admin._write_section("channels", updates)   # config.yaml 持久化
    for k, v in updates.items():                          # 热生效（轮询线程即读即用）
        setattr(CONFIG.channels, k, v)
    return {"ok": True}


@router.put("/channels/token")
def channels_token(body: dict):
    """bot token 只入 vault（password 位，list 默认脱敏）。空串=清除。"""
    tok = str(body.get("token") or "").strip()
    if tok:
        vault.put(VAULT_KEY, password=tok)
    else:
        vault.delete(VAULT_KEY)
    return {"ok": True, "token_set": bool(tok)}


@router.get("/channels/probe")
def channels_probe():
    """健康探测：getMe 真调一次（token/网络/代理 三关）。"""
    try:
        me = TelegramAPI().call("getMe", {})
        return {"ok": True, "bot": me.get("username"),
                "id": me.get("id")}
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
