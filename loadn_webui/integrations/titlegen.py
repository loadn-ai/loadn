"""自动标题：首条消息 → 外部小模型（火山方舟 OpenAI 兼容）生成任务标题。

触发链路：engine.submit → kv 标记 title_auto:<sid> 仍存在 且 配置启用 →
后台任务生成 → 落库 + session_meta 事件（侧边栏即时刷新）。

优先级：用户显式标题 / 手动改名 > 自动标题（成功或手动改名后清标记，只生成一次）。
失败静默跳过（打日志），绝不阻塞 turn。
"""
from __future__ import annotations

import re

from ..config import CONFIG
from ..util import get_logger

log = get_logger(__name__)

FLAG = "title_auto:{sid}"          # kv 标记：会话标题可被自动生成
_MAX_TITLE = 40                    # db 上限 80，展示留余量

_SYSTEM = ("你为任务平台起标题。根据用户消息生成一个简短的中文标题：不超过14个字，"
           "概括任务主题；不要引号、书名号、句号或 emoji；直接输出标题本身，不要任何解释；"
           "消息是外语也输出中文标题。")


def _sanitize(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r'^[\s"“”‘’\'《》「」『』]+', "", t)
    t = re.sub(r'[\s"“”‘’\'《》「」『』。.，,；;！!？?]+$', "", t)
    t = re.sub(r"\s+", " ", t)
    return t[:_MAX_TITLE]


async def _chat(system: str, user: str, max_tokens: int = 64) -> str:
    """单轮 chat/completions；异常上抛由调用方决定降级。"""
    import httpx
    cfg = CONFIG.titlegen
    base = cfg.api_base.rstrip("/")
    base = base.removesuffix("/chat/completions")
    async with httpx.AsyncClient(timeout=20) as cl:
        r = await cl.post(f"{base}/chat/completions",
                          headers={"Authorization": f"Bearer {cfg.api_key}"},
                          json={"model": cfg.model, "max_tokens": max_tokens,
                                "temperature": 0.3,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}]})
        r.raise_for_status()
        data = r.json()
        return str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "")


async def generate_title(text: str) -> str | None:
    """纯生成（配置未启用/未配 key 返回 None；接口失败记日志返回 None）。"""
    cfg = CONFIG.titlegen
    if not (cfg.enabled and cfg.api_key and text.strip()):
        return None
    try:
        raw = await _chat(_SYSTEM, (text or "")[:2000])
    except Exception as e:  # noqa: BLE001 —— 标题失败不影响任务
        log.warning("自动标题生成失败: %s", e)
        return None
    return _sanitize(raw) or None


async def maybe_auto_title(sid: str, text: str) -> None:
    """标记仍在（未被手动改名/尚未成功过）才会写标题；成功后清标记。"""
    from .. import db as db_mod
    from ..engine import ENGINE

    title = await generate_title(text)
    if not title:
        return
    with db_mod.conn() as c:
        if db_mod.kv_get(c, FLAG.format(sid=sid)) is None:
            return                      # 期间已被手动改名或已生成过
        db_mod.kv_del(c, FLAG.format(sid=sid))
        db_mod.update_session(c, sid, title=title)
    ENGINE.publish(sid, "session_meta", {"title": title})
    log.info("自动标题 sid=%s → %s", sid, title)
