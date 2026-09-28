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
           "rotate_input_tokens", "egress", "sandbox")

_EFFORTS = ("low", "medium", "high")
# 会话级外联档位（null/缺省=跟随全局 config；egress 代理判定链见 egress_proxy）
EGRESS_MODES = ("off", "warn", "enforce")
# 会话级沙箱档位（null/缺省=跟随全局；枚举与 config.SANDBOX_TIERS 同源）。
# 用途：运维/宿主接管会话放开隔离（off）或收紧（bwrap）——下一 turn 生效，
# spawn 期语义与全局档一致（含降档审计/sandbox_tier 遥测）
from .config import SANDBOX_TIERS  # noqa: E402


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
        elif k == "egress":
            if v not in EGRESS_MODES:
                raise ValueError(f"egress 需为 {'|'.join(EGRESS_MODES)}（null=跟随全局）")
            out[k] = v
        elif k == "sandbox":
            if v not in SANDBOX_TIERS:
                raise ValueError(f"sandbox 需为 {'|'.join(SANDBOX_TIERS)}（null=跟随全局）")
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
    """生效值：覆盖优先（0 哨兵换算 None），否则 profile 侧。
    egress 无 profile 侧——None=跟随全局 config（代理判定时解析）。"""
    def _get(k):
        if k in ov:
            v = ov[k]
            if k in ("max_turns", "rotate_input_tokens") and v == 0:
                return None
            return v
        # egress/sandbox 无 profile 侧——None=跟随全局（spawn/代理判定时解析）
        return None if k in ("egress", "sandbox") else getattr(prof, k)
    return {k: _get(k) for k in ALLOWED}


def defaults(prof) -> dict:
    """profile 侧默认值（UI「跟随 profile（X）」提示用）。

    egress/sandbox 无 profile 侧（会话级专属覆盖键）——恒 None=跟随全局。
    """
    return {k: (None if k in ("egress", "sandbox") else getattr(prof, k))
            for k in ALLOWED}


def session_egress_override(raw) -> str | None:
    """库里的 params_json → 会话级 egress 档位覆盖（None=跟随全局）。

    代理判定每连接调一次；坏数据 fail-open 到跟随全局 + log。
    """
    ov = load(raw).get("egress")
    return ov if ov in EGRESS_MODES else None
