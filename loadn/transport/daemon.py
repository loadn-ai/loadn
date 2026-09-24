"""daemon：UDS 多连接引擎宿主（P2-1，codex app-server × ZCode server 同构）。

生命周期：
- `loadn daemon` 前台启动：监听 $LOADN_HOME/var/engine.sock；连接 token
  生成写同目录 engine.token（0600）。attach 首行 {"auth": "<token>"} 校验
  （connection_auth 语义——无 token/错 token 立即断）。
- 连接协议（NDJSON，方言与 stdio 一致——桥零改动的根基）：
  - 连接首行：{"auth": token, "session_id": "..."}（attach 目标会话）
  - 其后每行 = 一个「turn 请求」：{"type": "run", "prompt": "..."}；
    引擎按序执行，事件流写回该连接（同时镜像广播给同会话只读连接）
  - {"type": "detach"}：主动断开（引擎收尾当前 turn 后挂起）
- 会话挂起：连接全断后 AgentCore 存活（cache warmer/P2-2 游标受益），
  重新 attach 继续；daemon 退出=全部会话终局。
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

from loadn import loadn_home
from loadn.util import get_logger

log = get_logger(__name__)


def sock_path() -> Path:
    return loadn_home() / "var" / "engine.sock"


def token_path() -> Path:
    return loadn_home() / "var" / "engine.token"


class EngineDaemon:
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.cores: dict[str, object] = {}          # sid → AgentCore（挂起存活）
        self.peers: dict[str, list[asyncio.StreamWriter]] = {}  # sid → 连接
        self.token = ""

    # ------------------------------------------------------------ 生命周期
    async def serve(self) -> None:
        p = sock_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.unlink()
        except OSError:
            pass
        self.token = secrets.token_urlsafe(24)
        token_path().write_text(self.token)
        os.chmod(token_path(), 0o600)
        server = await asyncio.start_unix_server(self._handle, str(p))
        os.chmod(p, 0o600)
        log.info("loadn daemon 监听 %s（token 0600）", p)
        async with server:
            await server.serve_forever()

    # ------------------------------------------------------------ 连接
    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        # 首行鉴权（connection_auth）
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=15)
            hello = json.loads(line)
        except (asyncio.TimeoutError, OSError, ValueError):
            writer.close()
            return
        if hello.get("auth") != self.token:
            writer.write(b'{"type":"error","error":"auth failed"}\n')
            await writer.drain()
            writer.close()
            return
        sid = str(hello.get("session_id") or "")
        if not sid:
            writer.write(b'{"type":"error","error":"session_id required"}\n')
            await writer.drain()
            writer.close()
            return
        self.peers.setdefault(sid, []).append(writer)
        try:
            await self._pump(sid, reader, writer)
        finally:
            self.peers[sid] = [w for w in self.peers.get(sid, []) if w is not writer]
            try:
                writer.close()
            except OSError:
                pass
            log.info("daemon 连接断开 sid=%s（引擎挂起保留）", sid)

    async def _pump(self, sid: str, reader: asyncio.StreamReader,
                    writer: asyncio.StreamWriter) -> None:
        """连接消息循环：run 请求 → run_turn；事件流回写+只读镜像。"""
        while True:
            try:
                raw = await reader.readline()
            except OSError:
                return
            if not raw:
                return                      # UI 断开：挂起等待重连
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "detach":
                return
            if msg.get("type") != "run":
                continue
            core = await self._core_for(sid)
            if core is None:
                writer.write(b'{"type":"error","error":"engine init failed"}\n')
                await writer.drain()
                continue

            async def emit(ev: dict) -> None:
                line = (json.dumps(ev, ensure_ascii=False) + "\n").encode()
                for w in list(self.peers.get(sid, [])):
                    try:
                        w.write(line)
                        await w.drain()
                    except OSError:
                        pass

            # StreamJsonEmitter 语义复用（事件→NDJSON 定向回写连接而非
            # stdout；方言与 stdio 面完全一致——桥零改动的根基）。
            # T4 修（真 bug）：此前经 create_task 异步排队回写——客户端
            # run 后快速 detach 时 _pump 直接 return+writer.close()，
            # 未跑的回写任务被整体丢弃（事件吞掉）。对齐 stdio 面的
            # print+flush 同步语义：write 即刻落 transport 缓冲（UDS 本地
            # 小消息无背压面；drain 语义由后续 readline await 承担）
            from loadn.cli.stream_json import StreamJsonEmitter

            def _net_emit(ev: dict) -> None:
                line = (json.dumps(ev, ensure_ascii=False) + "\n").encode()
                for w in list(self.peers.get(sid, [])):
                    try:
                        w.write(line)
                    except OSError:
                        pass

            class _NetEmitter(StreamJsonEmitter):
                _emit = staticmethod(_net_emit)

            emitter = _NetEmitter(
                model=getattr(core.provider, "model_name", "") or "",
                tools=sorted(core.tools))
            emitter.session_id = sid
            emitter.send_init(sid)
            try:
                await core.run_turn(str(msg.get("prompt") or ""), emit=emitter)
            except Exception as e:                          # noqa: BLE001
                writer.write(json.dumps(
                    {"type": "error", "error": repr(e)}).encode() + b"\n")
                await writer.drain()

    async def _core_for(self, sid: str):
        """会话 AgentCore 复用（挂起重连不重建——cache warmer/上下文全保留）。"""
        if sid in self.cores:
            return self.cores[sid]
        from loadn.core.build import build_agent
        from loadn.core.session import SessionManager
        from loadn.providers import provider_config
        try:
            session = SessionManager.resume(sid, self.cwd)
            bundle = await build_agent(self.cwd, cfg=provider_config())
            bundle.session = session
            bundle.core.session = session
            self.cores[sid] = bundle.core
            return bundle.core
        except Exception as e:                              # noqa: BLE001
            log.warning("daemon 会话 %s 构建失败：%s", sid, e)
            return None


def main() -> int:
    d = EngineDaemon(Path.cwd())
    try:
        asyncio.run(d.serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
