"""M6c：providers/cassette 存活变异对赌。

anthropic 75%/openai_compat 71% 首轮——T 批投资见效，盲区集中在守卫门：
模型缺省回退、400 取证门、SSE 超长行防御、错误消息优先级、tool_result
文本抽取、cassette 模式门（磁带是真实采样工件，拒覆盖语义）。
"""
from __future__ import annotations

import json

import httpx
import pytest

from loadn.providers.anthropic import AnthropicProvider, _iter_sse
from loadn.providers.cassette import _REDACTED, maybe_cassette_client, snapshot
from loadn.providers.openai_compat import OpenAICompatProvider, _result_text


# ---------------------------------------------------------------- 模型缺省
def test_model_defaults_to_glm():
    cfg = {"provider": "anthropic", "base_url": "http://m.test",
           "api_key": "k"}
    assert AnthropicProvider(cfg).model_name == "glm-5.3"
    ocfg = {"provider": "openai", "base_url": "http://m.test/v1",
            "api_key": "k"}
    assert OpenAICompatProvider(ocfg).model_name == "glm-5.3"


# ---------------------------------------------------------------- 400 取证门
async def test_400_dumps_body_500_does_not(tmp_path, monkeypatch):
    """400（provider 协议错）落 dump 取证；500 不落（服务侧错非我方问题）。"""
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    import shutil
    for status, dumped in ((400, True), (500, False)):
        shutil.rmtree(tmp_path / "debug", ignore_errors=True)   # 上一轮残留清场
        def handler(request, status=status):
            return httpx.Response(status, json={"error": {"message": "x"}})

        prov = AnthropicProvider(
            {"provider": "anthropic", "base_url": "http://m.test",
             "api_key": "k"},
            transport=httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await prov._nonstream_once([], [], "sys")
        debug_files = list((tmp_path / "debug").glob("*"))
        assert bool(debug_files) is dumped


# ---------------------------------------------------------------- SSE 行防御
class _Resp:
    """_iter_sse 鸭子桩：按块吐字节。"""

    def __init__(self, chunks):
        self.chunks = chunks

    async def aiter_bytes(self):
        for c in self.chunks:
            yield c


async def test_iter_sse_frames_and_overlong_guard():
    from loadn.constants import STREAM_LINE_MAX
    ev = [
        (e, d) async for e, d in _iter_sse(
            _Resp([b"event: message\ndata: ", b'{"x":1}\n\n',
                   b"event: done\ndata: [DONE]\n"]))
    ]
    assert ev == [("message", '{"x":1}'), ("done", "[DONE]")]

    with pytest.raises(httpx.ReadError):
        async for _ in _iter_sse(
                _Resp([b"data: " + b"a" * (STREAM_LINE_MAX + 1)])):
            pass
    # 无换行但未超限：安全等待后续分片（拼接后再成帧）
    ev2 = [(e, d) async for e, d in _iter_sse(
        _Resp([b"event: m\ndata: ", b"1\n"]))]
    assert ev2 == [("m", "1")]


# ---------------------------------------------------------------- 错误消息优先
def _mk_resp(status=429, content=b""):
    req = httpx.Request("POST", "http://m.test/x")
    return httpx.Response(status, content=content, request=req)


def test_status_error_prefers_parsed_message():
    from loadn.providers.anthropic import _status_error as a_err
    e = a_err(_mk_resp(), '{"error":{"message":"配额已尽"}}'.encode())
    assert "配额已尽" in str(e) and '{"error"' not in str(e)  # 取解析值非原文
    from loadn.providers.openai_compat import _status_error as o_err
    e2 = o_err(_mk_resp(), '{"error":{"message":"上游超时"}}'.encode())
    assert "上游超时" in str(e2) and '{"error"' not in str(e2)
    # 非 JSON 体 → 原文回显
    e3 = a_err(_mk_resp(), b"gateway exploded")
    assert "gateway exploded" in str(e3)


# ---------------------------------------------------------------- result 文本
def test_result_text_block_extraction():
    assert _result_text(None) == ""
    assert _result_text("纯文本") == "纯文本"
    assert _result_text([
        {"type": "text", "text": "第一段"},
        {"type": "image", "source": {}},
        {"type": None},
        42,
    ]) == "第一段\n[image]\n[block]\n42"     # 非 text 块占位不丢内容


async def test_oai_iter_sse_overlong_guard():
    from loadn.constants import STREAM_LINE_MAX
    from loadn.providers.openai_compat import _iter_sse as o_iter
    with pytest.raises(httpx.ReadError):
        async for _ in o_iter(
                _Resp([b"data: " + b"b" * (STREAM_LINE_MAX + 1)])):
            pass
    ev = [(e, d) async for e, d in o_iter(
        _Resp([b"data: ", b'{"ok":1}\n\n']))]
    assert ev == [("", '{"ok":1}')]


# ---------------------------------------------------------------- cassette 门
def test_snapshot_canonical_body():
    a = snapshot("post", "http://u/x", {"Authorization": "Bearer t"},
                 '{"b": 2, "a": 1}')
    b = snapshot("POST", "http://u/x", {"authorization": "Bearer t"},
                 '{"a":1,"b":2}')            # 方法/头归一 + body canonical
    assert a == b
    d = json.loads(a)
    assert d["body"] == {"a": 1, "b": 2}
    assert d["headers"]["authorization"] == _REDACTED   # 敏感头脱敏


def test_cassette_client_mode_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_CASSETTE_DIR", str(tmp_path))
    monkeypatch.setenv("LOADN_CASSETTE_MODE", "record")
    (tmp_path / "t1.json").write_text("{}", encoding="utf-8")
    assert maybe_cassette_client("t1") is None       # record 且磁带已存在 → 拒覆盖
    assert maybe_cassette_client("t2") is not None   # record 新名 → 可录
    monkeypatch.setenv("LOADN_CASSETTE_MODE", "replay")
    assert maybe_cassette_client("t1") is not None   # replay 存在 → 挂磁带
    monkeypatch.setenv("LOADN_CASSETTE_DIR", "")
    assert maybe_cassette_client("t1") is None       # 未开启 → 真网络


async def test_oai_embedded_error_frame_message():
    """200 流内嵌 error 帧：取解析出的 message（or→and=回显整帧原文）。"""
    def handler(_request):
        return httpx.Response(
            200, text='data: {"error":{"code":"quota","message":"配额已尽"}}\n\n',
            headers={"content-type": "text/event-stream"})

    prov = OpenAICompatProvider(
        {"provider": "openai", "base_url": "http://m.test/v1",
         "api_key": "k", "model": "glm-5.3"},
        transport=httpx.MockTransport(handler))
    chunks = [c async for c in prov.chat([], [], "s")]
    err = next(c for c in chunks if c.kind == "error")
    assert err.retriable is False                       # 网关内嵌错不可重试
    assert "配额已尽" in err.error and '{"error"' not in err.error
