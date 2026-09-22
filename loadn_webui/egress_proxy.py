"""出口白名单代理（W5.1）：引擎流量的唯一审计/管控点。

架构（M2 落地形态）：
- 正向代理监听 127.0.0.1:<port>（引擎 spawn env 注入 https_proxy/HTTPS_PROXY
  ——httpx 标准库原生尊重；bwrap 物理隔离 unshare-net+socket 挂载为
  下一步，先 env 通道接管=零风险可回退）。
- 判定：CONNECT（https）按目标主机（SNI 可见面）；明文请求按 Host 头
  （防域名分片——URL host 与 Host 头不一致即拒）。
- mode: warn（放行+审计）/ enforce（非 egress.allow → 403）。
- 全量 audit(egress_request) + SSE 数据流向事件（面板数据源）。

诚实边界（v1.1 §6.6 调整）：https 隧道是直通——凭证注入仅在明文 HTTP 段；
LLM 凭证（M1 known-gap）仍在引擎侧 env，MITM 注入随 M2 完整化评估。
"""
from __future__ import annotations

import asyncio

from .audit import audit
from .config import CONFIG
from .util import get_logger

log = get_logger(__name__)


def _allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == a or host.endswith("." + a)
               for a in CONFIG.security.egress_allow)


def _record(host: str, decision: str, mode: str, port: int = 0) -> None:
    audit("egress_request",
          {"host": host, "decision": decision, "mode": mode, "port": port})
    # W5.2 数据流向事件（面板轮询源：audit tail；SSE 推送后续）
    try:
        from .engine import ENGINE
        ENGINE.publish("*", "egress", {"host": host, "decision": decision})
    except Exception:                                  # noqa: BLE001
        pass


class EgressProxy:
    """极简正向代理：CONNECT 直通（白名单后）+ 明文 HTTP 转发。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8793):
        self.host, self.port = host, port
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._handle, self.host, self.port, limit=1 << 20)
        # port=0（随机）时取实际绑定端口
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("egress 代理监听 %s:%s（mode=%s，白名单 %d 域）",
                 self.host, self.port, CONFIG.security.egress_mode,
                 len(CONFIG.security.egress_allow))
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            parts = line.decode("latin1").split()
            if len(parts) < 2:
                return
            method, target = parts[0], parts[1]
            # 吃掉头（保留原样转发给明文段用不到；CONNECT 只需目标）
            headers_raw = {}
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=10)
                if h in (b"\r\n", b"\n", b""):
                    break
                k, _, v = h.decode("latin1").partition(":")
                headers_raw[k.strip().lower()] = v.strip()
            if method == "CONNECT":
                await self._connect(reader, writer, target)
            else:
                await self._plain_http(reader, writer, method, target,
                                        headers_raw)
        except (asyncio.TimeoutError, OSError, ValueError):
            pass
        finally:
            try:
                writer.close()
            except OSError:
                pass

    def _gate(self, host: str, port: int) -> bool:
        mode = CONFIG.security.egress_mode
        if _allowed(host):
            _record(host, "allow", mode, port)
            return True
        _record(host, "deny", mode, port)
        return mode != "enforce"          # warn：拒绝只记录，放行直通

    async def _connect(self, reader, writer, target: str) -> None:
        host, _, port_s = target.partition(":")
        port = int(port_s or "443")
        if not self._gate(host, port):
            writer.write(b"HTTP/1.1 403 Forbidden by egress policy\r\n\r\n")
            await writer.drain()
            return
        try:
            upstream = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=15)
        except (OSError, asyncio.TimeoutError):
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await self._pipe(reader, upstream[1], writer, upstream[0])

    async def _plain_http(self, reader, writer, method: str, url: str,
                          headers: dict) -> None:
        """明文绝对 URL 形态：Host 头判定（与 URL host 不一致=分片攻击，拒）。"""
        from urllib.parse import urlsplit
        u = urlsplit(url if "//" in url else f"http://{url}")
        host_hdr = headers.get("host", "")
        host = u.hostname or host_hdr
        if host_hdr and u.hostname and host_hdr.lower() != u.hostname.lower():
            _record(host, "deny-mismatch", CONFIG.security.egress_mode)
            if CONFIG.security.egress_mode == "enforce":
                writer.write(b"HTTP/1.1 403 Host mismatch\r\n\r\n")
                await writer.drain()
                return
        port = u.port or 80
        if not self._gate(host, port):
            writer.write(b"HTTP/1.1 403 Forbidden by egress policy\r\n\r\n")
            await writer.drain()
            return
        try:
            up_r, up_w = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=15)
        except (OSError, asyncio.TimeoutError):
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return
        # 重组请求行（相对化）+ 头
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        req = f"{method} {path} HTTP/1.1\r\n"
        for k, v in headers.items():
            if k != "proxy-connection":
                req += f"{k}: {v}\r\n" if k != "host" else f"Host: {v}\r\n"
        req += "Connection: close\r\n\r\n"
        up_w.write(req.encode("latin1"))
        await up_w.drain()
        await self._pipe(reader, up_w, writer, up_r)

    async def _pipe(self, r1, w1, r2, w2) -> None:
        async def _p(a, wb):
            try:
                while True:
                    chunk = await a.read(65536)
                    if not chunk:
                        break
                    wb.write(chunk)
                    await wb.drain()
            except OSError:
                pass
            finally:
                try:
                    wb.close()
                except OSError:
                    pass
        await asyncio.gather(_p(r1, w1), _p(r2, w2))
