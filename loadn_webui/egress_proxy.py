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

GW_HOST = "llm-gw.internal"          # 虚拟域：代理内 LLM 网关（凭证注入点）
GW_PATHS_PREFIX = ("/v1/messages", "/api/anthropic")   # 透传路径族

# 运行实例（lifespan start() 注册；spawn 侧 ensure_session_uds 的入口）
_INSTANCE: EgressProxy | None = None


def get_proxy() -> EgressProxy | None:
    return _INSTANCE


def _upstream_creds() -> tuple[str, str]:
    """真上游（base_url, token）——控制域读取全局 settings env 段（缓存）。"""
    global _UPSTREAM
    if _UPSTREAM is None:
        import json as _json
        from pathlib import Path as _P
        try:
            env = (_json.loads((_P.home() / ".claude" / "settings.json")
                               .read_text()).get("env") or {})
            _UPSTREAM = (env.get("ANTHROPIC_BASE_URL", "").rstrip("/"),
                         env.get("ANTHROPIC_AUTH_TOKEN", ""))
        except (OSError, ValueError):
            _UPSTREAM = ("", "")
    return _UPSTREAM


_UPSTREAM = None


def _allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == a or host.endswith("." + a)
               for a in CONFIG.security.egress_allow)


_POLICY_MTIME: int | None = None


def _maybe_reload_policy() -> None:
    """白名单/模式热重载：config.yaml 外部手工编辑免重启生效。

    管理面写路径（settings_admin）已原地更新 CONFIG，这里只兜「直接改 yaml」。
    每请求一次 stat（便宜）；解析失败保持旧值。刻意只热更 egress 两键——
    sandbox 等其余 security 键影响面在 spawn 期，仍按重启语义走。
    """
    global _POLICY_MTIME
    import os as _os

    from .config import PATHS as _P
    p = _P["root"] / "config.yaml"
    try:
        mtime = _os.stat(p).st_mtime_ns
    except OSError:
        return
    if mtime == _POLICY_MTIME:
        return
    _POLICY_MTIME = mtime
    try:
        import yaml as _yaml
        data = _yaml.safe_load(p.read_text()) or {}
        sec = data.get("security") if isinstance(data, dict) else None
        if not isinstance(sec, dict):
            return
        allow, mode = sec.get("egress_allow"), sec.get("egress_mode")
        on_deny, wait_s = sec.get("egress_on_deny"), sec.get("egress_ask_wait_s")
        if isinstance(allow, list):
            CONFIG.security.egress_allow = [str(a) for a in allow]
        if isinstance(mode, bool):              # yaml 1.1：裸 off → False
            mode = "off" if mode is False else None
        if mode in ("off", "warn", "enforce"):
            CONFIG.security.egress_mode = mode
        # 新两键同款热更（非法值保持旧值——热更通道不 fail-closed 拒启，
        # 只有无视；启动通道的枚举报错在 config.load_config）
        if on_deny in ("deny", "ask"):
            CONFIG.security.egress_on_deny = on_deny
        if isinstance(wait_s, int) and 15 <= wait_s <= 600:
            CONFIG.security.egress_ask_wait_s = wait_s
        log.info("egress 策略热重载：allow=%d 域 mode=%s on_deny=%s",
                 len(CONFIG.security.egress_allow), CONFIG.security.egress_mode,
                 CONFIG.security.egress_on_deny)
    except (OSError, ValueError):
        log.warning("config.yaml 解析失败，egress 策略保持旧值")


def _session_mode(sid: str | None) -> str:
    """本连接生效的 egress 档位：会话 params.egress 覆盖 > 全局 config。

    无 sid（共享 TCP 通道的旧形态）或读库异常 → 全局（fail-closed 到
    更严的一侧不可能，fail-open 到全局档是既定语义：覆盖只用于放开/收紧
    本任务，全局才是基线）。
    """
    if not sid:
        return CONFIG.security.egress_mode
    try:
        from . import db as _db
        from . import params as _params
        with _db.conn() as c:
            sess = _db.get_session(c, sid)
        raw = sess["params_json"] if sess else None
        return _params.session_egress_override(raw) or CONFIG.security.egress_mode
    except Exception:                                  # noqa: BLE001
        return CONFIG.security.egress_mode


