"""stream_event 合成与接线（零 token：直接喂 Chunk / ScriptedProvider 驱动）。"""
from __future__ import annotations

import random

import tests.helpers as H
from loadn.core.loop import AgentCore, LoopSettings
from loadn.core.session import SessionManager
from loadn.core.streamsynth import StreamEventSynthesizer
from loadn.providers import Chunk


def _feed_all(synth: StreamEventSynthesizer, chunks: list[Chunk]) -> list[dict]:
    out: list[dict] = []
    for c in chunks:
        out.extend(synth.feed(c))
    return out


def _kinds(evs: list[dict]) -> list[str]:
    return [e["type"] for e in evs]


def test_text_round_event_sequence():
    synth = StreamEventSynthesizer(model="fake")
    evs = _feed_all(synth, H.text_round("你好，世界"))
    assert _kinds(evs) == ["message_start", "content_block_start",
                           "content_block_delta", "content_block_stop",
                           "message_delta", "message_stop"]
    # delta 文本 == 最终文本（拼接守恒）
    deltas = [e for e in evs if e["type"] == "content_block_delta"]
    assert "".join(d["delta"]["text"] for d in deltas) == "你好，世界"
    # message_start 骨架
    ms = evs[0]["message"]
    assert ms["role"] == "assistant" and ms["model"] == "fake" and ms["id"]
    # message_delta 带 stop_reason 与 usage
    md = next(e for e in evs if e["type"] == "message_delta")
    assert md["delta"]["stop_reason"] == "end_turn"
    assert md["usage"]["output_tokens"] == 20


def test_tool_round_single_block():
    synth = StreamEventSynthesizer(model="fake")
    evs = _feed_all(synth, H.tool_round("tu_9", "Echo", {"msg": "hi"}))
    kinds = _kinds(evs)
    # usage 首 → message_start；两片 json 增量同 index 同块
    assert kinds[0] == "message_start"
    cbs = [e for e in evs if e["type"] == "content_block_start"]
    deltas = [e for e in evs if e["type"] == "content_block_delta"]
    assert len(cbs) == 1
    assert cbs[0]["content_block"]["type"] == "tool_use"
    assert cbs[0]["content_block"]["id"] == "tu_9"
    assert cbs[0]["content_block"]["name"] == "Echo"
    assert len(deltas) == 2
    assert {d["index"] for d in deltas} == {cbs[0]["index"]}
    import json as _j
    assert _j.loads("".join(d["delta"]["partial_json"] for d in deltas)) == {"msg": "hi"}
    # 收尾：stop_reason=tool_use
    md = next(e for e in evs if e["type"] == "message_delta")
    assert md["delta"]["stop_reason"] == "tool_use"


def test_mixed_blocks_indices_paired():
    """thinking → text → tool 三块：index 0/1/2 连续、starts==stops。"""
    synth = StreamEventSynthesizer(model="fake")
    chunks = [Chunk(kind="thinking_delta", text="想想"),
              Chunk(kind="text_delta", text="结论"),
              Chunk(kind="input_json_delta", tool_use_id="tu_1",
                    tool_name="Echo", partial_json='{"m'),
              Chunk(kind="input_json_delta", tool_use_id="tu_1",
                    partial_json='sg":"x"}'),
              Chunk(kind="stop", usage={"output_tokens": 9},
                    stop_reason="tool_use")]
    evs = _feed_all(synth, chunks)
    starts = [e["index"] for e in evs if e["type"] == "content_block_start"]
    stops = [e["index"] for e in evs if e["type"] == "content_block_stop"]
    assert starts == [0, 1, 2] and stops == [0, 1, 2]
    # 每个增量之前必有对应开块（index 均在 starts 里）
    assert all(e["index"] in starts
               for e in evs if e["type"] == "content_block_delta")
    assert _kinds(evs)[-2:] == ["message_delta", "message_stop"]


def test_message_id_reuse_and_reset():
    synth = StreamEventSynthesizer(model="fake")
    chunks = [Chunk(kind="usage", usage={"input_tokens": 5},
                    model="m1", message_id="msg_real_1")]
    evs = _feed_all(synth, chunks)
    assert evs[0]["message"]["id"] == "msg_real_1"
    assert synth.message_id == "msg_real_1"
    synth.reset()
    evs2 = _feed_all(synth, [Chunk(kind="text_delta", text="x"),
                             Chunk(kind="stop", usage={}, stop_reason="end_turn")])
    assert evs2[0]["message"]["id"] != "msg_real_1"   # reset 换新 id


def test_error_chunk_no_events():
    synth = StreamEventSynthesizer(model="fake")
    assert _feed_all(synth, [Chunk(kind="error", error="boom")]) == []


