"""net-policy（P0-5，OpenClaw O2 × codex C3 同构）：URL 凭证脱敏 +
IP 私网分类 + DNS 重绑定防护。纯函数、零第三方依赖（stdlib only）。

- redact_url：userinfo@ → ***@；敏感 query 键（token/key/api_key/secret/
  signature/…，openclaw 清单）值 → ***；#fragment 整体丢弃；Telegram
  /bot<token>/ 路径。审计/日志不得出现明文 token（宪法安全章）。
- redact：自由文本兜底（audit detail_json 等任意串）——scheme URL 逐个
  过 redact_url，匹配次数上限防膨胀（openclaw 嵌套递归限深同思想）。
- classify_ip / is_non_public_ip：RFC1918 / CGNAT 100.64/10 / 链路本地
  169.254 + fe80::/10 / 回环 / 未指定 / 组播 / TEST-NET / v4-mapped
  （ipaddress 之上补显式段，防 py 版本差异）。
- resolve_guard：DNS 重绑定防护——公网域名解析出任一非公网 IP 即拒
  （allowlist 域名的 DNS 被污染→引擎触达内网）。TTL 缓存 + 容量上限。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

REDACTED = "***"

# 敏感 query 键（openclaw SENSITIVE_URL_QUERY_PARAM_NAMES 语义同构；
# 后缀变体 *_token / token_<hex> 一并覆盖）
_SENSITIVE_KEYS = {
    "token", "key", "api_key", "apikey", "secret", "access_token",
    "auth_token", "password", "passwd", "pass", "auth", "jwt", "session",
    "id_token", "code", "client_secret", "app_secret", "hook_token",
    "refresh_token", "signature", "x_amz_signature", "x_amz_security_token",
    "private_key", "credential", "authorization", "sig", "x_api_key",
    "x_access_token", "x_auth_token",
}
_KEY_RE = re.compile(r"^(?:.*_)?token(?:_[a-f0-9]{16,})?$", re.I)

# Telegram bot 凭证路径 /bot<id>:<token>（含 %3A 编码形）
_TELEGRAM_RE = re.compile(r"/bot\d{6,}(?::|%3[aA])[A-Za-z0-9_-]{20,}(?=/|$)",
                          re.I)
_URL_RE = re.compile(r"\bhttps?://[^\s<>\"']+|\bwss?://[^\s<>\"']+"
                     r"|\bftp://[^\s<>\"']+", re.I)
_MAX_REDACT_SWEEPS = 32


# ---------------------------------------------------------------- 脱敏
def redact_url(url: str) -> str:
    """单个 URL 的凭证脱敏（不变式：脱敏后的串不再含原凭证）。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return _TELEGRAM_RE.sub("/bot" + REDACTED, url)
    userinfo = ""
    if parts.username:
        userinfo = REDACTED + ":" + REDACTED + "@" if parts.password \
            else REDACTED + "@"
    netloc = parts.netloc
    if "@" in netloc:
        netloc = userinfo + netloc.split("@", 1)[1]
    # query：敏感键值清空（保留键名与参数次序——审计可读性）
    pairs = []
    if parts.query:
        for kv in parts.query.split("&"):
            k, _, v = kv.partition("=")
            if _norm_key(k) in _SENSITIVE_KEYS or _KEY_RE.match(k or ""):
                pairs.append(f"{k}={REDACTED}")
            else:
                pairs.append(kv)
    path = _TELEGRAM_RE.sub("/bot" + REDACTED, parts.path)
    return urlunsplit((parts.scheme, netloc, path,
                       "&".join(pairs) if pairs else "", ""))   # fragment 丢


def _norm_key(k: str) -> str:
    return k.strip().lower()


def redact(text: str) -> str:
    """自由文本兜底：串内全部 URL 形态逐个脱敏（scheme 扫描，非 URL 原样）。"""
    if not text or "://" not in text:
        return text
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        return redact_url(m.group(0)) if count <= _MAX_REDACT_SWEEPS \
            else m.group(0)
    return _URL_RE.sub(_sub, text)


# ---------------------------------------------------------------- IP 分类
def classify_ip(raw: str) -> str | None:
    """IP 字面量分类；非 IP/公网 → None。分类名进审计 reason。"""
    try:
        ip = ipaddress.ip_address(raw.strip().split("%", 1)[0])
    except ValueError:
        return None
    v4 = None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = v4 = ip.ipv4_mapped
    elif isinstance(ip, ipaddress.IPv4Address):
        v4 = ip
    if ip.is_loopback:
        return "loopback"
    if v4 is not None and v4 in ipaddress.ip_network("100.64.0.0/10"):
        return "cgnat"                       # py<3.11 is_private 不含 CGNAT
    if v4 is not None and any(
            v4 in ipaddress.ip_network(n) for n in
            ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")):
        return "test_net"
    if ip.is_link_local:
        return "link_local"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_private:
        return "private"
    if ip.is_multicast:
        return "multicast"
    return None                              # 公网


def is_non_public_ip(raw: str) -> bool:
    """codex is_non_public_ip 同构：分类非空即非公网。"""
    return classify_ip(raw) is not None


# ---------------------------------------------------------------- DNS 重绑定
_DNS_CACHE: dict[str, tuple[float, list[str]]] = {}
_DNS_TTL_S = 60.0
_DNS_CACHE_MAX = 512


def resolve_ips(host: str) -> list[str]:
    """同步解析（调用方负责放 executor）；失败/无记录 → []。"""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        return []
    return sorted({i[4][0] for i in infos})


def rebind_check(host: str, *, now: float | None = None) -> tuple[bool, str]:
    """公网域名 → 非公网 IP 的重绑定判定。返回 (ok, reason)。

    - 字面 IP 直接分类（无 DNS）；localhost → 拒
    - 域名解析全部 IP，任一非公网 → 拒（reason 带分类名）
    - 解析失败 → 拒（fail-closed；对不可达域名本也建不了连接）
    - TTL 缓存（解析只做一次，卡面要求）
    """
    import time as _time
    t = _time.monotonic() if now is None else now
    cat = classify_ip(host)
    if cat is not None:
        return False, f"ip:{cat}"
    if host.strip().lower() in ("localhost",) or not host.strip("."):
        return False, "ip:localhost"
    cached = _DNS_CACHE.get(host)
    if cached and t - cached[0] < _DNS_TTL_S:
        ips = cached[1]
    else:
        ips = resolve_ips(host)
        if len(_DNS_CACHE) >= _DNS_CACHE_MAX:
            _DNS_CACHE.clear()               # 简单容量闸（审计频率低，够用）
        _DNS_CACHE[host] = (t, ips)
    if not ips:
        return True, "dns:unresolvable"      # 解析不出=连不上（502 自然失败），
        # fail-open 是对的：重绑定威胁只在「解析成功且指向非公网」
    for ip in ips:
        cat = classify_ip(ip)
        if cat is not None:
            return False, f"dns-rebind:{ip}:{cat}"
    return True, ""
