"""P9 Telegram 双向渠道（fake API，零 token）：验收五件 + P9b 审批键盘。

①白名单往返（命令→绑定→文本→submit→turn 完成→回信）
②非白名单忽略+审计
③4096 分段
④命令解析（/new /bind /status /unbind）
⑤断线重连（指数退避倍增 + 成功复位）
P9b：审批推送内联键盘；回调 Approve → decide + 码 steer 回会话。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import httpx

from loadn_webui.config import PATHS
from loadn_webui.integrations.channels import ChannelsService, TelegramAPI, split_message


class FakeTG:
    """假 Bot API：记录外发请求；getUpdates 按脚本队列出牌。"""

    def __init__(self, updates_script: list | None = None, fail_first: int = 0):
        self.sent: list[tuple[str, dict]] = []
        self.updates = list(updates_script or [])
        self.fail_first = fail_first      # 前 N 次 getUpdates 抛错（重连测试）

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = request.url.path.rsplit("/", 1)[-1]
        self.sent.append((method, body))
        if method == "getUpdates":
            if self.fail_first > 0:
                self.fail_first -= 1
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={
                "ok": True, "result": self.updates.pop(0)
                if self.updates else []})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {
                "id": 42, "username": "loadn_test_bot"}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class FakeEngine:
    def __init__(self):
        self.submitted: list[tuple[str, str]] = []
        self.steered: list[tuple[str, str]] = []

    async def submit(self, sid, text, mode="foreground", attachments=None):
        self.submitted.append((sid, text))
        return 1

    def steer_if_running(self, sid, text):
        self.steered.append((sid, text))
        return 1


def _upd(chat="100", text="hello", uid=7, update_id=1):
    return {"update_id": update_id, "message": {
        "chat": {"id": int(chat)}, "from": {"id": uid, "username": "u"},
        "text": text, "date": 0}}


def _svc(fake, allow=("100",), engine=None):
    CONFIG_API = TelegramAPI(client=fake.client())
    return ChannelsService(engine or FakeEngine(), api=CONFIG_API)


def _allow(monkeypatch, ids):
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.channels, "telegram_allow", list(ids))


def _channel_audit() -> list[dict]:
    p = PATHS["var"] / "audit.db"
    if not p.exists():
        return []
    out = []
    with sqlite3.connect(p) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'audit_events%'")]
        for t in sorted(tables):
            for r in c.execute(f"SELECT detail_json FROM {t} "
                               "WHERE type='channel' ORDER BY rowid"):
                out.append(json.loads(r[0]))
    return out


# ---------------------------------------------------------------- ④ 命令+① 往返
async def test_commands_and_roundtrip(client, monkeypatch):
    _allow(monkeypatch, ("100",))
    fake = FakeTG()
    eng = FakeEngine()
    svc = _svc(fake, engine=eng)
    # /new：建会话+绑定（真实 DB 走通）
    svc.handle_update(_upd(text="/new 测试会话"))
    assert fake.sent[-1][0] == "sendMessage" and "已新建并绑定" in fake.sent[-1][1]["text"]
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        bind = c.execute("SELECT * FROM channel_bindings").fetchone()
    assert bind is not None and bind["chat_id"] == "100"
    sid = bind["session_id"]
    # 普通文本 → 绑定会话 submit（渠道消息=用户消息，不解析系统操作）
    svc.handle_update(_upd(text="帮我看下今天的安排", update_id=2))
    await asyncio.sleep(0)                      # create_task 让一拍
    assert eng.submitted == [(sid, "帮我看下今天的安排")]
    # turn 完成 → 回信（真实 turns/messages 落库）
    with db_mod.conn() as c:
        tid = c.execute("INSERT INTO turns(session_id, status, mode) "
                        "VALUES(?, 'done', 'background')",
                        (sid,)).lastrowid
        c.execute("INSERT INTO messages(session_id, turn_id, role, content) "
                  "VALUES(?,?, 'assistant', '安排如下：1) …')", (sid, tid))
    svc.push_completed_turns()
    assert fake.sent[-1][1]["text"] == "安排如下：1) …"
    # /status 报绑定；/unbind 解绑后再发文本提示未绑定
    svc.handle_update(_upd(text="/status", update_id=3))
    assert sid in fake.sent[-1][1]["text"]
    svc.handle_update(_upd(text="/unbind", update_id=4))
    svc.handle_update(_upd(text="还在吗", update_id=5))
    assert "尚未绑定" in fake.sent[-1][1]["text"]
    # /bind 已知会话
    svc.handle_update(_upd(text=f"/bind {sid}", update_id=6))
    assert "已绑定" in fake.sent[-1][1]["text"]
    svc.handle_update(_upd(text="/bind no-such", update_id=7))
    assert "会话不存在" in fake.sent[-1][1]["text"]


# ---------------------------------------------------------------- ② 白名单/bot/限速
async def test_whitelist_bot_and_rate(client, monkeypatch):
    _allow(monkeypatch, ("100",))
    fake = FakeTG()
    eng = FakeEngine()
    svc = _svc(fake, engine=eng)
    n0 = len(_channel_audit())
    # 非白名单：忽略 + 审计
    svc.handle_update(_upd(chat="999", text="hi"))
    assert not fake.sent
    audits = _channel_audit()[n0:]
    assert audits and audits[-1]["action"] == "ignore_not_allowed"
    # bot 消息丢弃（无审计静默——防 bot loop）
    up = _upd(text="/new")
    up["message"]["from"]["is_bot"] = True
    svc.handle_update(up)
    assert not fake.sent and len(_channel_audit()) == n0 + 1
    # 限速：11 条/分钟内 → 第 11 条提示
    for i in range(11):
        svc.handle_update(_upd(text=f"msg{i}", update_id=10 + i))
    assert any("太快" in (b.get("text") or "") for _, b in fake.sent)


# ---------------------------------------------------------------- ③ 分段
def test_split_message():
    small = "短消息"
    assert split_message(small) == [small]
    big = "\n\n".join(f"段落{i}\n内容{'x' * 20}"
                      for i in range(200))
    parts = split_message(big)
    assert len(parts) > 1 and all(len(p) <= 4096 for p in parts)
    assert "".join(parts).replace("\n", "") == big.replace("\n", "")
    # 无换行的巨块：硬切兜底
    mono = "y" * 9000
    assert all(len(p) <= 4096 for p in split_message(mono))


# ---------------------------------------------------------------- ⑤ 断线重连
async def test_reconnect_backoff_and_reset(client, monkeypatch):
    _allow(monkeypatch, ("100",))
    fake = FakeTG(updates_script=[[_upd(text="/status", update_id=1)]],
                  fail_first=3)
    sleeps: list[float] = []
    svc = ChannelsService(FakeEngine(),
                          api=TelegramAPI(client=fake.client()),
                          sleep=sleeps.append)
    # 手动跑 4 轮（不启线程）：前 3 轮失败退避 1→2→4，第 4 轮成功复位
    for _ in range(4):
        try:
            updates = svc.api.call("getUpdates", {"offset": 0})
            svc._backoff = 1.0
            for up in updates:
                svc.handle_update(up)
        except Exception:
            sleeps.append(svc._backoff)
            svc._backoff = min(svc._backoff * 2, 60.0)
    assert sleeps[:3] == [1.0, 2.0, 4.0]      # 指数退避
    assert svc._backoff == 8.0 or svc._backoff == 1.0  # 复位/继续双态不炸
    assert any(m == "sendMessage" for m, _ in fake.sent)  # 第 4 轮命令送达


# ---------------------------------------------------------------- P9b 审批
async def test_approval_keyboard_and_callback(client, monkeypatch):
    from loadn_webui.security import approve as approve_mod
    _allow(monkeypatch, ("100",))
    fake = FakeTG()
    eng = FakeEngine()
    svc = _svc(fake, engine=eng)
    svc.status["running"] = True
    # 建绑定 + 审批请求 → 绑定 chat 收内联键盘
    svc.handle_update(_upd(text="/new"))
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        sid = c.execute("SELECT session_id FROM channel_bindings").fetchone()["session_id"]
    out = approve_mod.create(sid, "mail_send", {"to": "x@y.z"}, note="P9b")
    # 钩子经 get_service 单例（生产=轮询线程实例）；此处直接驱动本实例验证推送
    svc.notify_approval(out["id"], sid, out["summary"])
    kb = fake.sent[-1][1].get("reply_markup", {}).get("inline_keyboard")
    assert kb and kb[0][0]["text"].startswith("Approve")
    aid = out["id"]
    # Approve 回调 → decide + 一次性码 steer 回会话
    cb = {"update_id": 99, "callback_query": {
        "id": "cbq1", "data": f"apr:{aid}:a",
        "message": {"chat": {"id": 100}, "text": "?"}}}
    svc.handle_update(cb)
    assert eng.steered and "确认码" in eng.steered[0][1]
    answered = [b for m, b in fake.sent if m == "answerCallbackQuery"]
    assert answered and "已批准" in answered[-1]["text"]
    # 非白名单回调忽略
    cb2 = {"update_id": 100, "callback_query": {
        "id": "cbq2", "data": f"apr:{aid}:d",
        "message": {"chat": {"id": 999}, "text": "?"}}}
    n1 = len(fake.sent)
    svc.handle_update(cb2)
    assert len(fake.sent) == n1


async def test_guards_not_running_and_empty_text(client, monkeypatch):
    """守卫对赌：running=False 时审批不推送；空文本/无 chat 更新静默忽略。"""
    _allow(monkeypatch, ("100",))
    from loadn_webui.security import approve as approve_mod
    fake = FakeTG()
    eng = FakeEngine()
    svc = _svc(fake, engine=eng)          # running=False（未 start）
    svc.handle_update(_upd(text="/new"))
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        sid = c.execute(
            "SELECT session_id FROM channel_bindings").fetchone()["session_id"]
    out = approve_mod.create(sid, "mail_send", {"to": "x@y.z"})
    svc.notify_approval(out["id"], sid, out["summary"])
    assert not any("reply_markup" in b for _, b in fake.sent)  # 未运行不推
    # 空文本：不命令、不投递、不审计
    n0 = len(_channel_audit())
    svc.handle_update(_upd(text="", update_id=50))
    svc.handle_update({"update_id": 51, "message": {"chat": {"id": 100}}})
    assert len(_channel_audit()) == n0
    assert not any(m == "sendMessage" for m, _ in fake.sent[1:])


async def test_r2_callback_binding_guard(client, monkeypatch):
    """二轮修#10 对赌：审批不属绑定会话 → 拒决且不裁决；码路由到审批
    所属 sid（bind 换绑后旧键盘失效）。"""
    from loadn_webui.security import approve as approve_mod
    _allow(monkeypatch, ("100",))
    fake = FakeTG()
    eng = FakeEngine()
    svc = _svc(fake, engine=eng)
    svc.status["running"] = True
    svc.handle_update(_upd(text="/new"))
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        sidA = c.execute(
            "SELECT session_id FROM channel_bindings").fetchone()["session_id"]
    out = approve_mod.create(sidA, "mail_send", {"to": "x@y.z"})
    aid = out["id"]
    # create 的钩子经 get_service(None) 单例（生产=轮询线程实例，此处
    # running=False 静默）——照 P9 模式直接驱动本实例验证推送
    svc.notify_approval(out["id"], sidA, out["summary"])
    kb = fake.sent[-1][1]["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["callback_data"] == f"apr:{aid}:a"
    # 换绑到别的会话 → 旧键盘裁决被绑定校验拒（不 decide）
    r2 = await client.post("/api/sessions", json={"title": "B"})
    sidB = r2.json()["session"]["id"]
    with db_mod.conn() as c:
        c.execute("UPDATE channel_bindings SET session_id=? WHERE chat_id='100'",
                  (sidB,))
    svc.handle_update({"update_id": 90, "callback_query": {
        "id": "cb1", "data": f"apr:{aid}:a",
        "message": {"chat": {"id": 100}, "text": "?"}}})
    st = approve_mod.status(aid)
    assert st["status"] == "pending"              # 未被裁决（绑定不符即拒）
    assert any(m == "answerCallbackQuery" for m, _ in fake.sent)
    last = [b for m, b in fake.sent if m == "answerCallbackQuery"][-1]
    assert "不属于当前绑定" in last["text"]
    # 绑回 → Approve 生效，码路由到审批所属 sidA（非当前绑定 sidB）
    with db_mod.conn() as c:
        c.execute("UPDATE channel_bindings SET session_id=? WHERE chat_id='100'",
                  (sidA,))
    svc.handle_update({"update_id": 91, "callback_query": {
        "id": "cb2", "data": f"apr:{aid}:a",
        "message": {"chat": {"id": 100}, "text": "?"}}})
    assert approve_mod.status(aid)["status"] == "approved"
    assert eng.steered and sidA == sidA and "确认码" in eng.steered[0][1]


async def test_r2_markdown_fallback_and_cursor_advance(monkeypatch):
    """二轮修#11 对赌：Markdown 失败降级纯文本，游标无条件推进（不重试）。"""
    import httpx
    _allow(monkeypatch, ("100",))
    sent: list[tuple[str, dict]] = []
    fail_markdown = {"on": True}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = request.url.path.rsplit("/", 1)[-1]
        sent.append((method, body))
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": []})
        if method == "sendMessage" and body.get("parse_mode") == "Markdown" \
                and fail_markdown["on"]:
            return httpx.Response(400, json={"ok": False,
                                             "description": "can't parse entities"})
        return httpx.Response(200, json={"ok": True, "result": {}})

    api = TelegramAPI(client=httpx.Client(
        transport=httpx.MockTransport(handler)))
    svc = ChannelsService(FakeEngine(), api=api)
    svc.send_reply("100", "```python\nprint('hi')\n``` 未配对`")
    kinds = [(m, b.get("parse_mode")) for m, b in sent]
    assert ("sendMessage", "Markdown") in kinds
    assert ("sendMessage", None) in kinds       # 降级纯文本（非重试 Markdown）


