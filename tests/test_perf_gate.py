"""C-perf：性能回归门（宽预算防抖动——挡的是数量级劣化不是毫秒差异）。

本机基线（2026-09-28，dev 机 load 7-8）：引擎 import 85ms / 平台
import 334ms / tail(50)@10MB <5ms。预算 = 基线 × 4~8 余量，CI 共享
runner 波动下不误报；数量级劣化（O(n)→O(整读)、import 膨胀、循环
退化）必红。已有 tail@10MB<50ms 在 test_session_eng——这里补齐引擎
冷启动/平台冷启动/fake turn 全链/context 组装/审计链校验五面。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.coverage("quality.perf")]

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
ENV = {**os.environ, "LOADN_PROVIDER": "fake",
       "PYTHONDONTWRITEBYTECODE": "1"}


def _import_ms(stmt: str) -> float:
    t0 = time.perf_counter()
    subprocess.run([PY, "-B", "-c", stmt], check=True, env=ENV, cwd=REPO)
    return (time.perf_counter() - t0) * 1000


def test_engine_cold_import_budget():
    """引擎 import < 800ms（基线 85ms×8——挡依赖面膨胀/httpx 导入退化）。"""
    dt = _import_ms("import loadn.core.build")
    assert dt < 800, f"引擎冷启动 {dt:.0f}ms（预算 800ms）"


def test_platform_cold_import_budget():
    """平台 import < 2500ms（基线 334ms×7——挡 FastAPI 装配退化）。"""
    dt = _import_ms("import loadn_webui.api.app")
    assert dt < 2500, f"平台冷启动 {dt:.0f}ms（预算 2500ms）"


async def test_fake_turn_roundtrip_budget(tmp_path):
    """ScriptedProvider 3 事件（工具轮+文本轮）全链 < 300ms（无网络纯 Python 路径——
    挡 loop 装配/replay/记账的循环退化；基线 ~30ms×10）。"""
    import tests.helpers as H
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager

    rounds = [H.tool_round("t1", "Echo", {"msg": "x"}),
              H.tool_round("t2", "Echo", {"msg": "y"}),
              H.text_round("done")]
    core = AgentCore(provider=H.ScriptedProvider(rounds),
                     tools={"Echo": H.EchoTool()},
                     session=SessionManager.create(tmp_path),
                     cwd=tmp_path, settings=LoopSettings(max_turns=5))
    t0 = time.perf_counter()
    summary = await core.run_turn("跑三轮")
    dt = (time.perf_counter() - t0) * 1000
    assert summary.subtype == "success" and summary.num_turns == 3
    assert dt < 300, f"fake turn 全链 {dt:.0f}ms（预算 300ms）"


async def test_context_assembly_budget(tmp_path, monkeypatch):
    """ContextAssembler 8 节组装 < 60ms（基线 ~5ms×10——挡 env 树/链扫描
    退化为整仓遍历）。"""
    from loadn.core.context import ContextAssembler
    for i in range(40):                      # 小树即可量级判定
        (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    for i in range(20):
        (sub / f"m{i}.py").write_text("y = 2\n", encoding="utf-8")
    b = ContextAssembler(cwd=tmp_path)
    t0 = time.perf_counter()
    text = b.build()
    dt = (time.perf_counter() - t0) * 1000
    assert text and dt < 60, f"8 节组装 {dt:.0f}ms（预算 60ms）"


def test_audit_chain_verify_budget(tmp_path, monkeypatch):
    """审计链 1000 行校验 < 400ms（基线 ~20ms×20——挡哈希链退化为
    全表重算×N；月分表后单次校验只碰当月）。"""
    monkeypatch.setenv("LOADN_WEBUI_HOME", str(tmp_path))
    from loadn_webui import audit as audit_mod
    for i in range(1000):
        audit_mod.audit("anomaly", {"what": "perf-probe", "i": i})
    t0 = time.perf_counter()
    bad = audit_mod.verify()
    dt = (time.perf_counter() - t0) * 1000
    assert bad == [] and dt < 400, f"1000 行链校验 {dt:.0f}ms（预算 400ms）"
