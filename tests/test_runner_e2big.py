"""E2BIG 防线对赌：超长 prompt（>128KB 单参上限的元凶）改走 stdin 管道。

用户实证：粘贴超长内容（大日志/长文档）→ spawn_failed: [Errno 7]
Argument list too long（Linux MAX_ARG_STRLEN=128KB，prompt 是三引擎
argv[-1] 契约位）。修复=build_argv 后超 100KB 摘 argv[-2:] 改 stdin
（claude/loadn/opencode -p 均支持管道 prompt）。fake 引擎回读 stdin
字节数与 positional 参数个数作双向证据。
"""
from __future__ import annotations

import stat
from pathlib import Path

import pytest

from loadn_webui.claude_runner import TurnCall, run_turn

FAKE_PY = '''#!/usr/bin/env python3
import sys
# 精确证据：argv 里有无 "--" 分隔符（=prompt 走 argv）+ stdin 字节数（=prompt 走管道）
if "--" in sys.argv[1:]:
    print(f"DDASH=1 STDIN_BYTES={len(sys.stdin.buffer.read())}")
else:
    print(f"DDASH=0 STDIN_BYTES={len(sys.stdin.buffer.read())}")
'''


@pytest.fixture
def fake_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binp = tmp_path / "fake-claude"
    binp.write_text(FAKE_PY)
    binp.chmod(binp.stat().st_mode | stat.S_IEXEC)
    # CONFIG.engines.claude.bin 优先级最高（生产 config.yaml 可能非空，会压住
    # 环境变量——首跑曾误发真引擎 turn 烧 $0.05，实证修复全链但不可重复）
    monkeypatch.setattr("loadn_webui.config.CONFIG.engines.claude.bin", str(binp))
    monkeypatch.setattr("loadn_webui.config.CONFIG.claude.claude_bin", "")
    monkeypatch.setenv("LOADN_CLAUDE_BIN", str(binp))
    return binp


def _call(prompt: str, cwd: Path) -> TurnCall:
    return TurnCall(prompt=prompt, cwd=cwd, session_id="11111111-1111-1111-1111-111111111111",
                    effort="high", turn_id=1, sid="t-e2big", engine="claude",
                    timeout_s=30, stall_timeout_s=20)


def _tail(out_log: str) -> str:
    return Path(out_log).read_text(errors="replace")[-400:]


async def test_long_prompt_goes_stdin(fake_engine, tmp_path):
    """200KB prompt：argv 无 -- 分隔符（DDASH=0）+ stdin 全量到达（200000 字节）+ exit 0。"""
    r = await run_turn(_call("x" * 200_000, tmp_path))
    assert r.ok, f"turn 失败: {r.error} | tail={_tail(r.log_out)}"
    tail = _tail(r.log_out)
    assert "DDASH=0" in tail, tail            # --/prompt 已从 argv 摘除
    assert "STDIN_BYTES=200000" in tail, tail  # 管道全量到达（修复前=spawn_failed E2BIG）


async def test_short_prompt_keeps_argv(fake_engine, tmp_path):
    """短 prompt：argv 契约不变（DDASH=1），stdin 路径不触发。"""
    r = await run_turn(_call("hello", tmp_path))
    assert r.ok, f"turn 失败: {r.error}"
    tail = _tail(r.log_out)
    assert "DDASH=1" in tail, tail
    assert "STDIN_BYTES=0" in tail, tail


async def test_boundary_just_over_limit(fake_engine, tmp_path):
    """100_001 字节（刚过阈值）：走 stdin；99_999 不触发（由 short 用例旁证）。"""
    r = await run_turn(_call("y" * 100_001, tmp_path))
    assert r.ok, f"turn 失败: {r.error}"
    assert "STDIN_BYTES=100001" in _tail(r.log_out)
    assert "DDASH=0" in _tail(r.log_out)
