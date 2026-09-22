"""SessionManager（工程详设 §3/§4.11）：会话创建/resume/fork/轮换 + 状态重建。

会话 = $LOADN_HOME/sessions/<sid>/transcript.jsonl（唯一真相源）+
SessionState（todos/compact_points 等重放重建）。messages_for_turn 每次
从 transcript 重放（遇 compact 截断），turn 内的内存上下文由 loop 维护。
"""
from __future__ import annotations

import uuid as uuid_mod
from pathlib import Path

from loadn.persistence import db as db_mod
from loadn.persistence.transcript import TranscriptStore
from loadn.types import Message, SessionState


class SessionManager:
    def __init__(self, session_id: str, cwd: Path, home: Path | None = None) -> None:
        self.session_id = session_id
        self.cwd = Path(cwd)
        self.transcript = TranscriptStore(session_id, home=home)
        self.state = SessionState()
        self._replay_state()

    # ------------------------------------------------------------ 构造
    @classmethod
    def create(cls, cwd: Path, title: str = "", home: Path | None = None,
               parent_id: str = "", session_id: str | None = None) -> SessionManager:
        sid = session_id or str(uuid_mod.uuid4())
        sm = cls(sid, cwd, home=home)
        sm.transcript.append("init", {"title": title, "cwd": str(cwd),
                                      "parent": parent_id}, fsync=True)
        cls._db_upsert(sm, title, parent_id)
        return sm

    @classmethod
    def resume(cls, sid: str, cwd: Path, home: Path | None = None) -> SessionManager:
        """按 id 续会话；transcript 不存在视为新会话（init 事件补记）。"""
        sm = cls(sid, cwd, home=home)
        if not sm.transcript.path.exists():
            sm.transcript.append("init", {"title": "", "cwd": str(cwd),
                                          "resumed_fresh": True}, fsync=True)
        return sm

    @classmethod
    def fork(cls, sid: str, cwd: Path, home: Path | None = None) -> SessionManager:
        """fork：复制 transcript 到新会话 id（此后两线独立演进）。"""
        src = TranscriptStore(sid, home=home)
        new = cls.create(cwd, title="", home=home, parent_id=sid)
        if src.path.exists():
            for ev in src.read_events():
                if ev.get("type") == "init":
                    continue
                new.transcript.append(ev.get("type") or "system",
                                       ev.get("payload") or {})
            new._replay_state()
        return new

    def rotate(self) -> str:
        """上下文轮换：新 sid 开档（宿主外层轮换时调用；旧档保留）。"""
        new = SessionManager.create(self.cwd, home=self.transcript.dir.parent.parent)
        return new.session_id

    # ------------------------------------------------------------ turn 协作
    def messages_for_turn(self) -> list[Message]:
        """重放重建上下文（compact 点截断）。"""
        return self.transcript.replay_messages()

    def append_user(self, text: str) -> None:
        self.transcript.append("user", {"role": "user",
                                        "content": [{"type": "text", "text": text}]})

    def append_event(self, type_: str, payload: dict | None = None, *,
                     fsync: bool = False) -> None:
        self.transcript.append(type_, payload, fsync=fsync)

    def last_compact_summary(self) -> str:
        """最后一个 compact 事件的摘要文本（无则空串——UPDATE 模式的 prev）。"""
        for ev in reversed(self.transcript.read_events()):
            if ev.get("type") == "compact":
                return str((ev.get("payload") or {}).get("summary") or "")
        return ""

    def mark_compact(self, summary: str) -> None:
        self.transcript.append("compact", {"summary": summary}, fsync=True)
        self.state.compact_points.append(self.transcript.last_uuid() or "")
        self._replay_state()

    def record_usage(self, summary) -> None:
        """turn 记账进 session.db（索引库，真相在 transcript result 事件）。"""
        try:
            with db_mod.conn(self.transcript.dir.parent.parent) as c:
                db_mod.upsert_session(c, self.session_id, cwd=str(self.cwd))
                db_mod.add_usage(c, self.session_id, summary.usage,
                                 summary.model_usage or None,
                                 next(iter(summary.model_usage or {"": None}), ""))
        except Exception:  # noqa: BLE001 — 索引库故障不影响主流程
            pass

    # ------------------------------------------------------------ 内部
    def _replay_state(self) -> None:
        from loadn.types import Todo
        st = self.transcript.replay_state()
        self.state.compact_points = st.get("compact_points") or []
        todos: list[Todo] = []
        for i, t in enumerate(st.get("todos") or []):
            if isinstance(t, dict):
                todos.append(Todo(
                    id=f"t{i + 1}",
                    subject=t.get("content") or t.get("subject") or "任务",
                    description=t.get("description") or t.get("content") or "",
                    status=t.get("status", "pending"),
                    activeForm=t.get("activeForm", "")))
        self.state.todos = todos

    @staticmethod
    def _db_upsert(sm: SessionManager, title: str, parent_id: str = "") -> None:
        try:
            with db_mod.conn(sm.transcript.dir.parent.parent) as c:
                db_mod.upsert_session(c, sm.session_id, title=title,
                                      cwd=str(sm.cwd), parent_id=parent_id)
        except Exception:  # noqa: BLE001
            pass
