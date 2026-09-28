"""CC-Fingerprint 伪装层测试（零 token：MockTransport 捕 headers/body 断言）。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from loadn.providers import fingerprint
from loadn.providers.anthropic import AnthropicProvider
from loadn.types import Message, TextBlock

SSE_HEADERS = {"content-type": "text/event-stream"}


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _ok_stream():
    return (sse("message_start", {"message": {"id": "m",
                                              "usage": {"input_tokens": 3}}})
            + sse("message_stop", {"type": "message_stop"}))


def make_provider(cfg, handler):
    captured: dict = {"reqs": []}

    def wrap(request):
        captured["reqs"].append({
            "path": str(request.url),
            "headers": dict(request.headers),
            "body": json.loads(request.read()),
        })
        return handler(request)

    prov = AnthropicProvider(cfg, transport=httpx.MockTransport(wrap))
    return prov, captured


def glm_cfg(**over):
    cfg = {"provider": "anthropic", "base_url": "https://open.bigmodel.cn/api/anthropic",
           "api_key": "sk-glm", "model": "glm-5.3", "extra": {}}
    cfg.update(over)
    return cfg


async def _one_chat(prov):
    async for _ in prov.chat([Message(role="user", content=[TextBlock(text="hi")])],
                             [], "测试 system"):
        pass


# ---------------------------------------------------------------- 开关与门控
def test_stealth_mode_env(monkeypatch):
    monkeypatch.delenv("LOADN_STEALTH", raising=False)
    assert fingerprint.stealth_mode() == ""
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    assert fingerprint.stealth_mode() == "cc"
    monkeypatch.setenv("LOADN_STEALTH", "cc-all")
    assert fingerprint.stealth_mode() == "cc-all"
    monkeypatch.setenv("LOADN_STEALTH", "bogus")
    assert fingerprint.stealth_mode() == ""


def test_active_glm_gating():
    assert fingerprint.active("https://open.bigmodel.cn/api/anthropic") is False  # 需开关
    import os
    os.environ["LOADN_STEALTH"] = "cc"
    try:
        assert fingerprint.active("https://open.bigmodel.cn/api/anthropic") is True
        assert fingerprint.active("https://api.anthropic.com") is False   # 非 GLM 不开
        os.environ["LOADN_STEALTH"] = "cc-all"
        assert fingerprint.active("https://api.anthropic.com") is True   # 测试档全开
    finally:
        del os.environ["LOADN_STEALTH"]


# ---------------------------------------------------------------- headers
async def test_cc_headers_full_family(monkeypatch, tmp_path):
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    prov, cap = make_provider(glm_cfg(), lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=_ok_stream()))
    await _one_chat(prov)
    h = cap["reqs"][0]["headers"]
    assert h["user-agent"] == fingerprint.CC_PROFILE.user_agent
    assert "(external, sdk-ts, agent-sdk/" in h["user-agent"]
    for k in ("x-stainless-lang", "x-stainless-package-version", "x-stainless-os",
              "x-stainless-arch", "x-stainless-runtime", "x-stainless-runtime-version",
              "x-stainless-retry-count", "anthropic-beta", "x-app"):
        assert k in h, f"缺 {k}"
    assert h["x-stainless-lang"] == "js"
    assert h["authorization"] == "Bearer sk-glm"
    assert "x-api-key" not in h          # CC AUTH_TOKEN 形态只发 Bearer
    assert h["anthropic-beta"] == fingerprint.CC_PROFILE.BETA_FLAGS
    assert h["anthropic-beta"].count(",") == 5   # 6 个 beta 位（真机形状）


async def test_retry_count_increments(monkeypatch, tmp_path):
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    state = {"n": 0}

    def handler(r):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, headers=SSE_HEADERS, content=_ok_stream())

    prov, cap = make_provider(glm_cfg(), handler)
    await _one_chat(prov)
    assert len(cap["reqs"]) == 2
    assert cap["reqs"][0]["headers"]["x-stainless-retry-count"] == "0"
    assert cap["reqs"][1]["headers"]["x-stainless-retry-count"] == "1"


async def test_off_state_no_injection(monkeypatch, tmp_path):
    """关闭伪装（默认）：零注入回归保护——无 stainless、双鉴权头照旧。"""
    monkeypatch.delenv("LOADN_STEALTH", raising=False)
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    prov, cap = make_provider(glm_cfg(), lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=_ok_stream()))
    await _one_chat(prov)
    h = cap["reqs"][0]["headers"]
    assert not any(k.startswith("x-stainless") for k in h)
    assert "user-agent" not in h or not h["user-agent"].startswith("claude-cli/")
    assert h.get("x-api-key") == "sk-glm"
    assert "metadata" not in cap["reqs"][0]["body"]


# ---------------------------------------------------------------- 请求体
async def test_cc_request_body_habits(monkeypatch, tmp_path):
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    prov, cap = make_provider(glm_cfg(extra={"temperature": 0.7}),
                              lambda r: httpx.Response(
                                  200, headers=SSE_HEADERS, content=_ok_stream()))
    prov._stealth_sid = "sess-abc"
    await _one_chat(prov)
    b = cap["reqs"][0]["body"]
    assert b["stream"] is True                    # stream 恒真
    assert "temperature" not in b                 # CC 默认不带
    # metadata.user_id 真机形状：JSON 串 {device_id, account_uuid, session_id}
    uid = json.loads(b["metadata"]["user_id"])
    assert len(uid["device_id"]) == 64
    assert uid["account_uuid"] == ""
    assert uid["session_id"] == "sess-abc"
    assert b["max_tokens"] == 32000               # 真机实测档位
    assert b["thinking"] == {"type": "adaptive"}  # 真机 thinking 形态
    assert b["output_config"] == {"effort": "high"}
    assert b["context_management"]["edits"][0]["type"] == "clear_thinking_20251015"
    # system 三块：billing 头（无缓存标记）+ Agent SDK 身份句 + 主 prompt
    sysv = b["system"]
    assert isinstance(sysv, list) and len(sysv) == 3
    assert sysv[0]["text"].startswith("x-anthropic-billing-header: cc_version=2.1.183")
    assert "cache_control" not in sysv[0]
    assert sysv[1]["text"] == fingerprint.CC_IDENTITY_PREFIX
    assert sysv[1]["cache_control"] == {"type": "ephemeral"}
    assert sysv[2]["text"] == "测试 system"
    assert sysv[2]["cache_control"] == {"type": "ephemeral"}
    assert cap["reqs"][0]["path"].endswith("/v1/messages?beta=true")


async def test_identity_stable_across_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    prov, cap = make_provider(glm_cfg(), lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=_ok_stream()))
    await _one_chat(prov)
    await _one_chat(prov)
    d1 = json.loads(cap["reqs"][0]["body"]["metadata"]["user_id"])["device_id"]
    d2 = json.loads(cap["reqs"][1]["body"]["metadata"]["user_id"])["device_id"]
    assert d1 == d2 and len(d1) == 64              # 落盘复用，不随机漂移
    assert (tmp_path / "stealth_identity.json").exists()


# ---------------------------------------------------------------- system 泄漏清扫
def test_stealth_system_scrub(monkeypatch):
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    from loadn.core.context import CORE_PROMPT, _stealth_system
    out = _stealth_system(CORE_PROMPT)
    assert "loadn" not in out.lower()
    assert not out.startswith("你是 loadn")     # 身份句已剥
    monkeypatch.delenv("LOADN_STEALTH")
    assert _stealth_system(CORE_PROMPT) == CORE_PROMPT.strip()   # 关闭=原样


async def test_no_identity_leak_in_body(monkeypatch, tmp_path):
    """启用伪装后的完整请求体序列化串中不得出现自曝身份字样。"""
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    prov, cap = make_provider(glm_cfg(), lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=_ok_stream()))
    await _one_chat(prov)
    blob = json.dumps(cap["reqs"][0]["body"], ensure_ascii=False).lower()
    for leak in ("loadn", "httpx"):
        assert leak not in blob, f"身份泄漏: {leak}"


# ---------------------------------------------------------------- 工具面整形
async def test_tool_surface_shape(monkeypatch, tmp_path):
    """伪装通道：藏 InteractiveShell + 注册 CC stub（BashOutput/KillShell 真功能）。"""
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    from loadn.core.build import build_agent
    from loadn.supervisor.process import ProcessSupervisor
    from loadn.tools.base import ToolContext

    bundle = await build_agent(tmp_path, cfg={"provider": "fake"})
    tools = bundle.core.tools
    assert "InteractiveShell" not in tools        # loadn 特有工具隐藏
    for name in ("AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
                 "BashOutput", "KillShell"):
        assert name in tools, f"缺 CC stub {name}"
    # stub 真功能：BashOutput/KillShell 接 supervisor
    sup = ProcessSupervisor()
    try:
        info = await sup.spawn_bg(["bash", "-c", "echo bg-ok"], cwd=tmp_path)
        ctx = ToolContext(cwd=tmp_path, supervisor=sup)
        out = await tools["BashOutput"].execute({"task_id": info.id}, ctx)
        assert "bg-ok" in out or info.id in out
        out2 = await tools["KillShell"].execute({"task_id": info.id}, ctx)
        assert "已终止" in out2
    finally:
        await sup.shutdown()
    # 关闭伪装反向验证
    monkeypatch.setenv("LOADN_STEALTH", "off")
    bundle2 = await build_agent(tmp_path, cfg={"provider": "fake"})
    assert "InteractiveShell" in bundle2.core.tools
    assert "AskUserQuestion" not in bundle2.core.tools


# ---------------------------------------------------------------- GLM 通道关闭时
async def test_non_glm_channel_untouched(monkeypatch, tmp_path):
    """stealth=cc 但 base_url 非 GLM：行为与非伪装完全一致。"""
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    cfg = glm_cfg(base_url="https://api.example.test")
    prov, cap = make_provider(cfg, lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=_ok_stream()))
    await _one_chat(prov)
    h = cap["reqs"][0]["headers"]
    assert not any(k.startswith("x-stainless") for k in h)
    assert h.get("x-api-key") == "sk-glm"
    assert "metadata" not in cap["reqs"][0]["body"]


async def test_aux_request_metadata_shape(monkeypatch, tmp_path):
    """辅助请求（无 sid）：session_id 用落盘的固定 aux id（恒定）。"""
    monkeypatch.setenv("LOADN_STEALTH", "cc")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    prov, cap = make_provider(glm_cfg(), lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=_ok_stream()))
    await _one_chat(prov)
    uid = json.loads(cap["reqs"][0]["body"]["metadata"]["user_id"])
    ident = fingerprint.identity(tmp_path)
    assert uid["session_id"] == ident["aux_session_id"]