def _deny_body(host: str, reason: str) -> bytes:
    """403 响应体（agent 可读）：拦了要告诉它为什么、下一步能做什么。"""
    import json as _json
    hints = {
        "not-in-allowlist": "该域不在出口白名单。用户可在「安全中心/属性面板·安全与外联」放行；"
                            "任务内可运行 loadn-web r egress " + host + " --note 理由 申请临时授权",
        "ask-timeout": "外联审批等待超时未裁决。用户批准/放行后重试本请求即可成功",
        "denied-by-user": "用户已拒绝本次外联申请",
        "host-mismatch": "URL 域名与 Host 头不一致（域名分片防护），拒绝转发",
    }
    payload = _json.dumps(
        {"error": "egress_denied", "host": host, "reason": reason,
         "hint": hints.get(reason, "")}, ensure_ascii=False).encode()
    head = (f"HTTP/1.1 403 Forbidden by egress policy\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n")
    return head.encode("latin1") + payload


def _record(host: str, decision: str, mode: str, port: int = 0,
            sid: str | None = None) -> None:
    audit("egress_request",
          {"host": host, "decision": decision, "mode": mode, "port": port},
          sid=sid or None)
    # W5.2 数据流向事件（面板轮询源：audit tail；SSE 推送后续）。
    # payload 带 sid：会话属性面板凭它只刷新本会话的外联流水
    try:
        from .engine import ENGINE
        payload = {"host": host, "decision": decision}
        if sid:
            payload["sid"] = sid
        ENGINE.publish("*", "egress", payload)
    except Exception:                                  # noqa: BLE001
        pass


