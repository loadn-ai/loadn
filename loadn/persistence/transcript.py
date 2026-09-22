"""transcript 持久化：JSONL append-only（工程详设 §4.11）。

每行一个事件 {type, ts, session_id, parent_uuid, uuid, payload}，
type ∈ init|user|assistant|tool_result|system|compact|result——事件名与
claude CLI stream-json 同族，resume = 重放重建 messages（遇 compact 事件
截断前文，天然减小恢复体积）。每 turn 结束 fsync。
"""
from __future__ import annotations

import json
import time
import uuid as uuid_mod
from pathlib import Path

from loadn import loadn_home
from loadn.types import Message


class TranscriptStore:
    def __init__(self, session_id: str, home: Path | None = None) -> None:
        self.session_id = session_id
        self.dir = (home or loadn_home()) / "sessions" / session_id
        self.path = self.dir / "transcript.jsonl"
        self._last_uuid: str | None = None

    # ------------------------------------------------------------ 写
    def _ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def append(self, type_: str, payload: dict | None = None, *,
               fsync: bool = False) -> str:
        """追加事件；返回事件 uuid（parent_uuid 链自动维护）。"""
        self._ensure_dir()
        ev = {
            "type": type_,
            "ts": round(time.time(), 3),
            "session_id": self.session_id,
            "parent_uuid": self._last_uuid,
            "uuid": str(uuid_mod.uuid4()),
            "payload": payload or {},
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            if fsync:
                f.flush()
                import os as _os
                _os.fsync(f.fileno())
        self._last_uuid = ev["uuid"]
        return ev["uuid"]

    # ------------------------------------------------------------ 读
    def read_events(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue   # 被杀时的半行
        return out

    def last_uuid(self) -> str | None:
        evs = self.read_events()
        return evs[-1].get("uuid") if evs else None

    def replay_messages(self) -> list[Message]:
        """重建 messages（compact 点截断 + 摘要头回注——恢复会话不丢交接）。

        连续 tool_result 事件归并进同一条 user 消息（Anthropic 形态：一批
        tool_result 属于 assistant 消息后的下一条 user 消息）。
        """
        evs = self.read_events()
        last_compact = -1
        compact_summary = ""
        for i, ev in enumerate(evs):
            if ev.get("type") == "compact":
                last_compact = i
                compact_summary = str((ev.get("payload") or {}).get("summary") or "")
        messages: list[Message] = []
        if last_compact >= 0 and compact_summary:
            from loadn.types import TextBlock as _TB
            messages.append(Message(role="user", content=[_TB(
                text=f"【上下文已压缩】之前的会话交接摘要如下，请以此接力继续，"
                     f"不要重做已完成步骤：\n\n{compact_summary}\n\n请继续当前任务。")]))
        for ev in evs[last_compact + 1:]:
            t = ev.get("type")
            payload = ev.get("payload") or {}
            if t == "user":
                messages.append(Message.from_dict(payload))
            elif t == "assistant":
                messages.append(Message.from_dict(payload))
            elif t == "tool_result":
                blk = payload
                if messages and messages[-1].role == "user" \
                        and not messages[-1].text_parts() \
                        and all(b.__class__.__name__ == "ToolResultBlock"
                                for b in messages[-1].content):
                    # 归并进连续 tool_result 的 user 消息
                    messages[-1] = Message(role="user", content=[
                        *messages[-1].content, _result_block(blk)])
                else:
                    messages.append(Message(role="user", content=[_result_block(blk)]))
        return messages

    def replay_state(self) -> dict:
        """重放 todos/compact_points 等 SessionState 片段（TodoWrite 经
        assistant 事件的 tool_use 输入重建）。"""
        todos: list[dict] = []
        compact_points: list[str] = []
        for ev in self.read_events():
            t = ev.get("type")
            if t == "compact":
                compact_points.append(ev.get("uuid") or "")
            elif t == "assistant":
                content = (ev.get("payload") or {}).get("content") or []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use" \
                            and b.get("name") == "TodoWrite" \
                            and isinstance((b.get("input") or {}).get("todos"), list):
                        todos = b["input"]["todos"]
        return {"todos": todos, "compact_points": compact_points}


def _result_block(blk: dict):
    from loadn.types import ToolResultBlock
    return ToolResultBlock(tool_use_id=blk.get("tool_use_id") or "",
                           content=blk.get("content"),
                           is_error=bool(blk.get("is_error")))