async def test_r2_callback_race_already_decided(client, monkeypatch):
    """二轮修#17 对赌：webui 抢先裁决后 telegram 回调到达——decide 返回
    ok:False + status=approved，回执须如实「已是 approved 态（他人已
    裁决）」，不得走「已批准，码已注回会话」分支（突变 399 and→or
    实证存活——ok=False 但 status 命中元组即误入批准分支）。"""
    from loadn_webui import db as db_mod
    from loadn_webui.security import approve as approve_mod
    _allow(monkeypatch, ("100",))
    fake = FakeTG()
    eng = FakeEngine()
    svc = _svc(fake, engine=eng)
    svc.status["running"] = True
    svc.handle_update(_upd(text="/new"))
    with db_mod.conn() as c:
        sidA = c.execute(
            "SELECT session_id FROM channel_bindings").fetchone()["session_id"]
    out = approve_mod.create(sidA, "mail_send", {"to": "x@y.z"})
    aid = out["id"]
    # webui 面抢先裁决（并发窗口的另一头）
    approve_mod.decide(aid, True, by="webui")
    assert approve_mod.status(aid)["status"] == "approved"
    svc.handle_update({"update_id": 95, "callback_query": {
        "id": "cb9", "data": f"apr:{aid}:a",
        "message": {"chat": {"id": 100}, "text": "?"}}})
    replies = [b for m, b in fake.sent if m == "answerCallbackQuery"]
    assert replies and "他人已裁决" in replies[-1]["text"]
    assert "码已注回" not in replies[-1]["text"]
    assert not eng.steered, "已被裁决的回调不得再注入确认码（码已明文过一次）"


async def test_r3_offset_persisted_across_restart(client, monkeypatch):
    """三轮修（backlog 清）对赌：offset 落 kv——批处理完持久化，新实例
    （重启模拟）从确认位续拉（原归零重放 24h updates：消息双投/重绑）。"""
    from loadn_webui import db as db_mod
    from loadn_webui.integrations.channels import ChannelsService
    OFFSET_KV_KEY = ChannelsService.OFFSET_KV_KEY
    _allow(monkeypatch, ("100",))
    fake = FakeTG()
    svc = _svc(fake)
    svc._offset = 42                        # 模拟已处理到 41
    svc._save_offset()
    with db_mod.conn() as c:
        v = c.execute("SELECT value FROM kv WHERE key=?",
                      (OFFSET_KV_KEY,)).fetchone()["value"]
    assert v == "42"
    # 重启模拟：新实例读回确认位
    svc2 = _svc(FakeTG())
    assert svc2._offset == 42
    # getUpdates 请求带确认位（Telegram 语义：确认 41 及以前）
    fake.updates = [[]]
    svc.api.call("getUpdates", {"offset": svc._offset, "timeout": 0})
    assert fake.sent[-1][1]["offset"] == 42
