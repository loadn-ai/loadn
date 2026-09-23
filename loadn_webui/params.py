"""会话级参数覆盖（属性面板「模型参数」段的后端语义）。

覆盖链：会话 params_json > profile（registry.yaml）> 引擎/config 默认。
PATCH 整体替换（与 skills/mcp 同语义）；键值 null/缺省 = 该项跟随 profile。

两个 0 哨兵是刻意的：PATCH null 表「清除覆盖」，而 max_turns /
rotate_input_tokens 的 profile None 语义恰是「不限制/禁用轮换」——
会话想表达这个态只能用 0（effective 换算回 None）。
"""
from __future__ import annotations

import json

from .util import get_logger

log = get_logger(__name__)

ALLOWED = ("model", "effort", "max_turns", "timeout_s", "stall_timeout_s",
           "rotate_input_tokens")

_EFFORTS = ("low", "medium", "high")


def validate(body_value) -> str | None:
    """PATCH body["params"] → 落库 JSON 字符串（None=存 NULL 全清）。

    ValueError 带中文理由（routes 映射 400）。
    """
    if body_value is None:
        return None                      # 整体清除 → 跟随 profile
    if not isinstance(body_value, dict):
        raise ValueError("params 需为对象（或 null 清除全部覆盖）")
    out: dict = {}
    for k, v in body_value.items():
        if k not in ALLOWED:
            raise ValueError(f"未知参数 {k!r}（可选：{'/'.join(ALLOWED)}）")
        if v is None:
            continue                     # 单键清除（跟随 profile）
        if k == "model":
            if not isinstance(v, str) or not (s := v.strip()) or len(s) > 100:
                raise ValueError("model 需为非空字符串（≤100 字符）")
            out[k] = s
        elif k == "effort":
            if v not in _EFFORTS:
                raise ValueError(f"effort 需为 {'|'.join(_EFFORTS)}")
            out[k] = v
        elif k == "max_turns":
            # 0 = 不限制（生效 None）；范围与 profile 收敛闸同量级
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 200:
                raise ValueError("max_turns 需为 0-200 的整数（0=不限制）")
            out[k] = v
        elif k in ("timeout_s", "stall_timeout_s"):
            if not isinstance(v, int) or isinstance(v, bool) or not 30 <= v <= 86400:
                raise ValueError(f"{k} 需为 30-86400 的整数（秒）")
            out[k] = v
        else:                            # rotate_input_tokens
            # 0 = 禁用轮换（生效 None）
            if (not isinstance(v, int) or isinstance(v, bool)
                    or not 0 <= v <= 10_000_000):
                raise ValueError("rotate_input_tokens 需为 0-10000000 的整数（0=禁用轮换）")
            out[k] = v
    if not out:
        return None                      # 清完剩空壳 → 也存 NULL
    return json.dumps(out, ensure_ascii=False)


def load(raw) -> dict:
    """库里的 params_json → dict；坏数据 fail-open 到 {}（跟 profile）+ log。"""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {k: v for k, v in d.items() if k in ALLOWED} if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        log.warning("params_json 解析失败，回退 profile 默认：%r", raw)
        return {}


def effective(prof, ov: dict) -> dict:
    """六键生效值：覆盖优先（0 哨兵换算 None），否则 profile 侧。"""
    def _get(k):
        if k in ov:
            v = ov[k]
            if k in ("max_turns", "rotate_input_tokens") and v == 0:
                return None
            return v
        return getattr(prof, k)
    return {k: _get(k) for k in ALLOWED}


def defaults(prof) -> dict:
    """profile 侧默认值（UI「跟随 profile（X）」提示用）。"""
    return {k: getattr(prof, k) for k in ALLOWED}
