"""stream_event 直播的顺序保证：interleaved-thinking 交错流不得乱序。

GLM 的 interleaved-thinking 让 thinking/text 块交错到达；两类 delta 缓冲
独立计时冲刷——修复前 thinking 尾巴会晚于 text 冲出，前端按序渲染导致
思考碎片插进正文中间（2026-09-17 实况反馈）。修复：类切换即块边界，
先冲净另一类缓冲。
"""
from __future__ import annotations

import asyncio

from loadn_webui.claude_runner import StopHandle
from loadn_webui.engine import ENGINE, ActiveTurn


def _delta(kind: str, payload: str) -> dict:
    """kind ∈ think|text → Anthropic 形 delta 事件（注意 thinking_delta 全拼）。"""
    key = "thinking" if kind == "think" else "text"
    dtype = "thinking_delta" if kind == "think" else "text_delta"
    return {"type": "stream_event",
            "event": {"type": "content_block_delta",
                      "delta": {"type": dtype, key: payload}}}


async def test_kind_switch_flushes_other_buffer_first():
    """thinking 缓冲未到阈值 → text 到达 → thinking 必须先于 text 冲出。"""
    sid, tid = "ord-test-sid", 99901
    q: asyncio.Queue = asyncio.Queue()
    ENGINE._subs[sid] = {q}
    at = ActiveTurn(turn_id=tid, session_id=sid, stop=StopHandle(),
                    started_at=asyncio.get_event_loop().time())
    try:
        # 块首片：计时初值 0 → 立即冲出
        await ENGINE._consume(sid, tid, at, _delta("think", "思考片段甲"))
        first = q.get_nowait()
        assert first[1] == "thinking" and first[2]["text"] == "思考片段甲"
        # thinking 尾巴（首片已冲过 → 计时重置，本片缓冲不冲）
        await ENGINE._consume(sid, tid, at, _delta("think", "尾巴"))
        assert q.empty(), "未到阈值不应冲刷"
        # text 到达 = 类切换 = 块边界 → 先冲 thinking 尾巴，text 首片再冲
        await ENGINE._consume(sid, tid, at, _delta("text", "正文开头"))
        think_tail = q.get_nowait()
        assert think_tail[1] == "thinking" and think_tail[2]["text"] == "尾巴"
        text_ev = q.get_nowait()
        assert text_ev[1] == "text" and text_ev[2]["text"] == "正文开头"
        # text 尾巴缓冲 → thinking 到达（interleave）→ 先冲 text 尾巴；
        # 新 think 块首片因同 kind 计时未到 0.5s 而合流缓冲（设计行为）
        await ENGINE._consume(sid, tid, at, _delta("text", "正文尾巴"))
        assert q.empty()
        await ENGINE._consume(sid, tid, at, _delta("think", "第二段思考"))
        text_tail = q.get_nowait()
        assert text_tail[1] == "text" and text_tail[2]["text"] == "正文尾巴"
        assert q.empty(), "新 think 块应合流缓冲（计时未到）"
        # 终局（assistant 整块到达）冲净残留 → 思考尾巴最后有序冲出
        await ENGINE._consume(sid, tid, at, {"type": "assistant", "message": {
            "role": "assistant", "content": []}})
        think2 = q.get_nowait()
        assert think2[1] == "thinking" and think2[2]["text"] == "第二段思考"
    finally:
        ENGINE._subs.pop(sid, None)


async def test_interleaved_sequence_total_order():
    """交错序列 think→text→think→text 的 SSE 事件序与到达序完全一致。"""
    sid, tid = "ord-test-sid2", 99902
    q: asyncio.Queue = asyncio.Queue()
    ENGINE._subs[sid] = {q}
    at = ActiveTurn(turn_id=tid, session_id=sid, stop=StopHandle(),
                    started_at=asyncio.get_event_loop().time())
    try:
        seq = [("think", "思考A"), ("think", "思考A续"),
               ("text", "正文1"), ("think", "思考B"),
               ("text", "正文2"), ("text", "正文2续")]
        for kind, s in seq:
            await ENGINE._consume(sid, tid, at, _delta(kind, s))
        # 终局事件冲净残留
        await ENGINE._consume(sid, tid, at, {"type": "assistant", "message": {
            "role": "assistant", "content": []}})
        got = []
        while not q.empty():
            _, typ, data = q.get_nowait()
            got.append((typ, data["text"]))
        # 事件序与到达序完全一致（interleave 不乱序）；同类合流把同块内
        # 分片并成一条（正文2 两片在终局合并冲出——顺序与内容都无损）
        assert got == [("thinking", "思考A"),
                       ("thinking", "思考A续"),
                       ("text", "正文1"),
                       ("thinking", "思考B"),
                       ("text", "正文2正文2续")], got
    finally:
        ENGINE._subs.pop(sid, None)
