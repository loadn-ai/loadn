"""skill 中文简介：英文 description → 中文一两句话（doubao 小模型 + kv 缓存）。

管理页/市场卡片对「无汉字」的描述自动请求翻译；缓存键含原文哈希，
原文改了自动重译。已是中文的描述不送译（不耗模型）；失败静默 zh=None
（前端回退显示原文）。
"""
from __future__ import annotations

import asyncio
import hashlib
import re

from .config import CONFIG
from .util import get_logger

log = get_logger(__name__)

_CJK = re.compile(r"[一-鿿]")
_MAX_ITEMS = 30            # 单批上限（市场一页 ~20）
_MAX_DESC = 1200           # 送译截断（官方 docx 描述 ~900 字符）
_CONCURRENCY = 4
_SYSTEM = (
    "你翻译 AI agent 技能库里的技能简介。把给定的英文技能名和描述翻译成流畅的中文，"
    "1-2 句话、60 字以内；保留关键英文术语（如 docx、PDF、Excel、API）不译；"
    "简介中不要重复技能名、不要以技能名开头；"
    "不要引号、不要解释，直接输出中文简介本身。"
)


def cache_key(name: str, description: str) -> str:
    h = hashlib.sha1(f"{name}\x00{description}".encode()).hexdigest()[:16]
    return f"skill_zh:{h}"


def needs_zh(description: str) -> bool:
    """无汉字 → 需要翻译（中文描述直接跳过，不耗模型）。"""
    return bool(description.strip()) and not _CJK.search(description)


async def translate_one(name: str, description: str) -> str | None:
    """单条翻译（titlegen 未启用/未配 key 返回 None；接口失败记日志返回 None）。"""
    from .titlegen import _chat
    cfg = CONFIG.titlegen
    if not (cfg.enabled and cfg.api_key):
        return None
    try:
        raw = await _chat(_SYSTEM, f"技能名: {name}\n描述: {description[:_MAX_DESC]}",
                          max_tokens=200)
    except Exception as e:  # noqa: BLE001 —— 翻译失败不影响列表
        log.warning("skill 简介翻译失败 %s: %s", name, e)
        return None
    t = re.sub(r"\s+", " ", (raw or "").strip()).strip("\"'“” ")
    return t[:200] or None


async def translate_batch(items: list[dict]) -> list[dict]:
    """[{name, description}] → [{name, zh}]：kv 缓存命中秒回，缺失并发补译并落缓存。"""
    from . import db as db_mod
    items = [x for x in items if isinstance(x, dict)][:_MAX_ITEMS]
    out: dict[int, str | None] = {}
    todo: list[tuple[int, str, str]] = []
    with db_mod.conn() as c:
        for i, it in enumerate(items):
            name = str(it.get("name") or "")[:80]
            desc = str(it.get("description") or "")
            if not needs_zh(desc):
                out[i] = None
                continue
            hit = db_mod.kv_get(c, cache_key(name, desc))
            if hit is not None:
                out[i] = hit
            else:
                todo.append((i, name, desc))
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def run(i: int, name: str, desc: str) -> None:
        async with sem:
            out[i] = await translate_one(name, desc)

    if todo:
        await asyncio.gather(*(run(*t) for t in todo))
        fresh = [(cache_key(n, d), out[i]) for i, n, d in todo if out[i]]
        if fresh:
            with db_mod.conn() as c:
                for k, v in fresh:
                    db_mod.kv_set(c, k, v)
    return [{"name": str(it.get("name") or "")[:80], "zh": out.get(i)}
            for i, it in enumerate(items)]