class EgressProxy:
    """极简正向代理：CONNECT 直通（白名单后）+ 明文 HTTP 转发。

    双监听：TCP（claude/opencode 引擎，share-net 场景）+ Unix socket
    （loadn 引擎 unshare-net 场景——socket 可 bind-mount 进沙箱）。
    会话级 socket（ensure_session_uds）：loadn 沙箱 bind 各自的
    egress-<sid12>.sock，代理凭 accept 来源区分会话——审批式临时授权
    （egress_grants）按任务生效的判定边界。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8793,
                 uds_path: str = ""):
        self.host, self.port, self.uds_path = host, port, uds_path
        self._server: asyncio.AbstractServer | None = None
        self._uds_server: asyncio.AbstractServer | None = None
        self._session_servers: dict[str, asyncio.AbstractServer] = {}
        self._session_ports: dict[str, int] = {}

    def _session_path(self, sid: str):
        """会话级 socket 路径（短哈希防 unix 路径 108 字节上限）。"""
        import hashlib
        from pathlib import Path as _P
        tag = hashlib.sha1(sid.encode()).hexdigest()[:12]
        return _P(self.uds_path).parent / f"egress-{tag}.sock"

    async def ensure_session_uds(self, sid: str) -> str | None:
        """幂等创建会话级监听（loadn 引擎 spawn 前调用）。返回 socket 路径。"""
        if not self.uds_path:
            return None
        if sid in self._session_servers:
            return str(self._session_path(sid))
        p = self._session_path(sid)
        try:
            import os as _os
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                _os.unlink(p)
            except OSError:
                pass
            srv = await asyncio.start_unix_server(
                lambda r, w: self._handle(r, w, sid), path=str(p), limit=1 << 20)
            _os.chmod(p, 0o660)
            self._session_servers[sid] = srv
            return str(p)
        except OSError:
            log.exception("会话级 egress socket 创建失败（%s）", sid)
            return None

    async def ensure_session_tcp(self, sid: str) -> int | None:
        """per-session 回环 TCP 监听（直跑引擎的 sid 归属通道）。

        bwrap 形态走 UDS（unshare-net 内 socat 桥）；direct 形态（档位 off
        /降级）引擎 env https_proxy 从共享端口改指这里——全部引擎都有会话
        归属，ask 弹卡与任务级临时授权不再只覆盖 loadn 引擎。
        幂等；失败返回 None（调用方回落共享端口=只有全局白名单+无弹卡归属）。
        """
        if sid in self._session_ports:
            return self._session_ports[sid]
        try:
            srv = await asyncio.start_server(
                lambda r, w: self._handle(r, w, sid),
                "127.0.0.1", 0, limit=1 << 20)
            port = srv.sockets[0].getsockname()[1]
            self._session_ports[sid] = port
            self._session_servers[f"tcp:{sid}"] = srv    # stop() 统一收口
            return port
        except OSError:
            log.exception("会话级 egress TCP 监听失败（%s）", sid)
            return None

    async def start(self) -> int:
        global _INSTANCE
        _INSTANCE = self
        self._server = await asyncio.start_server(
            self._handle, self.host, self.port, limit=1 << 20)
        # port=0（随机）时取实际绑定端口
        self.port = self._server.sockets[0].getsockname()[1]
        if self.uds_path:
            try:
                import os as _os
                pathlib_path = __import__("pathlib").Path(self.uds_path)
                pathlib_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    _os.unlink(self.uds_path)
                except OSError:
                    pass
                self._uds_server = await asyncio.start_unix_server(
                    self._handle, path=self.uds_path, limit=1 << 20)
                _os.chmod(self.uds_path, 0o660)
            except OSError:
                log.exception("egress unix socket 监听失败（P3 断网形态不可用）")
        log.info("egress 代理监听 %s:%s%s（mode=%s，白名单 %d 域）",
                 self.host, self.port,
                 f"+uds:{self.uds_path}" if self._uds_server else "",
                 CONFIG.security.egress_mode, len(CONFIG.security.egress_allow))
        return self.port

    async def stop(self) -> None:
        for srv in (self._server, self._uds_server,
                    *self._session_servers.values()):
            if srv:
                srv.close()
                await srv.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter,
                      sid: str | None = None) -> None:
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
                await self._connect(reader, writer, target, sid)
            else:
                await self._plain_http(reader, writer, method, target,
                                        headers_raw, sid)
        except (asyncio.TimeoutError, OSError, ValueError):
            pass
        finally:
            try:
                writer.close()
            except OSError:
                pass

    async def _gate(self, host: str, port: int, sid: str | None = None
                    ) -> tuple[bool, str]:
        """判定（每连接一次）。返回 (是否放行, reason)——reason 同步落审计
        与 SSE，denied 时进 403 体给 agent 可读的解释。"""
        _maybe_reload_policy()
        from . import egress_grants
        mode = _session_mode(sid)
        if mode == "off":
            _record(host, "allow-open", mode, port, sid)
            return True, "allow-open"
        if _allowed(host):
            _record(host, "allow", mode, port, sid)
            return True, "allow"
        if sid and egress_grants.allowed(sid, host):
            _record(host, "allow-grant", mode, port, sid)
            return True, "allow-grant"
        if mode == "warn":
            _record(host, "allow-warn", mode, port, sid)
            return True, "allow-warn"
        # enforce：未列域。deny=旧形态直接拒；ask=弹卡确认（默认）——
        # 拦了不给用户选择的机会，比多问一次更糟
        if CONFIG.security.egress_on_deny == "ask" and sid:
            ok, why = await self._ask_and_wait(sid, host, port, mode)
            return ok, why
        _record(host, "deny", mode, port, sid)
        return False, "not-in-allowlist"

    async def _ask_and_wait(self, sid: str, host: str, port: int,
                            mode: str) -> tuple[bool, str]:
        """弹卡确认：建 egress 审批（同域去重）→ SSE 推会话 → 挂起轮询裁决。

        挂起占用的是本连接（引擎侧表现为请求慢），批准即已落临时授权
        （approve.decide 的 egress 分支），本请求直接放行——agent 无需重试。
        超时/拒绝 → 403（fail-closed）。
        """
        from . import approve as approve_mod
        aid = approve_mod.pending_egress_id(sid, host)
        if aid is None:
            try:
                out = approve_mod.create(
                    sid, "egress", {"host": host, "ttl_s": 7200},
                    note="平台自动发起：任务外联被白名单拦截，等待用户裁决")
                aid = out["id"]
                from .engine import ENGINE
                ENGINE.publish(sid, "approval", {"kind": "request", **out})
            except (ValueError, OSError):
                log.exception("egress 弹卡创建失败，回退直接拒（%s→%s）", sid, host)
                _record(host, "deny", mode, port, sid)
                return False, "not-in-allowlist"
        # 等待上限信任 CONFIG（启动 load_config 与管理面 PUT 均已 15-600 校验）
        wait = int(CONFIG.security.egress_ask_wait_s)
        deadline = asyncio.get_event_loop().time() + wait
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            st = approve_mod.status(aid)
            if st["status"] == "executed":       # decide 批准即落授权
                _record(host, "allow-ask", mode, port, sid)
                return True, "allow-ask"
            if st["status"] in ("denied", "expired"):
                _record(host, "denied-by-user", mode, port, sid)
                return False, "denied-by-user"
        _record(host, "ask-timeout", mode, port, sid)
        return False, "ask-timeout"

    async def _connect(self, reader, writer, target: str,
                       sid: str | None = None) -> None:
        host, _, port_s = target.partition(":")
        port = int(port_s or "443")
        ok, reason = await self._gate(host, port, sid)
        if not ok:
            writer.write(_deny_body(host, reason))
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
        await self._pipe(reader, upstream[1], upstream[0], writer)

    async def _plain_http(self, reader, writer, method: str, url: str,
                          headers: dict, sid: str | None = None) -> None:
        """明文绝对 URL 形态：Host 头判定（与 URL host 不一致=分片攻击，拒）。

        P5 LLM 网关：目标=GW_HOST → 代理内注入凭证转发真上游（token 零入沙箱）。
        """
        from urllib.parse import urlsplit
        u = urlsplit(url if "//" in url else f"http://{url}")
        host_hdr = headers.get("host", "")
        host = u.hostname or host_hdr
        if host == GW_HOST:
            await self._llm_gateway(reader, writer, method, u, headers)
            return
        if host_hdr and u.hostname and host_hdr.lower() != u.hostname.lower():
            _record(host, "deny-mismatch", _session_mode(sid), sid=sid)
            if _session_mode(sid) == "enforce":
                writer.write(_deny_body(host, "host-mismatch"))
                await writer.drain()
                return
        port = u.port or 80
        ok, reason = await self._gate(host, port, sid)
        if not ok:
            writer.write(_deny_body(host, reason))
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
        await self._pipe(reader, up_w, up_r, writer)

    async def _llm_gateway(self, reader, writer, method, u, headers) -> None:
        """虚拟域网关：读完整请求 → 注入 Authorization → httpx 连真上游。"""
        import httpx as _hx
        base, token = _upstream_creds()
        if not base:
            writer.write(b"HTTP/1.1 503 no upstream credentials\r\n"
                         b"Content-Length: 0\r\n\r\n")
            await writer.drain()
            return
        try:
            clen = int(headers.get("content-length", "0") or 0)
            # readexactly 而非 read：read(n) 只保证「最多 n」——大请求体跨
            # TCP 分段时首读只拿到第一段，body 截断转上游=JSON decode error
            # （实测 body.9860 = 首段长度；对话越大越必炸）
            body = await asyncio.wait_for(reader.readexactly(clen),
                                          timeout=60) if clen else b""
            fwd = {k: v for k, v in headers.items()
                   if k not in ("host", "authorization", "content-length",
                                "transfer-encoding", "proxy-connection",
                                "connection")}
            fwd["authorization"] = f"Bearer {token}"
            fwd["x-api-key"] = token
            _record(GW_HOST, "allow-gateway", CONFIG.security.egress_mode)
            up_url = base + (u.path or "/v1/messages") + \
                (f"?{u.query}" if u.query else "")
            async with _hx.AsyncClient(timeout=600) as client:
                up_resp = await client.request(method, up_url,
                                               content=body or None,
                                               headers=fwd)
            resp_body = up_resp.content
            status = up_resp.status_code
            rh = "".join(
                f"{k}: {v}\r\n" for k, v in up_resp.headers.items()
                if k.lower() in ("content-type", "request-id",
                                 "anthropic-ratelimit-requests-remaining"))
            from http import HTTPStatus
            try:
                reason = HTTPStatus(status).phrase
            except ValueError:
                reason = "OK"
            head = (f"HTTP/1.1 {status} {reason}\r\n{rh}"
                    f"Content-Length: {len(resp_body)}\r\n"
                    f"Connection: close\r\n\r\n")
            writer.write(head.encode("latin1") + resp_body)
            await writer.drain()
        except (OSError, ValueError, _hx.HTTPError, asyncio.TimeoutError,
                asyncio.IncompleteReadError) as e:
            log.exception("LLM 网关转发失败")
            msg = f"gateway error: {type(e).__name__}".encode()
            head = (b"HTTP/1.1 502 Bad Gateway\r\n"
                    + f"Content-Length: {len(msg)}\r\n\r\n".encode())
            writer.write(head + msg)
            await writer.drain()

    async def _pipe(self, r1, w1, r2, w2) -> None:
        """双向裸转发。关闭时机：两方向都 EOF 后统一收尾——单方向 EOF 提前
        close 对端会截断 TLS 长流（实测 curl TLS decode_error 教训）。"""
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
        await asyncio.gather(_p(r1, w1), _p(r2, w2))
        for w in (w1, w2):
            try:
                w.close()
            except OSError:
                pass
