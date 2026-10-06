"""P9 双向对话渠道：ChannelProvider 抽象 + Telegram 首实现。

- 长轮询 getUpdates（指数退避 1s→60s 上限重连；offset 持久推进）
- 安全：白名单 chat_id（fail-closed——非白名单忽略+审计 channel）；bot
  发来的消息直接丢弃（防 bot loop）；每 chat 限速 10/min 超限提示；
  外部消息只作为绑定会话的用户消息投递（不解析为系统操作——文本含
  命令词仅当整条是命令时才走命令分支）
- 命令：/new（新建会话+绑定）/bind <sid> /status /unbind
- 回信：轮询绑定会话的 turns 终态增量 → 最后一条 assistant 消息
  （Markdown，>4096 分段）
- P9b：审批请求推内联键盘（Approve/Deny）；回调 decide → 一次性码经
  steer 注回会话（与 webui 审批卡同一条 decide/consume 语义）
- token 只进 vault（键 telegram-bot）；WhatsApp/Signal 预留 CHANNEL_KINDS
  注册位不实现（卡边界）。

测试注入：`_client`（httpx.Client，可挂 MockTransport）与 `_sleep`。
"""
from __future__ import annotations

import time

import httpx

from ..config import CONFIG
from ..util import get_logger

log = get_logger(__name__)

VAULT_KEY = "telegram-bot"        # vault 平台键（token 唯一存放处）
TG_LIMIT = 4096                   # sendMessage 单条上限
RATE_PER_MIN = 10                 # 每 chat 限速（卡面）
BACKOFF_CAP_S = 60.0              # 断线退避上限
POLL_TIMEOUT_S = 25               # 长轮询挂起时长

# 渠道注册表（WhatsApp/Signal 预留接口位——注册即接线，不实现）
CHANNEL_KINDS: dict = {"telegram": None}


# ---------------------------------------------------------------- Telegram API
class TelegramAPI:
    """Bot API 薄封装（可注入 client/sleep 供零 token 测试）。"""

    def __init__(self, client: httpx.Client | None = None,
                 sleep=time.sleep) -> None:
        self._client = client
        self._sleep = sleep

    def _token(self) -> str:
        """bot token 只存 vault（password 字段位——list 默认脱敏）。"""
        from ..security import vault
        ent = vault.get(VAULT_KEY) or {}
        return str(ent.get("password") or "")

    def call(self, method: str, payload: dict) -> dict:
        """同步调用（轮询线程上下文）。失败抛 RuntimeError（上层退避）。"""
        tok = self._token()
        if not tok and self._client is None:
            raise RuntimeError("渠道未配置 bot token（vault:telegram-bot）")
        url = f"https://api.telegram.org/bot{tok}/{method}"
        if self._client is not None:
            resp = self._client.post(url, json=payload)
        else:
            with httpx.Client(timeout=httpx.Timeout(10, read=POLL_TIMEOUT_S + 10),
                              proxy=CONFIG.resources.proxy or None) as c:
                resp = c.post(url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"telegram {method} HTTP {resp.status_code}: "
                               f"{resp.text[:120]}")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description')}")
        return data.get("result") or {}


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """>4096 分段（按段落/行切，避免劈开 markdown 结构；兜底硬切）。"""
    text = text or ""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n"):
        cand = f"{cur}\n{para}" if cur else para
        if len(cand) > limit and cur:
            parts.append(cur)
            cur = para
        else:
            cur = cand
        while len(cur) > limit:            # 单段超限 → 硬切
            parts.append(cur[:limit])
            cur = cur[limit:]
    if cur:
        parts.append(cur)
    return parts


