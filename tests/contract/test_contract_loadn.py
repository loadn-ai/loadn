"""PROTOCOL v1 契约对赌（tests/contract/）——引擎侧真 CLI + webui 侧 spec 双面锁定。

对应 docs/PROTOCOL.md 条目：
§1 argv 方言（PROMPT 恒 argv[-1]、旗标面）
§2 事件流（result 硬契约字段）
§3 会话语义（冲突文案含 "Session ID already in use" 子串）
§4 能力位附表（loadn 行）
§5 steer（env 通道、一致性铁律、消费回执）
§6 判死兜底（transcript 家目录约定）

改契约 = 独立 PR + PROTOCOL bump + 本文件同步——两侧任一漂移此处红牌。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent


def _loadn_bin() -> str:
    """被测引擎二进制：与 webui 同 venv 的 loadn console_script。"""
    sibling = Path(sys.executable).parent / "loadn"
    if sibling.exists():
        return str(sibling)
    pytest.skip("loadn console_script 不在本解释器 venv（契约对赌需引擎侧同环境）")


def _run_engine(home: Path, sid: str, prompt: str, *, resume: bool = False,
                steer_file: Path | None = None, cwd: Path | None = None,
                timeout: float = 30) -> tuple[int, list[dict], str]:
    argv = [_loadn_bin(), "-p", "--verbose", "--output-format", "stream-json",
            "--dangerously-skip-permissions", "--permission-mode", "bypassPermissions"]
    argv += (["--resume", sid] if resume else ["--session-id", sid])
    argv.append(prompt)
    env = {**os.environ, "LOADN_PROVIDER": "fake", "LOADN_HOME": str(home)}
    if steer_file is not None:
        env["LOADN_STEER_FILE"] = str(steer_file)
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                       env=env, cwd=str(cwd or home))
    events = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return p.returncode, events, p.stderr


# ---------------------------------------------------------------- §1 argv

def test_protocol_argv_contract():
    """§1：build_argv 旗标面 + PROMPT 恒 argv[-1]（webui 调用纪律）。"""
    from loadn_webui.claude_runner import TurnCall
    from loadn_webui.engines.loadn import LoadnSpec

    ws = Path("/tmp/ws")
    call = TurnCall(turn_id=1, session_id="11111111-2222-3333-4444-555555555555",
                    prompt="干 A 活；B 活", cwd=ws, sid="s1", engine="loadn",
                    effort="high", max_turns=7,
                    rotate_input_tokens=50000)
    cmd, env = LoadnSpec().build_argv(call)
    assert cmd[-1] == "干 A 活；B 活"                    # PROMPT 恒 argv[-1]
    assert "-p" in cmd and "--verbose" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--no-compact" in cmd                          # rotate 已设 → 禁内压（§1）
    assert cmd[cmd.index("--max-turns") + 1] == "7"
    assert cmd[cmd.index("--session-id") + 1] == "11111111-2222-3333-4444-555555555555"
    assert "--resume" not in cmd
    assert env.get("LOADN_STEER_FILE") == str(ws / ".steer.s1.jsonl")   # §5


def test_protocol_steer_path_consistency_invariant(tmp_path):
    """§5 一致性铁律：spec 用的 steer 路径 == webui 写入路径（ws_of(sid)==cwd 不变量）。

    engine.steer_if_running 写 ws_of(sid)/.steer.<sid>.jsonl；spec 拼 call.cwd
    ——两源恒等是隐式耦合，沙箱/容器路径映射改变会静默断链，此处锁死。
    """
    from loadn_webui import workspace as ws_mod
    from loadn_webui.claude_runner import TurnCall
    from loadn_webui.engines.loadn import LoadnSpec

    sid = "20260922-consistency-x1"
    ws = ws_mod.ws_of(sid) if hasattr(ws_mod, "ws_of") else tmp_path / sid
    call = TurnCall(turn_id=1, session_id=str(uuid.uuid4()), prompt="p",
                    cwd=ws, sid=sid, engine="loadn", effort="high")
    _, env = LoadnSpec().build_argv(call)
    # webui 写入侧形态（engine.py:196-201 同款）
    webui_side = ws / f".steer.{sid}.jsonl"
    assert env["LOADN_STEER_FILE"] == str(webui_side)


# ------------------------------------------------------------ §2 事件流

def test_protocol_event_stream_and_result_contract(tmp_path):
    """§2：事件类型 ⊆ 声明集合；result 硬字段齐全。"""
    sid = str(uuid.uuid4())
    code, events, err = _run_engine(tmp_path, sid, "契约测试")
    assert code == 0, err
    allowed = {"system", "assistant", "user", "result", "stream_event",
               "steer", "plan", "todos"}
    for e in events:
        assert e["type"] in allowed, f"未声明事件类型: {e['type']}"
    result = [e for e in events if e["type"] == "result"]
    assert len(result) == 1
    r = result[0]
    for k in ("usage", "modelUsage", "num_turns", "total_cost_usd",
              "subtype", "session_id"):
        assert k in r, f"result 缺硬契约字段 {k}"
    assert r["session_id"] == sid


# ------------------------------------------------------------ §3 冲突文案

def test_protocol_session_conflict_wording(tmp_path):
    """§3：同 id 二次跑 → stderr 含子串 + exit 1（fresh→resume 翻转依赖）。"""
    sid = str(uuid.uuid4())
    code1, _, _ = _run_engine(tmp_path, sid, "第一次")
    assert code1 == 0
    code2, _, err2 = _run_engine(tmp_path, sid, "第二次")   # 同 id fresh=冲突
    assert code2 == 1
    assert "Session ID already in use" in err2


# ------------------------------------------------------------ §5 steer 消费

def _run_engine_steering(home: Path, sid: str, steer: Path, cwd: Path,
                         env_key: str = "LOADN_STEER_FILE") -> list[dict]:
    """Popen 流式驱动：慢工具撑开窗口，读到首轮输出后追加 steer，收全程事件。

    §5 语义：构造时刻 offset——启动前预置内容不重放，必须运行中追加。
    env_key 参数化同时覆盖新旧通道名。
    """
    (cwd / ".fake").mkdir(exist_ok=True)
    (cwd / ".fake" / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "sleep 3 && echo done"}}))
    steer.write_text("", encoding="utf-8")          # 空文件：offset=0
    argv = [_loadn_bin(), "-p", "--verbose", "--output-format", "stream-json",
            "--dangerously-skip-permissions", "--session-id", sid, "跑"]
    env = {k: v for k, v in os.environ.items() if k != "LOADN_STEER_FILE"}
    env.update({"LOADN_PROVIDER": "fake", "LOADN_HOME": str(home),
                env_key: str(steer)})
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, env=env, cwd=str(cwd))
    events: list[dict] = []
    injected = False
    for line in proc.stdout:                        # type: ignore[union-attr]
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not injected and events and events[0].get("type") == "system":
            time.sleep(0.2)                         # 确保引擎已过构造点
            with steer.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": "2026-09-22T00:00:00",
                                    "text": "插话契约"}, ensure_ascii=False) + "\n")
            injected = True
    proc.wait(timeout=30)
    return events


def test_protocol_steer_consumed_with_receipt(tmp_path):
    """§5：运行中追加的插话被轮询消费并回执 steer 事件。"""
    import time  # noqa: F401 （_run_engine_steering 内联使用）

    sid = str(uuid.uuid4())
    steer = tmp_path / f".steer.{sid}.jsonl"
    events = _run_engine_steering(tmp_path, sid, steer, cwd=tmp_path)
    steers = [e for e in events if e["type"] == "steer"]
    assert steers and steers[0]["text"] == "插话契约"


def test_protocol_steer_env_legacy_fallback(tmp_path):
    """§5：旧名 HAHANESS_STEER_FILE 仍被引擎接受（fallback 一版）。"""
    sid = str(uuid.uuid4())
    steer = tmp_path / f".steer.{sid}.jsonl"
    events = _run_engine_steering(tmp_path, sid, steer, cwd=tmp_path,
                                  env_key="HAHANESS_STEER_FILE")
    steers = [e for e in events if e["type"] == "steer"]
    assert steers and steers[0]["text"] == "插话契约"


# ------------------------------------------------------------ §6 transcript

def test_protocol_transcript_home_convention(tmp_path):
    """§6：transcript 落 $LOADN_HOME/sessions/<id>/transcript.jsonl；spec 探测得到。"""
    from loadn_webui.engines.loadn import LoadnSpec, loadn_home

    sid = str(uuid.uuid4())
    code, _, err = _run_engine(tmp_path, sid, "探测")
    assert code == 0, err
    t = tmp_path / "sessions" / sid / "transcript.jsonl"
    assert t.exists(), "transcript 未落家目录约定路径"
    monkey_home = tmp_path
    old = os.environ.get("LOADN_HOME")
    os.environ["LOADN_HOME"] = str(monkey_home)
    try:
        assert loadn_home() == monkey_home
        age = LoadnSpec().transcript_age(sid)
        assert age is not None and age >= 0        # 探测约定成立
    finally:
        if old is None:
            os.environ.pop("LOADN_HOME", None)
        else:
            os.environ["LOADN_HOME"] = old


# ------------------------------------------------------------ §4 能力位

def test_protocol_capability_bits():
    """§4 附表 loadn 行：全旗标、uuid 域、transcript 判死可用。"""
    from loadn_webui.engines.loadn import LoadnSpec

    spec = LoadnSpec()
    assert spec.supports_transcript is True
    assert spec.session_id_domain == "uuid"
    assert spec.max_turns_flag is True
    assert spec.effort_flag is True