def test_invariants_random_sequences():
    """随机 Chunk 序列的不变量：index 连续、starts==stops、拼接守恒。"""
    rng = random.Random(42)
    for _ in range(20):
        synth = StreamEventSynthesizer(model="fake")
        asm_chunks: list[Chunk] = []
        text_ref = ""
        tool_json: dict[str, str] = {}
        think_ref = ""
        for _ in range(rng.randint(1, 12)):
            pick = rng.choice(["text", "think", "tool", "tool", "usage"])
            if pick == "text":
                t = f"t{rng.randint(0, 99)}"
                asm_chunks.append(Chunk(kind="text_delta", text=t))
                text_ref += t
            elif pick == "think":
                t = f"h{rng.randint(0, 99)}"
                asm_chunks.append(Chunk(kind="thinking_delta", text=t))
                think_ref += t
            elif pick == "tool":
                tid = f"tu_{rng.randint(1, 3)}"
                first = tid not in tool_json
                pj = f'p{rng.randint(0, 9)}'
                asm_chunks.append(Chunk(
                    kind="input_json_delta", tool_use_id=tid,
                    tool_name=("T" + tid) if first else "", partial_json=pj))
                tool_json[tid] = tool_json.get(tid, "") + pj
            else:
                asm_chunks.append(Chunk(kind="usage",
                                        usage={"input_tokens": rng.randint(1, 9)}))
        asm_chunks.append(Chunk(kind="stop", usage={"output_tokens": 1},
                                stop_reason="end_turn"))
        evs = _feed_all(synth, asm_chunks)
        starts = [e["index"] for e in evs if e["type"] == "content_block_start"]
        stops = [e["index"] for e in evs if e["type"] == "content_block_stop"]
        assert starts == sorted(set(starts)) and starts == stops
        assert starts == list(range(len(starts)))   # 从 0 连续
        got_text = "".join(
            e["delta"]["text"] for e in evs
            if e["type"] == "content_block_delta"
            and e["delta"]["type"] == "text_delta")
        got_think = "".join(
            e["delta"]["thinking"] for e in evs
            if e["type"] == "content_block_delta"
            and e["delta"]["type"] == "thinking_delta")
        assert got_text == text_ref and got_think == think_ref
        # partial_json 按 index→tool_use_id（content_block_start 对齐）拼接守恒
        idx_tool = {e["index"]: e["content_block"].get("id") or ""
                    for e in evs if e["type"] == "content_block_start"
                    and e["content_block"]["type"] == "tool_use"}
        got_tools: dict[str, str] = {}
        for e in evs:
            if e["type"] == "content_block_delta" \
                    and e["delta"]["type"] == "input_json_delta":
                tid = idx_tool.get(e["index"], "")
                got_tools[tid] = got_tools.get(tid, "") + e["delta"]["partial_json"]
        assert got_tools == tool_json


async def test_loop_emits_stream_events(tmp_path):
    core = AgentCore(
        provider=H.ScriptedProvider([H.text_round("流式回复")]),
        tools={"Echo": H.EchoTool()}, session=SessionManager.create(tmp_path),
        cwd=tmp_path, settings=LoopSettings(max_turns=5))
    events = []
    await core.run_turn("看增量", emit=events.append, stream_events=True)
    kinds = [e["type"] for e in events]
    # stream_event* 全部在 assistant 之前；assistant 后只有 turn
    first_assistant = kinds.index("assistant")
    assert all(k == "stream_event" for k in kinds[:first_assistant])
    assert kinds[first_assistant:] == ["assistant", "turn"]
    assert any(e["event"]["type"] == "message_start" for e in events
               if e["type"] == "stream_event")
    # message_id 贯通：stream_event 的 message id == assistant 事件 id（fake 无 id 时跳过）


async def test_loop_default_no_stream_events(tmp_path):
    """默认（stream_events=False）零 stream_event——保护既有 emit 消费方。"""
    core = AgentCore(
        provider=H.ScriptedProvider([H.text_round("普通回复")]),
        tools={"Echo": H.EchoTool()}, session=SessionManager.create(tmp_path),
        cwd=tmp_path, settings=LoopSettings(max_turns=5))
    events = []
    await core.run_turn("默认", emit=events.append)
    assert [e["type"] for e in events] == ["assistant", "turn"]


async def test_chunk_assembler_arrival_order(tmp_path):
    """text 先于 tool 到达时 blocks() 按到达序输出（与 stream_event 流对齐）。"""
    core = AgentCore(
        provider=H.ScriptedProvider([[
            Chunk(kind="text_delta", text="先说明"),
            Chunk(kind="input_json_delta", tool_use_id="tu_1", tool_name="Echo",
                  partial_json='{"msg":"x"}'),
            Chunk(kind="stop", usage={"output_tokens": 3}, stop_reason="tool_use"),
        ], H.text_round("收尾")]),
        tools={"Echo": H.EchoTool()}, session=SessionManager.create(tmp_path),
        cwd=tmp_path, settings=LoopSettings(max_turns=5))
    events = []
    await core.run_turn("交错轮", emit=events.append)
    a = next(e for e in events if e["type"] == "assistant")
    types = [b.to_dict()["type"] for b in a["message"].content]
    assert types == ["text", "tool_use"]   # 到达序，而非旧固定序 [tool_use, text]
