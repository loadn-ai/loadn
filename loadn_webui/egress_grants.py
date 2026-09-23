"""审批式临时 egress 授权（任务级、限时、fail-closed）。

用户在 approve 通道批准 action_type=egress 后落一张**内存**授权：
(sid, host, 到期 epoch)。EgressProxy 判定 = 全局白名单 OR 本会话未过期授权。
重启即清空——授权随审批重新发起，不让重启「继承」放行面（宁可多问一次）。

会话归零（archived/删除）不主动清：授权 2h 自然到期，且 socket 消失后
沙箱回落共享白名单通道，授权无判定边界自然失效。
"""
from __future__ import annotations

import re
import time
from datetime import datetime

from .audit import audit
from .util import get_logger

log = get_logger(__name__)

DEFAULT_TTL_S = 2 * 3600               # 默认 2h
MIN_TTL_S, MAX_TTL_S = 300, 24 * 3600  # 钳制域：5 分钟 - 24 小时

# sid -> {host: expires_epoch}
_GRANTS: dict[str, dict[str, float]] = {}

_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def valid_host(host: str) -> str | None:
    """域名归一化+校验（小写、去尾点；须含点、无 scheme/port/path）。

    非法返回 None——放行面是安全敏感输入，宁严勿宽。
    """
    h = (host or "").strip().lower().rstrip(".")
    if "." not in h or not _HOST_RE.match(h):
        return None
    return h


def grant(sid: str, host: str, ttl_s: int = DEFAULT_TTL_S, *,
          approval_id: int | None = None) -> dict:
    """落授权（ttl 钳制到 [300, 86400]）。审计留痕，返回含到期时间。"""
    ttl = max(MIN_TTL_S, min(MAX_TTL_S, int(ttl_s or DEFAULT_TTL_S)))
    exp = time.time() + ttl
    _GRANTS.setdefault(sid, {})[host] = exp
    out = {"sid": sid, "host": host, "ttl_s": ttl,
           "expires_at": datetime.fromtimestamp(exp).isoformat(timespec="seconds")}
    audit("egress_policy", {"action": "grant", **out, "approval_id": approval_id},
          sid=sid)
    log.info("egress 临时授权：%s → %s（%ds，审批 #%s）", sid, host, ttl,
             approval_id)
    return out


def revoke(sid: str, host: str) -> bool:
    hosts = _GRANTS.get(sid)
    if not hosts or host not in hosts:
        return False
    del hosts[host]
    audit("egress_policy", {"action": "revoke", "sid": sid, "host": host}, sid=sid)
    return True


def allowed(sid: str, host: str) -> bool:
    """代理热路径：本会话是否有该域的未过期授权（后缀语义与白名单一致）。"""
    hosts = _GRANTS.get(sid)
    if not hosts:
        return False
    h = (host or "").lower().rstrip(".")
    now = time.time()
    for g, exp in hosts.items():
        if exp <= now:
            continue                       # 惰性清理在 list_active 做，这里只判
        if h == g or h.endswith("." + g):
            return True
    return False


def list_active(sid: str | None = None) -> list[dict]:
    """活跃授权清单（管理面展示；顺手清过期项）。sid 给定时只出该会话——
    过期回收仍全量走（惰性 GC 只有这一个入口，会话视图不能替它省略）。"""
    now = time.time()
    out: list[dict] = []
    for osid, hosts in list(_GRANTS.items()):
        for host, exp in list(hosts.items()):
            if exp <= now:
                del hosts[host]
                continue
            if sid and osid != sid:
                continue
            out.append({"sid": osid, "host": host,
                        "expires_at": datetime.fromtimestamp(exp)
                        .isoformat(timespec="seconds")})
    return sorted(out, key=lambda g: g["expires_at"])
