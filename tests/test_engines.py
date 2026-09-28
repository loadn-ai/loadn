"""引擎层单测：三引擎 argv 快照 + OpencodeEventAdapter 事件归一化。

- argv 面：flag 组成、PROMPT=argv[-1] 契约、resume/fresh 的 session flag 差异、
  opencode 无 --max-turns 的降级 warning（进程级一次）
- 适配器面：fixtures（真实 opencode run --format json 的代表性事件序列）逐事件
  断言——text 累积不重复、tool_use/tool_result 配对、usage 四键映射、
  idle→finalize 合成 result、session.error→error result、乱序/未知事件不炸
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _call(**kw):
    """最小 TurnCall（engine 字段只是簿记，build_argv 由显式 spec 驱动）。"""
    from loadn_webui.claude_runner import TurnCall
    base = dict(prompt="PROMPT", cwd=Path("/tmp/ws"), session_id=str(uuid.uuid4()),
                resume=False, effort="high", model=None, engine="claude")
    base.update(kw)
    return TurnCall(**base)


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


# ---------------------------------------------------------------- argv 快照
def test_claude_argv_snapshot():
    from loadn_webui.engines import ENGINES
    spec = ENGINES["claude"]
    call = _call()
    cmd, env = spec.build_argv(call)
    assert cmd[0].endswith("fake_claude.py")          # conftest 的 WORKDADDY_CLAUDE_BIN
    assert cmd[1] == "-p" and "--verbose" in cmd       # stream-json 必需
    assert _flag_value(cmd, "--output-format") == "stream-json"
    assert "--dangerously-skip-permissions" in cmd
    assert _flag_value(cmd, "--permission-mode") == "bypassPermissions"
    assert _flag_value(cmd, "--effort") == "high"
    assert "--model" not in cmd                        # 无模型继承 CLI 默认
    assert "--max-turns" not in cmd
    # fresh：--session-id <uuid>
    assert _flag_value(cmd, "--session-id") == call.session_id
    assert cmd[-1] == "PROMPT"                         # PROMPT 末位契约
    assert env == {}
    # resume：--resume 同 id；非 UUID id（opencode 域串进来的脏值）换新 UUID
    cmd2, _ = spec.build_argv(_call(resume=True, session_id="ses_dirty"))
    assert "--resume" in cmd2 and "--session-id" not in cmd2
    assert str(uuid.UUID(_flag_value(cmd2, "--resume"))) == _flag_value(cmd2, "--resume")
    # max_turns 透传
    cmd3, _ = spec.build_argv(_call(max_turns=7))
    assert _flag_value(cmd3, "--max-turns") == "7"


def test_hahaness_argv_snapshot():
    from loadn_webui.engines import ENGINES
    spec = ENGINES["hahaness"]
    call = _call()
    cmd, env = spec.build_argv(call)
    assert cmd[0].endswith("/loadn") or cmd[:3] == [sys.executable, "-m", "loadn"]
    # R1 起：console_script/venv 解析，不再注入 PYTHONPATH hack
    assert "-p" in cmd and "--verbose" in cmd
    assert _flag_value(cmd, "--output-format") == "stream-json"
    assert _flag_value(cmd, "--session-id") == call.session_id
    assert cmd[-1] == "PROMPT"
    assert "--no-compact" not in cmd                        # 未设轮换 → 内压放行
    cmd2, _ = spec.build_argv(_call(resume=True, session_id=call.session_id,
                                    rotate_input_tokens=400_000))
    assert _flag_value(cmd2, "--resume") == call.session_id
    assert "--no-compact" in cmd2                           # 外层轮换已设 → 禁内压


def test_opencode_argv_flag_face(monkeypatch, tmp_path):
    from loadn_webui.engines import ENGINES
    fake_bin = str(tmp_path / "opencode")
    monkeypatch.setenv("WORKDADDY_OPENCODE_BIN", fake_bin)
    monkeypatch.setattr("loadn_webui.config.CONFIG.engines.opencode.bin", None)
    spec = ENGINES["opencode"]
    assert spec.resolve_bin() == fake_bin

    # 基本面：run 子命令 + NDJSON + 自动批准；无 -p；PROMPT 末位
    call = _call(engine="opencode")
    cmd, env = spec.build_argv(call)
    assert cmd == [fake_bin, "run", "--format", "json", "--auto",
                   "--variant", "high", "PROMPT"]
    assert "-p" not in cmd
    # env 注入：snapshot 必关 + share 字符串枚举 + permission map（v1.18 实测 schema：
    # share 要 "manual"|"auto"|"disabled"，deny 是 工具键→规则 map 非字符串数组）
    cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert cfg["snapshot"] is False and cfg["share"] == "disabled"
    assert cfg["autoupdate"] is False
    assert cfg["permission"]["question"] == "deny"
    # profile 工具黑名单并入 permission map（claude 工具名→opencode 权限键）
    _, env2 = spec.build_argv(_call(engine="opencode", disallowed_tools=["WebSearch", "WebFetch"]))
    perm2 = json.loads(env2["OPENCODE_CONFIG_CONTENT"])["permission"]
    assert perm2 == {"question": "deny", "websearch": "deny", "webfetch": "deny"}


def test_opencode_model_and_effort_mapping(monkeypatch, tmp_path):
    from loadn_webui.engines import ENGINES
    monkeypatch.setenv("WORKDADDY_OPENCODE_BIN", str(tmp_path / "oc"))
    monkeypatch.setattr("loadn_webui.config.CONFIG.engines.opencode.bin", None)
    spec = ENGINES["opencode"]
    # 裸模型名 → opencode_provider 前缀（默认 zai）
    cmd, _ = spec.build_argv(_call(model="glm-4.7"))
    assert _flag_value(cmd, "--model") == "zai/glm-4.7"
    # 已含 provider 的原样透传
    cmd, _ = spec.build_argv(_call(model="anthropic/claude-sonnet-4"))
    assert _flag_value(cmd, "--model") == "anthropic/claude-sonnet-4"
    # 无模型省略
    cmd, _ = spec.build_argv(_call(model=None))
    assert "--model" not in cmd
    # effort 白名单内的透传；词表外的省略（防 CLI 秒拒）
    cmd, _ = spec.build_argv(_call(effort="minimal"))
    assert _flag_value(cmd, "--variant") == "minimal"
    cmd, _ = spec.build_argv(_call(effort="ultra"))
    assert "--variant" not in cmd


def test_opencode_session_flag_semantics(monkeypatch, tmp_path):
    from loadn_webui.engines import ENGINES
    monkeypatch.setenv("WORKDADDY_OPENCODE_BIN", str(tmp_path / "oc"))
    monkeypatch.setattr("loadn_webui.config.CONFIG.engines.opencode.bin", None)
    spec = ENGINES["opencode"]
    # fresh（首 turn）：不传任何 session flag，会话由引擎自建
    cmd, _ = spec.build_argv(_call(session_id="", resume=False))
    assert "--session" not in cmd
    # resume：--session ses_…（非 UUID 域原样，不规范化）
    cmd, _ = spec.build_argv(_call(session_id="ses_abc123", resume=True))
    assert _flag_value(cmd, "--session") == "ses_abc123"
    # resume 但 id 空（new_session_id()="" 的轮换占位）：不传，仍引擎自建
    cmd, _ = spec.build_argv(_call(session_id="", resume=True))
    assert "--session" not in cmd and cmd[-1] == "PROMPT"


def test_opencode_no_max_turns_flag_degrades_with_warning(caplog, monkeypatch, tmp_path):
    import loadn_webui.engines.opencode as oc_mod
    from loadn_webui.engines import ENGINES
    monkeypatch.setenv("WORKDADDY_OPENCODE_BIN", str(tmp_path / "oc"))
    monkeypatch.setattr("loadn_webui.config.CONFIG.engines.opencode.bin", None)
    monkeypatch.setattr(oc_mod, "_no_max_turns_warned", False)
    spec = ENGINES["opencode"]
    with caplog.at_level(logging.WARNING, logger="loadn_webui.engines.opencode"):
        cmd, _ = spec.build_argv(_call(max_turns=7, timeout_s=600))
        cmd2, _ = spec.build_argv(_call(max_turns=7))
    assert "--max-turns" not in cmd and "--max-turns" not in cmd2
    warns = [r for r in caplog.records if "max-turns" in r.getMessage()]
    assert len(warns) == 1                              # 降级只警告一次
    assert "timeout_s" in warns[0].getMessage()


def test_opencode_bin_resolution_and_capability_bits(monkeypatch):
    import loadn_webui.engines.opencode as oc_mod
    from loadn_webui.config import CONFIG
    from loadn_webui.engines import ENGINES
    spec = ENGINES["opencode"]
    # 配置 bin > env
    monkeypatch.setattr(CONFIG.engines.opencode, "bin", "/opt/oc/opencode")
    monkeypatch.setenv("WORKDADDY_OPENCODE_BIN", "/usr/local/bin/opencode")
    assert spec.resolve_bin() == "/opt/oc/opencode"
    # env > PATH 探测；未安装 → 空串（spawn_failed 路）
    monkeypatch.setattr(CONFIG.engines.opencode, "bin", None)
    monkeypatch.delenv("WORKDADDY_OPENCODE_BIN", raising=False)
    monkeypatch.setattr(oc_mod.shutil, "which", lambda name: None)
    assert spec.resolve_bin() == ""
    cmd, _ = spec.build_argv(_call())
    assert cmd[0] == ""      # 空 argv[0] → runner create_subprocess_exec OSError → spawn_failed
    # 能力位
    assert spec.name == "opencode"
    assert spec.supports_transcript is False and spec.max_turns_flag is False
    assert spec.session_id_domain == "any"
    assert spec.transcript_age("ses_x") is None        # 无 transcript 兜底
    assert spec.new_session_id() == ""                 # 轮换 = 下次 fresh 不传 id
    assert spec.is_in_use_error("Session ID already in use") is False


# ---------------------------------------------------------------- 适配器
def _load_events(name: str) -> list[dict]:
    return [json.loads(l) for l in (FIXTURES / name).read_text().splitlines() if l.strip()]


def _feed_all(name: str, adapter):
    out: list[dict] = []
    for ev in _load_events(name):
        out += adapter.feed(ev)
    return out


def _adapter():
    from loadn_webui.engines.opencode import OpencodeEventAdapter
    return OpencodeEventAdapter()


def test_adapter_text_accumulates_without_duplicates():
    """同一 text part 的多次全量快照只发一次整块终值；delta 丢弃；usage 四键映射。"""
    ad = _adapter()
    out = _feed_all("opencode_text.ndjson", ad)
    assert [e["type"] for e in out] == ["system", "assistant"]
    assert out[0]["subtype"] == "init"
    assert out[0]["session_id"] == "ses_fix_text01"
    blocks = out[1]["message"]["content"]
    assert blocks == [{"type": "text", "text": "好的，我来介绍一下 workdaddy 平台。"}]
    # finalize：idle + 无 error → success result
    res = ad.finalize(0, 1.5)
    assert [e["type"] for e in res] == ["result"]
    r = res[0]
    assert r["subtype"] == "success" and r["session_id"] == "ses_fix_text01"
    # step-finish 四键：output+reasoning 合并、cache read/write 分列
    # （session.updated 的 999999 是会话累计兜底值，此处不得双计）
    assert r["usage"] == {"input_tokens": 1000, "output_tokens": 250,
                          "cache_read_input_tokens": 10000,
                          "cache_creation_input_tokens": 100}
    assert r["total_cost_usd"] == 0.05 and r["num_turns"] == 1
    assert r["duration_ms"] == 1500
    mu = r["modelUsage"]["fake"]
    assert mu == {"inputTokens": 1000, "outputTokens": 250,
                  "cacheReadInputTokens": 10000, "cacheCreationInputTokens": 100,
                  "webSearchRequests": 0, "costUSD": 0.05}
    assert r["result"] == "好的，我来介绍一下 workdaddy 平台。"


def test_adapter_tool_pairing_and_thinking():
    """reasoning→thinking、tool part pending→completed 的 tool_use/tool_result 配对；
    running 态不重发 tool_use；最终 text 在 step-finish 冲出。"""
    ad = _adapter()
    out = _feed_all("opencode_tools.ndjson", ad)
    assert [e["type"] for e in out] == ["system", "assistant", "assistant",
                                        "assistant", "user", "assistant"]
    # 快照×2 的 reasoning 只发终值 thinking 块
    assert out[1]["message"]["content"] == [{"type": "thinking",
                                             "thinking": "思考完毕：先验证环境。"}]
    # tool part 首见前，同 message 的 text 整块先冲出（终值）
    assert out[2]["message"]["content"] == [{"type": "text", "text": "让我先跑一条命令。"}]
    tu = out[3]["message"]["content"][0]
    assert tu == {"type": "tool_use", "id": "prt_fix_bash", "name": "Bash",
                  "input": {"command": "echo hi"}}
    tr = out[4]["message"]["content"][0]
    assert tr == {"type": "tool_result", "tool_use_id": "prt_fix_bash",
                  "content": "hi", "is_error": False}
    assert out[5]["message"]["content"] == [{"type": "text", "text": "环境正常，任务完成。"}]
    # finalize 合成 result：result 文本为各 text 整块拼接
    r = ad.finalize(0, 2.0)[0]
    assert r["subtype"] == "success"
    assert r["usage"]["input_tokens"] == 800 and r["usage"]["output_tokens"] == 150
    assert r["num_turns"] == 1
    assert "让我先跑一条命令。" in r["result"] and "环境正常，任务完成。" in r["result"]


def test_adapter_usage_accumulation_across_steps():
    """多 step-finish 累加（两步两 message）；modelUsage camelCase 键。"""
    ad = _adapter()
    out = _feed_all("opencode_usage.ndjson", ad)
    assert [e["type"] for e in out] == ["system", "assistant", "assistant"]
    r = ad.finalize(0, 2.5)[0]
    assert r["usage"] == {"input_tokens": 1500, "output_tokens": 350,
                          "cache_read_input_tokens": 12000,
                          "cache_creation_input_tokens": 100}
    assert r["num_turns"] == 2
    assert r["total_cost_usd"] == pytest.approx(0.05)
    assert r["modelUsage"]["fake"]["inputTokens"] == 1500
    assert r["modelUsage"]["fake"]["costUSD"] == pytest.approx(0.05)


def test_adapter_session_error_to_error_result():
    """session.error → error_during_execution；error 前的 pending text 在 finalize
    冲出；无 step-finish 无 modelID → usage 全零、modelUsage 键回落 opencode。"""
    ad = _adapter()
    out = _feed_all("opencode_error.ndjson", ad)
    assert [e["type"] for e in out] == ["system"]      # error 时 text 仍 pending
    fin = ad.finalize(1, 0.42)
    assert [e["type"] for e in fin] == ["assistant", "result"]
    assert fin[0]["message"]["content"] == [{"type": "text", "text": "开始处理…"}]
    r = fin[1]
    assert r["subtype"] == "error_during_execution"
    assert r["result"] == "provider unavailable (502)"
    assert r["usage"] == {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0}
    assert r["total_cost_usd"] is None and r["num_turns"] == 0
    assert r["modelUsage"]["opencode"]["costUSD"] == 0.0
    assert r["session_id"] == "ses_fix_err01"


def test_adapter_fastfail_no_result_without_created():
    """没见过 session.created（fastfail 秒退）：finalize 不合成 result（runner 走
    exit 码路 → _is_fast_fail）。"""
    ad = _adapter()
    assert ad.feed({"type": "session.status", "properties": {"status": {"type": "idle"}}}) == []
    assert ad.finalize(1, 0.1) == []


def test_adapter_ignores_unknown_and_garbled_events():
    """未知/乱序/残缺事件不炸、不映射；completed 直达的 tool part 补发 tool_use
    保配对；重复 completed 不重发。"""
    ad = _adapter()
    junk = [
        {"type": "session.diff", "sessionID": "ses_x"},
        {"type": "message.updated", "sessionID": "ses_x", "info": {"modelID": "m"}},
        {"type": "permission.asked", "sessionID": "ses_x"},
        {"type": "session.deleted", "sessionID": "ses_x", "info": {}},
        {"type": "compaction.started"},
        {"type": "message.part.delta", "partID": "prt_x", "delta": {"text": "x"}},
        {"type": "message.part.updated", "sessionID": "ses_x",
         "part": {"type": "file", "id": "prt_f"}},
        {"type": "message.part.updated", "part": {}},          # 无 id 无 type
        {"type": "message.part.updated"},                      # 连 part 都没有
        {"type": "session.status", "properties": None},
        {"type": "session.status", "properties": {"status": "busy"}},
        {"type": "session.created", "sessionID": "ses_x", "info": {}},
        {"type": "session.error", "sessionID": "ses_x"},       # 无 error 详情
        {"type": "message.part.updated", "sessionID": "ses_x", "messageID": "msg_1",
         "part": {"type": "tool", "id": "prt_z", "tool": "Read", "state": "completed",
                  "input": {"file_path": "a.txt"}, "output": "内容"}},  # 跳过 pending
        {"type": "message.part.updated", "sessionID": "ses_x", "messageID": "msg_1",
         "part": {"type": "tool", "id": "prt_z", "tool": "Read", "state": "completed",
                  "input": {"file_path": "a.txt"}, "output": "内容"}},  # 重复 completed
    ]
    out: list[dict] = []
    for ev in junk:
        out += ad.feed(ev)                    # 不抛即过
    assert [e["type"] for e in out] == ["system", "assistant", "user"]
    assert out[0]["model"] == ""              # created 无 modelID
    assert out[1]["message"]["content"][0]["name"] == "Read"
    assert out[2]["message"]["content"][0]["tool_use_id"] == "prt_z"
    # 兜底：session.error 无详情时 finalize 仍有 error 文本
    r = ad.finalize(0, 0.1)[-1]
    assert r["subtype"] == "error_during_execution" and r["result"]


def test_adapter_real_v118_flat_stream():
    """v1.18 实测扁平事件（2026-09-16 真机捕获）：顶层 type 即 part 类型
    （step_start/tool_use/text/step_finish），tool 的 state 是对象且调用与
    结果同事件到达——适配器必须归一成 init/tool_use/tool_result/text/result。"""
    ad = _adapter()
    out = _feed_all("opencode_real_v1.18.ndjson", ad)
    types = [e["type"] for e in out]
    assert types == ["system", "assistant", "user", "assistant"]
    assert out[0]["subtype"] == "init" and out[0]["session_id"] == "ses_FIXTURE_session0001"
    tu = out[1]["message"]["content"][0]
    assert tu["type"] == "tool_use" and tu["name"] == "bash"
    assert tu["input"] == {"command": "cat note.txt"}
    tr = out[2]["message"]["content"][0]
    assert tr["tool_use_id"] == tu["id"] and not tr["is_error"]
    assert "hahaness" in tr["content"]
    assert "文件" in out[3]["message"]["content"][0]["text"]
    # 无 session.status idle：rc==0 即完成；usage 两步累加
    res = ad.finalize(0, 5.0)
    assert len(res) == 1 and res[0]["type"] == "result"
    r = res[0]
    assert r["subtype"] == "success" and r["num_turns"] == 2
    assert r["usage"]["input_tokens"] == 9514 + 116
    assert r["usage"]["output_tokens"] == 53 + 54
    assert r["usage"]["cache_read_input_tokens"] == 1792 + 11264
    assert r["session_id"] == "ses_FIXTURE_session0001"
    # rc!=0 且无 idle → error 语义
    ad2 = _adapter()
    _feed_all("opencode_real_v1.18.ndjson", ad2)
    assert ad2.finalize(1, 5.0)[0]["subtype"] == "error_during_execution"
