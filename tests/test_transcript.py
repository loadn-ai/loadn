"""transcript 持久化：replay 重建 / tool_result 归并 / compact 截断。"""
from __future__ import annotations

from hahaness.core.session import SessionManager
from hahaness.persistence.transcript import TranscriptStore
from hahaness.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


def _assistant_text(t: str) -> dict:
    return Message(role="assistant", content=[TextBlock(text=t)]).to_dict()


def _assistant_tool(tu_id: str, name: str, args: dict) -> dict:
    return Message(role="assistant",
                   content=[ToolUseBlock(id=tu_id, name=name, input=args)]).to_dict()


def _tool_result(tu_id: str, content: str, is_error: bool = False) -> dict:
    return ToolResultBlock(tool_use_id=tu_id, content=content,
                           is_error=is_error).to_dict()


def test_replay_basic_grouping(tmp_path):
    ts = TranscriptStore("s1", home=tmp_path)
    ts.append("user", Message(role="user", content=[TextBlock(text="干活")]).to_dict())
    ts.append("assistant", _assistant_tool("tu_1", "Bash", {"command": "ls"}))
    ts.append("tool_result", _tool_result("tu_1", "a\nb"))
    ts.append("tool_result", _tool_result("tu_2", "err", is_error=True))
    ts.append("assistant", _assistant_text("完成"))
    msgs = ts.replay_messages()
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    # 连续 tool_result 归并成同一条 user 消息
    assert len(msgs[2].content) == 2
    assert all(isinstance(b, ToolResultBlock) for b in msgs[2].content)
    assert msgs[2].content[1].is_error


def test_compact_truncates_history(tmp_path):
    ts = TranscriptStore("s2", home=tmp_path)
    ts.append("user", Message(role="user", content=[TextBlock(text="旧1")]).to_dict())
    ts.append("assistant", _assistant_text("旧答"))
    ts.append("compact", {"summary": "前面已压缩"})
    ts.append("user", Message(role="user", content=[TextBlock(text="新1")]).to_dict())
    ts.append("assistant", _assistant_text("新答"))
    msgs = ts.replay_messages()
    texts = [m.text_parts() for m in msgs]
    assert "旧1" not in texts and "新1" in texts


def test_replay_state_todos(tmp_path):
    ts = TranscriptStore("s3", home=tmp_path)
    ts.append("assistant", {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "tu_1", "name": "TodoWrite",
                     "input": {"todos": [
                         {"content": "步骤一", "status": "completed"},
                         {"content": "步骤二", "status": "in_progress"}]}}]})
    st = ts.replay_state()
    assert [t["status"] for t in st["todos"]] == ["completed", "in_progress"]


def test_half_line_tolerated(tmp_path):
    import json as _json
    ts = TranscriptStore("s4", home=tmp_path)
    ts._ensure_dir()
    with ts.path.open("a", encoding="utf-8") as f:
        f.write(_json.dumps({"type": "user", "payload": {"role": "user",
             "content": [{"type": "text", "text": "ok"}]},
             "uuid": "u1"}) + "\n")
        f.write('{"type":"assistant","payl')   # 被杀半行
    assert len(ts.read_events()) == 1


def test_session_manager_roundtrip(tmp_path):
    sm = SessionManager.create(tmp_path / "ws", title="t", home=tmp_path)
    sid = sm.session_id
    sm.append_user("第一问")
    sm.append_event("assistant", _assistant_text("第一答"))
    # resume：新实例按 id 重放
    sm2 = SessionManager.resume(sid, tmp_path / "ws", home=tmp_path)
    msgs = sm2.messages_for_turn()
    assert msgs[-1].text_parts() == "第一答"


def test_fork_copies_history(tmp_path):
    sm = SessionManager.create(tmp_path / "ws", home=tmp_path)
    sm.append_user("Q")
    sm.append_event("assistant", _assistant_text("A"))
    fk = SessionManager.fork(sm.session_id, tmp_path / "ws", home=tmp_path)
    assert fk.session_id != sm.session_id
    msgs = fk.messages_for_turn()
    assert any(m.text_parts() == "A" for m in msgs)