# ---------------------------------------------------------------- 渠道服务
class ChannelsService:
    """轮询线程 + 命令路由 + 回信增量（单实例；get_service 取）。"""

    def __init__(self, engine, api: TelegramAPI | None = None,
                 sleep=time.sleep) -> None:
        self.engine = engine
        self.api = api or TelegramAPI(sleep=sleep)
        self._sleep = sleep
        self._stop = False
        self._thread = None
        self._offset = 0
        self._backoff = 1.0
        self._rate: dict[str, list[float]] = {}
        self.status = {"running": False, "last_ok": "", "last_error": "",
                       "processed": 0, "backoff_s": 0.0}

    # ---- 生命周期（lifespan 挂载；线程内同步轮询，ENGINE 调用经 loop 投递）
    def start(self, loop=None) -> None:
        self._loop = loop
        self._stop = False
        import threading
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="loadn-channels")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def _run(self) -> None:
        self.status["running"] = True
        while not self._stop:
            try:
                updates = self.api.call("getUpdates", {
                    "offset": self._offset, "timeout": POLL_TIMEOUT_S,
                    "allowed_updates": ["message", "callback_query"]})
                self._backoff = 1.0
                self.status["backoff_s"] = 0.0
                self.status["last_ok"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                for up in updates:
                    self._offset = max(self._offset,
                                       int(up.get("update_id", 0)) + 1)
                    self.handle_update(up)
                self.push_completed_turns()
            except Exception as e:                    # noqa: BLE001 — 断线退避
                self.status["last_error"] = f"{e}"[:200]
                self.status["backoff_s"] = self._backoff
                self._sleep(self._backoff)
                self._backoff = min(self._backoff * 2, BACKOFF_CAP_S)
        self.status["running"] = False

    # ---- 消息路由
    def handle_update(self, up: dict) -> None:
        from ..security.audit import audit
        if "callback_query" in up:                    # P9b 审批按钮
            self._handle_callback(up["callback_query"])
            return
        msg = up.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        frm = msg.get("from") or {}
        text = str(msg.get("text") or "").strip()
        if not chat_id or not text:
            return
        if frm.get("is_bot"):                         # 防 bot loop
            return
        if chat_id not in (CONFIG.channels.telegram_allow or []):
            audit("channel", {"action": "ignore_not_allowed",
                              "chat_id": chat_id,
                              "from": frm.get("username")})
            return
        if self._rate_hit(chat_id):
            self.api.call("sendMessage", {
                "chat_id": chat_id,
                "text": "⚠️ 太快了（限 10 条/分钟），稍等再发。"})
            audit("channel", {"action": "rate_limited", "chat_id": chat_id})
            return
        self.status["processed"] += 1
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0]                       # /new@bot → /new
        if cmd in ("/new", "/bind", "/status", "/unbind"):
            self._command(chat_id, cmd, arg.strip())
            audit("channel", {"action": "command", "cmd": cmd,
                              "chat_id": chat_id})
        else:
            sid = self._bound_sid(chat_id)
            if sid is None:
                self.api.call("sendMessage", {
                    "chat_id": chat_id,
                    "text": "尚未绑定会话：/new 新建，或 /bind <会话id>。"})
                return
            self._submit(sid, text)
            audit("channel", {"action": "message", "chat_id": chat_id,
                              "sid": sid})

    def _rate_hit(self, chat_id: str) -> bool:
        now = time.monotonic()
        win = [t for t in self._rate.get(chat_id, []) if now - t < 60]
        if len(win) >= RATE_PER_MIN:
            self._rate[chat_id] = win
            return True
        win.append(now)
        self._rate[chat_id] = win
        return False

    # ---- 命令
    def _command(self, chat_id: str, cmd: str, arg: str) -> None:
        from .. import db as db_mod
        from ..util import iso
        if cmd == "/new":
            title = f"[tg] {arg[:40]}" if arg else "[tg] 渠道会话"
            sid = self._create_session(title)
            with db_mod.conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO channel_bindings"
                    "(chat_id, session_id, last_turn_id, created_at)"
                    " VALUES(?,?,0,?)", (chat_id, sid, iso()))
            self.api.call("sendMessage", {
                "chat_id": chat_id,
                "text": f"已新建并绑定会话 `{sid}`。\n直接发消息即可；"
                        "/status 看状态，/unbind 解绑。"})
        elif cmd == "/bind":
            sid = arg.strip()
            from .. import db as db_mod2
            with db_mod2.conn() as c:
                ok = c.execute("SELECT id FROM sessions WHERE id=?",
                               (sid,)).fetchone() is not None
            if not ok:
                self.api.call("sendMessage", {
                    "chat_id": chat_id, "text": f"会话不存在：{sid}"})
                return
            with db_mod.conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO channel_bindings"
                    "(chat_id, session_id, last_turn_id, created_at)"
                    " VALUES(?,?,0,?)", (chat_id, sid, iso()))
            self.api.call("sendMessage", {
                "chat_id": chat_id, "text": f"已绑定会话 `{sid}`。"})
        elif cmd == "/status":
            sid = self._bound_sid(chat_id)
            st = "未绑定（/new 或 /bind <sid>）" if sid is None else f"绑定 {sid}"
            self.api.call("sendMessage", {
                "chat_id": chat_id,
                "text": f"渠道状态：运行中，已处理 {self.status['processed']} 条；"
                        f"{st}"})
        elif cmd == "/unbind":
            with db_mod.conn() as c:
                c.execute("DELETE FROM channel_bindings WHERE chat_id=?",
                          (chat_id,))
            self.api.call("sendMessage", {"chat_id": chat_id,
                                          "text": "已解绑。"})

    def _bound_sid(self, chat_id: str) -> str | None:
        from .. import db as db_mod
        with db_mod.conn() as c:
            row = c.execute(
                "SELECT session_id FROM channel_bindings WHERE chat_id=?",
                (chat_id,)).fetchone()
        return row["session_id"] if row else None

    def _create_session(self, title: str) -> str:
        """线程安全建会话（复用 scheduler._fire_new_session 的路径——
        profile auto + workspace 脚手架）。"""
        from .. import profile as profile_mod
        from .. import workspace as ws_mod
        prof = profile_mod.auto_match(title)
        sid, _ = ws_mod.create_session(title, prof, None, None)
        return sid

    def _submit(self, sid: str, text: str) -> None:
        """投递用户消息（ENGINE.submit 是协程——经主 loop 线程安全调度）。"""
        import asyncio
        coro = self.engine.submit(sid, text, mode="background")
        loop = getattr(self, "_loop", None)
        if loop is not None:
            asyncio.run_coroutine_threadsafe(coro, loop)   # 轮询线程→主 loop
            return
        try:                                              # 已有 loop（测试/主线程）
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:                              # 无 loop 直跑
            asyncio.run(coro)

    # ---- 回信（turn 终态增量）
    def push_completed_turns(self) -> None:
        from .. import db as db_mod
        with db_mod.conn() as c:
            rows = c.execute(
                "SELECT b.chat_id, b.session_id, b.last_turn_id, t.id AS tid"
                " FROM channel_bindings b JOIN turns t"
                " ON t.session_id=b.session_id AND t.id>b.last_turn_id"
                " AND t.status IN ('done','error','stopped')"
                " ORDER BY t.id").fetchall()
            for r in rows:
                last = c.execute(
                    "SELECT content FROM messages WHERE session_id=? "
                    "AND role='assistant' AND turn_id=? ORDER BY id DESC LIMIT 1",
                    (r["session_id"], r["tid"])).fetchone()
                text = (last["content"] if last else
                        f"（turn #{r['tid']} 结束，无文本输出）")
                self.send_reply(r["chat_id"], text[:8000])
                c.execute("UPDATE channel_bindings SET last_turn_id=? "
                          "WHERE chat_id=?", (r["tid"], r["chat_id"]))

    def send_reply(self, chat_id: str, text: str) -> None:
        for i, part in enumerate(split_message(text)):
            self.api.call("sendMessage", {
                "chat_id": chat_id, "text": part,
                "parse_mode": "Markdown"})

    # ---- P9b 审批按钮
    def notify_approval(self, aid: int, sid: str, summary: str) -> None:
        """审批请求 → 绑定该会话的 chat 收到内联键盘（无绑定则静默）。"""
        from .. import db as db_mod
        with db_mod.conn() as c:
            row = c.execute(
                "SELECT chat_id FROM channel_bindings WHERE session_id=?",
                (sid,)).fetchone()
        if row is None or not self.status.get("running"):
            return
        self.api.call("sendMessage", {
            "chat_id": row["chat_id"],
            "text": f"🔔 审批请求 #{aid}：{summary[:200]}\n"
                    "批准后确认码会自动注回会话。",
            "reply_markup": {"inline_keyboard": [[
                {"text": "Approve ✅", "callback_data": f"apr:{aid}:a"},
                {"text": "Deny ❌", "callback_data": f"apr:{aid}:d"}]]}})

    def _handle_callback(self, cq: dict) -> None:
        from ..security import approve as approve_mod
        from ..security.audit import audit
        data = str(cq.get("data") or "")
        chat_id = str((cq.get("message") or {}).get("chat", {}).get("id") or "")
        cb_id = str(cq.get("id") or "")
        if chat_id not in (CONFIG.channels.telegram_allow or []):
            return                                     # 非白名单回调忽略
        if not data.startswith("apr:"):
            self.api.call("answerCallbackQuery",
                          {"callback_query_id": cb_id, "text": "未知操作"})
            return
        _, aid_s, verdict = data.split(":", 2)
        try:
            out = approve_mod.decide(int(aid_s), verdict == "a")
        except Exception as e:                         # noqa: BLE001
            out = {"ok": False, "error": str(e)}
        if out.get("ok") and out.get("status") == "approved":
            code = str(out.get("code") or "")
            sid = self._bound_sid(chat_id)
            if sid:
                self._steer(sid, f"审批 #{aid_s} 已批准（Telegram 渠道），"
                                 f"确认码 {code}")
            self.api.call("answerCallbackQuery", {
                "callback_query_id": cb_id, "text": "已批准，码已注回会话"})
        elif out.get("ok"):
            self.api.call("answerCallbackQuery", {
                "callback_query_id": cb_id, "text": "已否决"})
        else:
            self.api.call("answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": f"失败：{out.get('error', '?')}"})
        audit("channel", {"action": "approval_callback", "aid": aid_s,
                          "verdict": verdict, "chat_id": chat_id})

    def _steer(self, sid: str, text: str) -> None:
        st = self.engine.steer_if_running(sid, text)
        if st is None:
            log.info("渠道审批码注入：会话 %s 未在跑，转为后台新 turn", sid)
            self._submit(sid, text)


_SERVICE: dict = {}


def get_service(engine) -> ChannelsService:
    if "svc" not in _SERVICE:
        _SERVICE["svc"] = ChannelsService(engine)
    return _SERVICE["svc"]


def reset_service() -> None:
    """测试隔离：丢弃单例。"""
    _SERVICE.pop("svc", None)
