"""sandbox.py 未覆盖分支单测（wrap_engine claude/opencode 分支+bwrap_available）。"""
from __future__ import annotations

import pytest

from loadn_webui import sandbox
from loadn_webui.config import CONFIG


def test_wrap_engine_off_mode_passthrough():
    CONFIG.security.sandbox = "off"
    cmd, mode = sandbox.wrap_engine(["echo", "hi"], {}, engine="claude",
                                    sid="s", cwd="/tmp")
    assert (cmd, mode) == (["echo", "hi"], "direct")


@pytest.mark.parametrize("engine", ["claude", "opencode"])
def test_wrap_engine_generic_engines(engine, monkeypatch, tmp_path):
    """claude/opencode 分支：argv 完整性（基础矩阵+专属 binds+setenv+cmd 接尾）。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "_wrap_generic",
                        lambda cmd, env, cwd, extra_binds: (
                            ["bwrap"] + [x for m, s, d in extra_binds for x in (m, s)]
                            + [f"{k}={v}" for k, v in env.items()] + cmd))
    cmd, mode = sandbox.wrap_engine(["engine-bin", "run"], {"K": "V"},
                                    engine=engine, sid="s", cwd=str(tmp_path))
    assert mode == "bwrap"
    assert cmd[-2:] == ["engine-bin", "run"]        # cmd 接尾（回归 P2 bug）
    assert "K=V" in cmd


def test_wrap_engine_generic_fallback_when_bwrap_missing(monkeypatch, tmp_path):
    """bwrap 不存在 → direct-fallback（审计留痕路径）。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: None)
    cmd, mode = sandbox.wrap_engine(["x"], {}, engine="opencode",
                                    sid="s", cwd=str(tmp_path))
    assert (cmd, mode) == (["x"], "direct-fallback")


def test_wrap_engine_loadn_routes(monkeypatch, tmp_path):
    """loadn 引擎走 wrap_loadn（同路径 bind 矩阵）。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    called = {}
    monkeypatch.setattr(sandbox, "wrap_loadn",
                        lambda cmd, env, sid_session, cwd: called.update(
                            cmd=cmd, sid=sid_session) or ["wrapped"])
    cmd, mode = sandbox.wrap_engine(
        ["loadn", "-p", "--session-id", "aaaa-bbbb", "hi"], {},
        engine="loadn", sid="s", cwd=str(tmp_path))
    assert mode == "bwrap" and cmd == ["wrapped"]
    assert called["sid"] == "aaaa-bbbb"            # 档案 id 取 argv session-id


def test_wrap_generic_full_argv(tmp_path):
    """真实 _wrap_generic：binds/try-ro 缺失跳过/env 接尾/cmd 接尾。"""
    real = tmp_path / "must-exist"
    real.mkdir()
    wrapped = sandbox._wrap_generic(
        ["/bin/echo", "ok"], {"PATH": "/usr/bin:/bin"}, cwd=tmp_path,
        extra_binds=[("rw", str(real), str(real)),
                     ("try-ro", str(tmp_path / "absent"), "")])
    assert wrapped is not None
    assert wrapped[0] == "/usr/bin/bwrap"
    assert wrapped[-2:] == ["/bin/echo", "ok"]
    assert "--unshare-ipc" in wrapped and "--die-with-parent" in wrapped
    # 必需 ro 缺失 → None（回落直跑）
    assert sandbox._wrap_generic(
        ["/bin/echo"], {}, cwd=tmp_path,
        extra_binds=[("ro", str(tmp_path / "absent"), "")]) is None


def test_egress_uds_gating(monkeypatch, tmp_path):
    """_egress_uds：egress 关→None；socket 存在→路径。"""
    from loadn_webui.config import PATHS
    monkeypatch.setattr(CONFIG.security, "egress_mode", "off")
    assert sandbox._egress_uds() is None
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    run = tmp_path / "run"
    run.mkdir()
    (run / "egress.sock").write_text("")
    monkeypatch.setitem(PATHS, "run", run)
    assert sandbox._egress_uds() == run / "egress.sock"
