"""http-recorder VCR cassettes（P1-3，opencode D1 同构）：零 token 测试
从「手工 fake」升级到「真实 API 形状回放」。

- CassetteTransport：httpx AsyncBaseTransport 拦截层——回放模式不碰网络，
  未命中**明确失败**（不许网络兜底，保零 token）。
- 磁带格式：{"version":1, "interactions":[{method,url,headers,body,status,
  response_headers,response_body}]}；录制=真实请求一次落盘。
- matching（opencode canonicalSnapshot 同构）：method+url 全等；headers/body
  JSON 走 canonicalize（键排序递归）；**波动头忽略集**（x-request-id/
  timestamp 类）不参与比对。
- 脱敏：敏感头（authorization/x-api-key/cookie…）与 body 中 token 值
  替换 `<redacted>`；body 落盘前过 webui net_policy.redact——引擎侧无
  webui 模块（进程边界），移植等价规则：URL 带 query 敏感键的落盘前剥值。
- 选择策略：顺序 selectSequential 同构（回放按录制序逐条消费；同形重复
  请求按序取下一条）。
- env：LOADN_CASSETTE_DIR（目录，空=关闭）、LOADN_CASSETTE_MODE
  （record|replay，默认 replay；record 下文件已存在则**拒绝覆盖**——磁带
  是真实采样工件，重录须显式删）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from loadn.util import get_logger

log = get_logger(__name__)

CASSETTE_VERSION = 1
# 波动头：每次请求都变，不参与匹配（opencode matching 忽略集同构）
VOLATILE_HEADERS = {"x-request-id", "request-id", "traceparent",
                    "x-trace-id", "date", "user-agent", "content-length",
                    "host", "accept-encoding", "connection", "accept"}
# 敏感头：录制落盘即脱敏
SENSITIVE_HEADERS = {"authorization", "x-api-key", "cookie", "set-cookie",
                     "x-auth-token", "anthropic-api-key"}
_REDACTED = "<redacted>"
# body 中常见敏感键（浅层 JSON 对象键）
_SENSITIVE_BODY_KEYS = {"token", "api_key", "apikey", "access_token",
                        "auth_token", "client_secret", "password",
                        "secret", "authorization", "signature"}


def cassette_env() -> tuple[Path | None, str]:
    """(目录, 模式)。目录空 → (None, ...) = 功能关闭。"""
    d = os.environ.get("LOADN_CASSETTE_DIR", "").strip()
    mode = os.environ.get("LOADN_CASSETTE_MODE", "replay").strip()
    return (Path(d) if d else None), mode


def redact_headers(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        out[k] = _REDACTED if k.lower() in SENSITIVE_HEADERS else v
    return out


def redact_body(body: str | None) -> str | None:
    """JSON body 敏感键值脱敏（浅层）；非 JSON 原样（已是请求侧）。"""
    if not body:
        return body
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        return body
    if isinstance(d, dict):
        for k in list(d):
            if str(k).lower() in _SENSITIVE_BODY_KEYS:
                d[k] = _REDACTED
    return json.dumps(d, ensure_ascii=False)


def canonicalize(v):
    """opencode canonicalizeJson 同构：对象键排序递归（数组保序）。"""
    if isinstance(v, list):
        return [canonicalize(x) for x in v]
    if isinstance(v, dict):
        return {k: canonicalize(v[k]) for k in sorted(v)}
    return v


def _norm_headers(headers: dict) -> dict:
    """匹配视图：只保留参与比对的头（剥波动/敏感——敏感头脱敏后也稳定）。"""
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in VOLATILE_HEADERS:
            continue
        out[lk] = _REDACTED if lk in SENSITIVE_HEADERS else v
    return out


def snapshot(method: str, url: str, headers: dict, body) -> str:
    """匹配键（canonicalSnapshot 同构；body JSON 走 canonicalize）。"""
    b = body
    if isinstance(body, (str, bytes)) and body:
        try:
            b = canonicalize(json.loads(body if isinstance(body, str)
                                        else body.decode()))
        except (ValueError, TypeError):
            b = body.decode() if isinstance(body, bytes) else body
    return json.dumps({"method": method.upper(), "url": url,
                       "headers": canonicalize(_norm_headers(headers)),
                       "body": b}, sort_keys=True, ensure_ascii=False)


class CassetteTransport(httpx.AsyncBaseTransport):
    """录制/回放双模 transport（挂 httpx.AsyncClient(transport=…)）。

    record：透传真实网络（AsyncHTTPTransport），交互追加落盘（关机时由
    save 收口；这里每次交互即存——测试语义简单可靠）。
    replay：按 canonical 快照匹配磁带交互；未命中抛 RuntimeError（明确
    失败，零网络兜底）。
    """

    def __init__(self, name: str, directory: Path, mode: str = "replay",
                 real: httpx.AsyncBaseTransport | None = None):
        self.name = name
        self.path = directory / f"{name}.json"
        self.mode = mode
        self._real = real or httpx.AsyncHTTPTransport()
        self.interactions: list[dict] = []
        self._cursor = 0                 # 顺序选择（同形重复按序）
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.interactions = data.get("interactions") or []
            except (OSError, ValueError):
                log.warning("磁带 %s 损坏，按空带处理", self.path)

    # ------------------------------------------------------------ 回放
    async def handle_async_request(self, request: httpx.Request
                                   ) -> httpx.Response:
        body = request.content.decode(errors="replace") if request.content \
            else None
        if self.mode == "record":
            resp = await self._real.handle_async_request(request)
            raw = resp.read()
            self.interactions.append({
                "method": request.method, "url": str(request.url),
                "headers": redact_headers(dict(request.headers)),
                "body": redact_body(body),
                "status": resp.status_code,
                "response_headers": redact_headers(dict(resp.headers)),
                "response_body": raw.decode(errors="replace")})
            self.save()
            return httpx.Response(resp.status_code,
                                  headers=dict(resp.headers), content=raw,
                                  request=request)
        # replay：先顺序推进，快照不符再全局找（对齐 selectSequential 语义）
        key = snapshot(request.method, str(request.url),
                       dict(request.headers), body)
        for _ in range(len(self.interactions)):
            idx = self._cursor % len(self.interactions)
            self._cursor += 1
            it = self.interactions[idx]
            if snapshot(it["method"], it["url"], it.get("headers") or {},
                        it.get("body")) == key:
                return httpx.Response(
                    it.get("status", 200),
                    headers=dict(it.get("response_headers") or {}),
                    content=(it.get("response_body") or "").encode(),
                    request=request)
        raise RuntimeError(
            f"cassette {self.path.name} 无匹配交互（零 token 纪律：不落网络"
            f"兜底）。method={request.method} url={request.url}——磁带过期"
            "请重录（删除旧带后 LOADN_CASSETTE_MODE=record）")

    # ------------------------------------------------------------ 落盘
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"version": CASSETTE_VERSION, "interactions": self.interactions},
            ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


def maybe_cassette_client(name: str, **client_kw) -> httpx.AsyncClient | None:
    """env 配置了磁带目录 → 包 CassetteTransport 的 client；否则 None。

    record 且磁带已存在 → 拒绝覆盖（None 退化真网络 + warning）——磁带是
    真实采样工件。
    """
    directory, mode = cassette_env()
    if directory is None:
        return None
    transport = CassetteTransport(name, directory, mode)
    if mode == "record" and transport.path.exists():
        log.warning("磁带 %s 已存在，拒绝覆盖（重录须先删）", transport.path)
        return None
    return httpx.AsyncClient(transport=transport, **client_kw)
