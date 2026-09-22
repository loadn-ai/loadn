"""FakeProvider 单测：script 模式精确回放 + .fake 控制文件全分支（零 token）。"""
from __future__ import annotations

import asyncio
import json

import pytest

from loadn.providers import Chunk
from loadn.providers.fake import FakeProvider
from loadn.types import Message, TextBlock, ToolResultBlock

USER = lambda text="": Message(role="user", content=[TextBlock(text=text)])  # noqa: E731
TOOL_RESULT = lambda tid: Message(role="user", content=[  # noqa: E731
    ToolResultBlock(tool_use_id=tid, content="done", is_error=False)])


async def collect(provider, messages):
    return [c async for c in provider.chat(messages, [], "你是测试助手")]


# ---------------------------------------------------------------- script 模式
async def test_script_mode_rounds_and_autostop():
    p = FakeProvider(script=[
        [Chunk(kind="text_delta", text="第一轮")],
        [Chunk(kind="input_json_delta", tool_use_id="tu_1", tool_name="Bash",
               partial_json='{"command": "ls"}'),
         Chunk(kind="stop", stop_reason="tool_use")],
    ])
    r1 = await collect(p, [USER("hi")])
    assert [(c.kind, c.text) for c in r1[:2]] == [("text_delta", "第一轮"),
                                                  ("stop", "")]  # 自动补 stop
    assert r1[1].usage == {"input_tokens": 1000, "output_tokens": 200,
                           "cache_read_input_tokens": 10000,
                           "cache_creation_input_tokens": 0}
    r2 = await collect(p, [TOOL_RESULT("tu_1")])
    assert r2[-1].kind == "stop" and r2[-1].stop_reason == "tool_use"
    assert len(r2) == 2                            # 显式 stop 不重复补
    r3 = await collect(p, [USER("again")])
    assert r3[0].text == "(fake done)"             # script 弹空回落
    assert r3[-1].kind == "stop"


async def test_model_name():
    assert FakeProvider().model_name == "fake"


# ---------------------------------------------------------------- 控制文件模式
@pytest.fixture()
def fake_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_FAKE_DIR", str(tmp_path / ".fake"))
    d = tmp_path / ".fake"
    d.mkdir()
    return d


async def test_reply_text_round(fake_dir):
    (fake_dir / "reply").write_text("这是自定义回复")
    chunks = await collect(FakeProvider(), [USER("问题")])
    assert [(c.kind, c.text) for c in chunks if c.kind == "text_delta"] == \
        [("text_delta", "这是自定义回复")]
    assert chunks[-1].kind == "stop" and chunks[-1].stop_reason == "end_turn"
    usage_wave = chunks[0]
    assert usage_wave.kind == "usage"
    assert usage_wave.usage == {"input_tokens": 1000,
                                "cache_read_input_tokens": 10000,
                                "cache_creation_input_tokens": 0}


async def test_default_echo_reply(fake_dir):
    chunks = await collect(FakeProvider(), [USER("打开报告")])
    text = [c for c in chunks if c.kind == "text_delta"][0].text
    assert text == "收到：打开报告（fake）"


async def test_tools_two_rounds(fake_dir):
    (fake_dir / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "echo hi"}}))
    p = FakeProvider()
    r1 = await collect(p, [USER("跑一下")])
    deltas = [c for c in r1 if c.kind == "input_json_delta"]
    assert len(deltas) == 1
    assert deltas[0].tool_name == "Bash" and deltas[0].tool_use_id == "fake_tool_1"
    assert json.loads(deltas[0].partial_json) == {"command": "echo hi"}
    assert r1[-1].stop_reason == "tool_use"

    r2 = await collect(p, [TOOL_RESULT("fake_tool_1")])   # 吃到 tool_result
    assert [c.kind for c in r2] == ["usage", "text_delta", "stop"]
    assert r2[-1].stop_reason == "end_turn"


async def test_tools_multiple_calls_and_broken_file(fake_dir):
    (fake_dir / "tools").write_text("not-json{{{")
    p = FakeProvider()
    r1 = await collect(p, [USER("跑")])
    deltas = [c for c in r1 if c.kind == "input_json_delta"]
    assert len(deltas) == 1                            # 损坏回落默认 Bash echo
    assert deltas[0].tool_name == "Bash"
    assert json.loads(deltas[0].partial_json) == {"command": "echo hello-fake"}


async def test_todos_round(fake_dir):
    (fake_dir / "todos").write_text("")
    p = FakeProvider()
    r1 = await collect(p, [USER("列计划")])
    deltas = [c for c in r1 if c.kind == "input_json_delta"]
    assert deltas[0].tool_name == "TodoWrite"
    assert deltas[0].partial_json.startswith('{"todos"')
    assert json.loads(deltas[0].partial_json)["todos"][0]["content"] == "检索来源"
    assert r1[-1].stop_reason == "tool_use"


async def test_fail_and_fastfail(fake_dir):
    (fake_dir / "fail").write_text("")
    chunks = await collect(FakeProvider(), [USER()])
    assert len(chunks) == 1 and chunks[0].kind == "error"
    assert chunks[0].retriable is False
    assert "fake internal failure" in chunks[0].error

    (fake_dir / "fail").unlink()
    (fake_dir / "fastfail").write_text("")
    chunks2 = await collect(FakeProvider(), [USER()])
    assert chunks2[0].kind == "error"
    # resume 秒拒文案与 claude CLI 一致：驱动有界的轮换终止链
    assert chunks2[0].error == "Session not found"


async def test_bigusage(fake_dir):
    (fake_dir / "bigusage").write_text("")
    chunks = await collect(FakeProvider(), [USER()])
    assert chunks[-1].usage["input_tokens"] == 600000
    assert chunks[-1].usage["cache_read_input_tokens"] == 500000


async def test_giantline(fake_dir):
    (fake_dir / "giantline").write_text("")
    chunks = await collect(FakeProvider(), [USER()])
    text = [c for c in chunks if c.kind == "text_delta"][0]
    assert len(text.text) == 200_000


async def test_hang_sleeps_300(fake_dir, monkeypatch):
    (fake_dir / "hang").write_text("")
    slept: list[float] = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    chunks = await collect(FakeProvider(), [USER()])
    assert slept == [300]
    assert chunks[-1].kind == "stop"


async def test_fake_model_env(fake_dir, monkeypatch):
    monkeypatch.setenv("LOADN_FAKE_MODEL", "fake-glm-5.3")
    chunks = await collect(FakeProvider(), [USER("hi")])
    assert chunks[0].model == "fake-glm-5.3"
    assert chunks[-1].model == "fake-glm-5.3"
    assert FakeProvider().model_name == "fake"    # model_name 恒为 fake
