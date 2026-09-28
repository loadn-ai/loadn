"""模型目录 lookup（P1-5，pi models.generated / openclaw catalog 同构）。

窗口/输出上限/缓存 TTL/价格不再散硬编码：数据在 loadn/models.json
（版本化，变更走 PR），本模块是唯一读口。

- 变体后缀：`glm-5.3[1m]` = 基模型窗口覆盖为 1M（claude CLI 客户端
  标记；API 侧剥后缀见 providers.api_model_name）。[2m] 同理。
- 未知模型：**保守 128k + 每进程一次 warning**（旧版静默 200k 高估窗口
  → 大上下文会话在 90%+ 才压缩而炸限——宁可低估早压）。
- 价格 null=未知（P1-6 保活决策遇到 null 直接跳过，不臆造）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from loadn.util import get_logger

log = get_logger(__name__)

FALLBACK_WINDOW = 128_000
FALLBACK_MAX_OUTPUT = 8_192
FALLBACK_CACHE_TTL_S = 300           # Anthropic 标准 5min；P1-6 可被 env 覆盖

_VARIANT_RE = re.compile(r"\[(\d+)m\]$")
_CATALOG_PATH = Path(__file__).resolve().parent.parent / "models.json"
_catalog: dict | None = None
_warned: set[str] = set()


@dataclass(frozen=True)
class ModelSpec:
    name: str                        # 归一后的基模型名（无变体后缀）
    context_window: int
    max_output: int | None = None
    prompt_cache_ttl_s: int | None = None
    input_price_per_m: float | None = None
    output_price_per_m: float | None = None
    variant_window: int | None = None    # [Nm] 覆盖后的实际窗口（无变体=None）


def load_catalog() -> dict:
    """目录（懒加载一次；坏文件 → 空 dict + warning，全部走 fallback）。"""
    global _catalog
    if _catalog is None:
        try:
            _catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
            if not isinstance(_catalog, dict) or \
                    not isinstance(_catalog.get("models"), dict):
                raise ValueError("models.json 顶层需为 {version, models:{...}}")
        except (OSError, ValueError) as e:
            log.warning("模型目录不可读，全部走 fallback（%s）：%s",
                        FALLBACK_WINDOW, e)
            _catalog = {"models": {}}
    return _catalog["models"]


def lookup(model: str) -> ModelSpec:
    """模型名（含变体后缀）→ 规格。未知模型 fallback + 每进程一次 warning。"""
    raw = (model or "").strip()
    base = raw
    variant_window = None
    if m := _VARIANT_RE.search(raw):
        base = raw[:m.start()].strip()
        variant_window = int(m.group(1)) * 1_000_000
    entry = load_catalog().get(base)
    if entry is None:
        if base and base not in _warned:
            _warned.add(base)
            log.warning("模型 %r 不在目录（models.json）——窗口按保守 "
                        "%dk 估算（补目录可消除本告警）", base,
                        FALLBACK_WINDOW // 1000)
        return ModelSpec(name=base or "unknown",
                         context_window=variant_window or FALLBACK_WINDOW,
                         variant_window=variant_window)
    return ModelSpec(
        name=base,
        context_window=variant_window or int(entry.get("context_window")
                                             or FALLBACK_WINDOW),
        max_output=entry.get("max_output"),
        prompt_cache_ttl_s=entry.get("prompt_cache_ttl_s"),
        input_price_per_m=entry.get("input_price_per_m"),
        output_price_per_m=entry.get("output_price_per_m"),
        variant_window=variant_window)


def window_of(model: str) -> int:
    """便捷口：build._window_of 的目录化实现（变体覆盖 > 目录 > fallback）。"""
    return lookup(model).context_window


def cache_ttl(model: str) -> int:
    """缓存 TTL（目录未知 → 5min 默认；P1-6 决策用）。"""
    ttl = lookup(model).prompt_cache_ttl_s
    return ttl if ttl and ttl > 0 else FALLBACK_CACHE_TTL_S
