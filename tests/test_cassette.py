"""P1-3 VCR cassettes 验收（全程零真网络：record 用 MockTransport 当「真网」）。

- matching：canonical 快照（键排序/波动头忽略/敏感头脱敏后稳定）；
  同形重复请求按序消费；body 键序无关命中
- 录制：真实（mock）请求落盘，敏感头/体键 <redacted>；已有磁带拒覆盖
- 回放：未命中明确失败（零网络兜底）；重放得到录制的响应形状
- 磁带文件可跨进程重放（jsonl→json roundtrip）
"""
from __future__ import annotations

import json

import httpx
import pytest

from loadn.providers.cassette import (
    CassetteTransport,
    canonicalize,
    redact_body,
    redact_headers,
    snapshot,
)


def _fake_upstream(calls: list) -> httpx.AsyncBaseTransport:
    """「真网络」替身：固定 JSON 响应 + 记录请求。"""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "echo": request.url.path},
                              headers={"X-Request-Id": "differs-each-time"})
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------- 纯函数
def test_canonicalize_sorts_keys_recursively():
    assert canonicalize({"b": {"z": 1, "a": 2}, "a": [3, 1]}) == \
        {"a": [3, 1], "b": {"a": 2, "z": 1}}       # 数组保序


def test_redaction():
    h = redact_headers({"Authorization": "Bearer SK", "Content-Type": "application/json",
                        "X-Api-Key": "k"})
    assert h["Authorization"] == "<redacted>" and h["X-Api-Key"] == "<redacted>"
    assert h["Content-Type"] == "application/json"
    b = redact_body(json.dumps({"model": "m", "api_key": "SK", "nested": {"ok": 1}}))
    d = json.loads(b)
    assert d["api_key"] == "<redacted>" and d["model"] == "m"


def test_snapshot_ignores_volatile_and_redacts_sensitive():
    a = snapshot("POST", "https://x.example/v1",
                 {"x-request-id": "r1", "Authorization": "Bearer SK",
                  "content-type": "application/json"}, '{"b":1,"a":2}')
    b = snapshot("POST", "https://x.example/v1",
                 {"X-Request-Id": "r99999", "authorization": "Bearer OTHER",
                  "Content-Type": "application/json"}, '{"a":2,"b":1}')
    assert a == b                    # 波动头忽略 + 敏感头脱敏后稳定 + 键序无关


# ---------------------------------------------------------------- 录制
async def test_record_then_replay_roundtrip(tmp_path):
    calls: list = []
    transport = _fake_upstream(calls)
    rec = CassetteTransport("anthropic-demo", tmp_path, "record", real=transport)
    client = httpx.AsyncClient(transport=rec)
    r = await client.post("https://api.example/v1/messages",
                          json={"model": "m", "messages": []},
                          headers={"Authorization": "Bearer SECRET"})
    assert r.status_code == 200
    await client.aclose()
    # 磁带落盘：敏感头脱敏、形状完整
    data = json.loads((tmp_path / "anthropic-demo.json").read_text())
    assert data["version"] == 1 and len(data["interactions"]) == 1
    it = data["interactions"][0]
    assert it["headers"]["authorization"] == "<redacted>"
    assert "SECRET" not in json.dumps(data)
    assert it["status"] == 200 and "echo" in it["response_body"]
    # 回放（新进程语义：全新 transport 从盘加载）：拿到录制响应形状
    rep = CassetteTransport("anthropic-demo", tmp_path, "replay")
    client2 = httpx.AsyncClient(transport=rep)
    r2 = await client2.post("https://api.example/v1/messages",
                            json={"messages": [], "model": "m"},   # 键序不同
                            headers={"Authorization": "Bearer DIFFERENT",
                                     "X-Request-Id": "zz"})
    assert r2.status_code == 200
    assert r2.json()["echo"] == "/v1/messages"
    await client2.aclose()


async def test_record_refuses_overwrite(tmp_path, monkeypatch):
    (tmp_path / "exists.json").write_text('{"version":1,"interactions":[]}')
    monkeypatch.setenv("LOADN_CASSETTE_DIR", str(tmp_path))
    monkeypatch.setenv("LOADN_CASSETTE_MODE", "record")
    from loadn.providers.cassette import maybe_cassette_client
    assert maybe_cassette_client("exists") is None      # 拒覆盖 → 退化真网络


# ---------------------------------------------------------------- 回放纪律
async def test_replay_miss_fails_hard(tmp_path):
    rep = CassetteTransport("miss", tmp_path, "replay")
    (tmp_path / "miss.json").write_text(json.dumps(
        {"version": 1, "interactions": [
            {"method": "POST", "url": "https://api.example/a",
             "headers": {}, "body": None, "status": 200,
             "response_headers": {}, "response_body": "{}"}]}))
    client = httpx.AsyncClient(transport=rep)
    with pytest.raises(RuntimeError, match="零 token"):
        await client.post("https://api.example/UNMATCHED", json={})
    await client.aclose()


async def test_repeated_identical_requests_consume_in_order(tmp_path):
    """同形重复请求按序消费（selectSequential 同构）。"""
    (tmp_path / "seq.json").write_text(json.dumps(
        {"version": 1, "interactions": [
            {"method": "POST", "url": "https://x/a", "headers": {},
             "body": None, "status": 200, "response_headers": {},
             "response_body": '"first"'},
            {"method": "POST", "url": "https://x/a", "headers": {},
             "body": None, "status": 200, "response_headers": {},
             "response_body": '"second"'}]}))
    rep = CassetteTransport("seq", tmp_path, "replay")
    client = httpx.AsyncClient(transport=rep)
    assert (await client.post("https://x/a")).text == '"first"'
    assert (await client.post("https://x/a")).text == '"second"'
    await client.aclose()
