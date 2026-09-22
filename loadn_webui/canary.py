"""canary 蜜罐（W5.5）+ 会话熔断（W6.4 kill switch 联动）。

- 每会话 workspace 布置 notes/.canary_tokens.md：3 个格式真实、值唯一
  可溯源的伪 key（AKIA/ghp_/sk- 样）。agent 读走并外发（mail 正文/curl
  参数）→ policy.check_command 命中 → block + audit(canary_hit) + 会话
  熔断（locked：新 turn 拒绝）。
- 诚实口径：canary 防的是「数据外渗」的可检测性——命中即证成泄露企图，
  不宣称能防所有外渗通道（egress 代理 enforce 才是物理层，W5.1）。
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from .audit import audit
from .config import PATHS

_TEMPLATE = """<!-- 平台布放的泄露指示物（canary）：值均无效，任何外发命中即熔断会话 -->
# 内部对接凭据（勿外传）

- AWS 备用密钥: AKIA{aws}
- GitHub 备用令牌: ghp_{ghp}
- API 备用密钥: sk-cf-live-{sk}

（此文件属安全监控设施，任务无关，请勿引用其内容）
"""


def _canary_dir() -> Path:
    return PATHS["var"] / "canaries"


def plant(sid: str) -> list[str]:
    """布置会话 canary：workspace 文件 + 平台登记（值返回给检测器缓存）。"""
    ws = PATHS["workspace"] / sid
    vals = {
        "aws": secrets.token_hex(8).upper()[:14],
        "ghp": secrets.token_hex(20),
        "sk": secrets.token_hex(16),
    }
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "notes").mkdir(exist_ok=True)
    (ws / "notes" / ".canary_tokens.md").write_text(
        _TEMPLATE.format(**vals), encoding="utf-8")
    d = _canary_dir()
    d.mkdir(parents=True, exist_ok=True)
    tokens = [f"AKIA{vals['aws']}", f"ghp_{vals['ghp']}", f"sk-cf-live-{vals['sk']}"]
    (d / f"{sid}.json").write_text(json.dumps({"sid": sid, "tokens": tokens}))
    return tokens


_all_tokens: list[str] | None = None


def all_tokens() -> list[str]:
    """全部在册 canary 值（进程缓存；新会话 plant 后由写侧失效缓存）。"""
    global _all_tokens
    if _all_tokens is None:
        d = _canary_dir()
        out: list[str] = []
        if d.exists():
            for f in d.glob("*.json"):
                try:
                    out.extend(json.loads(f.read_text()).get("tokens") or [])
                except (OSError, json.JSONDecodeError):
                    continue
        _all_tokens = out
    return _all_tokens


def invalidate_cache() -> None:
    global _all_tokens
    _all_tokens = None


def hit(subject: str) -> str | None:
    """外发内容命中 canary → 返回命中的值（调用方负责 block+熔断）。"""
    for t in all_tokens():
        if t and t in (subject or ""):
            return t
    return None


# ---------------------------------------------------------------- 会话熔断

def lock_session(sid: str, reason: str) -> None:
    """熔断：locked 标记（engine.submit 拒绝新消息）+ 审计。"""
    d = PATHS["run"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "locked").mkdir(exist_ok=True)
    (d / "locked" / sid).write_text(reason)
    audit("kill_switch", {"sid": sid, "reason": reason, "level": "session"},
          sid=sid)


def is_locked(sid: str) -> str | None:
    f = PATHS["run"] / "locked" / sid
    try:
        return f.read_text() or "locked"
    except OSError:
        return None


def unlock_session(sid: str) -> bool:
    f = PATHS["run"] / "locked" / sid
    if f.exists():
        f.unlink()
        return True
    return False
