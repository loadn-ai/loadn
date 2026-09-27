"""运维通知（P2-2）：bark / Server酱 / Telegram 三通道，发给用户自己。

证书项目的教训：冷却到期、徽章邮件到达、发证同步完成，用户只能开着
WebUI 盯。本模块给 engine（turn error / done）与 scheduler（调度触发）
提供 fire-and-forget 推送；密钥只进 config.yaml notify 段，不进 skill 文本。

与 wechat-send（发给联系人）语义不同——这是平台运维通知。
"""
from __future__ import annotations

import asyncio

from ..config import CONFIG
from ..util import get_logger

log = get_logger(__name__)


# 事件默认开关（config notify.events 未覆盖部分按此；on_scheduled 默认开——
# 调度唤醒是用户显式设的等待，理应被告知；turn_done 默认关防刷屏）
_EVENT_DEFAULTS = {"on_error": True, "on_scheduled": True, "on_turn_done": False}


def event_enabled(name: str) -> bool:
    ev = {**_EVENT_DEFAULTS, **(CONFIG.notify.events or {})}
    return bool(ev.get(name, False))


async def send(title: str, body: str = "", event: str = "") -> bool:
    """推送一条通知。event 给定且配置关闭 → 静默跳过。异常吞掉（通知永不拖垮主流程）。"""
    cfg = CONFIG.notify
    if not cfg.provider:
        return False
    if event and not event_enabled(event):
        return False
    try:
        if cfg.provider == "bark":
            await _bark(title, body)
        elif cfg.provider == "serverchan":
            await _serverchan(title, body)
        elif cfg.provider == "telegram":
            await _telegram(title, body)
        else:
            log.warning("未知 notify.provider: %s", cfg.provider)
            return False
        return True
    except Exception:
        log.exception("通知推送失败（provider=%s）", cfg.provider)
        return False


def fire(title: str, body: str = "", event: str = "") -> None:
    """同步上下文的 fire-and-forget 入口（engine/scheduler 钩子用）。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send(title, body, event))
    except RuntimeError:          # 无运行 loop（CLI 同步路径）→ 直接跑
        asyncio.run(send(title, body, event))


async def _bark(title: str, body: str) -> None:
    from .resources import _post
    base = CONFIG.notify.bark_url.rstrip("/")
    resp = await _post(f"{base}", json={"title": title, "body": body, "group": "loadn"},
                       timeout=10.0)
    if resp.status_code != 200:
        raise RuntimeError(f"bark HTTP {resp.status_code}: {resp.text[:120]}")


async def _serverchan(title: str, body: str) -> None:
    from .resources import _post
    key = CONFIG.notify.serverchan_key
    resp = await _post(f"https://sctapi.ftqq.com/{key}.send",
                       data={"title": title, "desp": body}, timeout=10.0)
    if resp.status_code != 200:
        raise RuntimeError(f"serverchan HTTP {resp.status_code}: {resp.text[:120]}")


async def _telegram(title: str, body: str) -> None:
    from .resources import _post
    cfg = CONFIG.notify
    resp = await _post(
        f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage",
        json={"chat_id": cfg.telegram_chat_id, "text": f"{title}\n{body}".strip()},
        timeout=15.0, proxy=CONFIG.resources.proxy or None)   # 墙内走 clash
    if resp.status_code != 200:
        raise RuntimeError(f"telegram HTTP {resp.status_code}: {resp.text[:120]}")


# ---------------------------------------------------------------- 连通性
async def ping() -> dict:
    """设置页「测试全部」/ wd r ping 用：真实推一条测试消息。"""
    if not CONFIG.notify.provider:
        return {"ok": False, "msg": "未配置 provider"}
    ok = await send("loadn 通知测试", "配置生效（此条为连通性探测）")
    return {"ok": ok, "msg": f"已推送（{CONFIG.notify.provider}）" if ok else "推送失败，看服务日志"}
