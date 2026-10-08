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
            "SELECT b.chat_id, b.session_id, b.last_turn_id, b.created_at, "
            "b.owner_id, u.username AS owner_name "
            "FROM channel_bindings b "
            "LEFT JOIN users u ON u.id=b.owner_id "
            "ORDER BY b.created_at")]
    from ...integrations.channels import get_service
    return {"config": {"telegram_enabled": CONFIG.channels.telegram_enabled,
                       "telegram_allow": CONFIG.channels.telegram_allow,
                       "token_set": token_set},
            "status": get_service(None).status,
            "bindings": binds}


@router.put("/channels/bindings/{chat_id}")
def set_binding_owner(chat_id: str, body: dict):
    """多用户批3：把 chat 绑定认领给用户（admin）——该 chat 经渠道建的
    会话/回信归属此用户（渠道线程无 cookie，归属由认领表决定）。"""
    from ... import db as db_mod
    from ...security import userauth as ua
    from ...security.audit import audit
    username = str(body.get("owner") or "").strip()
    uid = None
    if username:
        row = ua.get_user_by_name(username)
        if row is None:
            raise HTTPException(404, f"用户不存在: {username}")
        uid = row["id"]
    with db_mod.conn() as c:
        cur = c.execute(
            "UPDATE channel_bindings SET owner_id=? WHERE chat_id=?",
            (uid, chat_id))
        if cur.rowcount == 0:
            raise HTTPException(404, f"绑定不存在: {chat_id}（先在 Telegram "
                                     "侧 /new 或 /bind 建立）")
    audit("channel", {"action": "binding_owner_set", "chat_id": chat_id,
                      "owner": username or None})
    return {"ok": True}


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
    # 七轮修（设定审计#5）：enabled 切换热起/停轮询线程——原只在 lifespan
    # 起线程判一次：UI 勾选启用后实际收不到消息直到重启（注释宣称热生效
    # 只对白名单成立）。重启线程前 stop 旧的（旧线程长轮询醒来即退；短暂
    # 双线程窗口 Telegram 侧 409 退避可忍）
    if "telegram_enabled" in updates:
        from ...integrations.channels import get_service
        svc = get_service(None)
        svc.stop()
        if updates["telegram_enabled"]:
            svc.start()
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
