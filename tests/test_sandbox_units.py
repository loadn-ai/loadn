"""sandbox.py 未覆盖分支单测（wrap_engine claude/opencode 分支+bwrap_available）。"""
from __future__ import annotations

import pytest

from loadn_webui import sandbox
from loadn_webui.config import CONFIG


@pytest.fixture(autouse=True)
def _fresh_tier_cache():
    """W2 档位解析缓存按 requested 值进程内常驻——测试间必须复位
    （否则前一个用例的探测结果/降档原因串进下一个）。"""
    sandbox._reset_tier_cache()
    yield
    sandbox._reset_tier_cache()


def test_wrap_engine_off_mode_passthrough(monkeypatch):
    monkeypatch.setattr(CONFIG.security, "sandbox", "off")
    cmd, mode = sandbox.wrap_engine(["echo", "hi"], {}, engine="claude",
                                    sid="s", cwd="/tmp")
    assert (cmd, mode) == (["echo", "hi"], "direct")


@pytest.mark.parametrize("engine", ["claude", "opencode"])
def test_wrap_engine_generic_engines(engine, monkeypatch, tmp_path):
    """claude/opencode 分支：argv 完整性（基础矩阵+专属 binds+setenv+cmd 接尾）。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    monkeypatch.setattr(sandbox, "bwrap_available", lambda: True)   # 探测与 which 解耦
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "_wrap_generic",
                        lambda cmd, env, cwd, extra_binds, project_root=None: (
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
    monkeypatch.setattr(sandbox, "bwrap_available", lambda: False)  # 档位探测同败
    cmd, mode = sandbox.wrap_engine(["x"], {}, engine="opencode",
                                    sid="s", cwd=str(tmp_path))
    assert (cmd, mode) == (["x"], "direct-fallback")


def test_wrap_engine_loadn_routes(monkeypatch, tmp_path):
    """loadn 引擎走 wrap_loadn（同路径 bind 矩阵）。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    monkeypatch.setattr(sandbox, "bwrap_available", lambda: True)
    called = {}
    monkeypatch.setattr(sandbox, "wrap_loadn",
                        lambda cmd, env, sid_session, cwd, project_root=None, \
                               owner_sid="": called.update(
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


def test_project_binds_order_and_gating(tmp_path):
    """_project_binds：None 直通；项目根 ro + 共享 inputs rw，且先于 cwd bind。"""
    assert sandbox._project_binds([], None) == []
    proj = tmp_path / "proj"
    (proj / "inputs").mkdir(parents=True)
    task_ws = proj / "tasks" / "01-x"
    task_ws.mkdir(parents=True)
    argv: list = []
    sandbox._project_binds(argv, proj)
    argv += ["--bind", str(task_ws), str(task_ws)]
    i_root = argv.index("--ro-bind")            # 第一个 ro-bind 是项目根
    assert argv[i_root + 1] == str(proj)
    i_in = argv.index("--bind")
    assert argv[i_in + 1] == str(proj / "inputs")
    assert i_root < i_in < argv.index("--bind", i_in + 1)   # 根→inputs→cwd 次序
    # inputs 目录不存在 → 只挂根 ro
    proj2 = tmp_path / "proj2"
    proj2.mkdir()
    argv2: list = []
    sandbox._project_binds(argv2, proj2)
    assert str(proj2) in argv2 and str(proj2 / "inputs") not in argv2


def test_egress_uds_gating(monkeypatch, tmp_path):
    """_egress_uds：socket 不存在→None。三态档位都走代理（off=直通+审计），
    不再按 mode 关闭——off 档下 bwrap 引擎同样 unshare-net+UDS。"""
    from loadn_webui.config import PATHS
    monkeypatch.setattr(CONFIG.security, "egress_mode", "off")
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setitem(PATHS, "run", run)
    assert sandbox._egress_uds() is None                 # 无 socket → 不启用
    (run / "egress.sock").write_text("")
    assert sandbox._egress_uds() == run / "egress.sock"  # off 档也存在


# ---------------------------------------------------------------- M3 突变补测
def test_bwrap_available_false_on_probe_fail(monkeypatch):
    """探测子进程非零退出 → False（returncode 判定从未被对赌）。"""
    import subprocess

    class _P:
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
    assert sandbox.bwrap_available() is False
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert sandbox.bwrap_available() is False   # 探测异常同败（降档语义）


def test_wrap_engine_downgrade_warn_deduped(monkeypatch, tmp_path):
    """降档告警去重：同 reason 第二次静默；显式 off 零告警 + direct。"""
    import io
    import logging

    monkeypatch.setattr(sandbox, "resolve_tier",
                        lambda req=None: ("off", "bwrap-unavailable"))
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    sandbox.log.addHandler(h)
    try:
        cmd, mode = sandbox.wrap_engine(["x"], {}, engine="loadn",
                                        sid="s1", cwd=str(tmp_path))
        assert (cmd, mode) == (["x"], "direct-fallback")   # 降档 ≠ direct
        assert "bwrap-unavailable" in buf.getvalue()        # 首次必告警
        buf.seek(0), buf.truncate(0)
        sandbox.wrap_engine(["x"], {}, engine="loadn", sid="s1",
                            cwd=str(tmp_path))
        assert buf.getvalue() == ""                         # 同因第二次静默
        monkeypatch.setattr(sandbox, "resolve_tier", lambda req=None: ("off", ""))
        buf.seek(0), buf.truncate(0)
        _, mode2 = sandbox.wrap_engine(["x"], {}, engine="loadn",
                                       sid="s1", cwd=str(tmp_path))
        assert mode2 == "direct" and buf.getvalue() == ""   # 显式 off 零告警
    finally:
        sandbox.log.removeHandler(h)


def test_wrap_loadn_net_bridge_toggle(monkeypatch, tmp_path):
    """UDS 桥双态：无 socket=share-net 直跑；有=unshare-net+socat 桥前缀。"""
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "_egress_uds", lambda sid="": None)
    argv = sandbox.wrap_loadn(["loadn", "hi"], {"https_proxy":
                                                "http://127.0.0.1:8793"},
                              sid_session="s", cwd=tmp_path)
    assert "--share-net" in argv and "--unshare-net" not in argv
    assert "socat" not in " ".join(argv) and argv[-2:] == ["loadn", "hi"]

    uds = tmp_path / "egress-x.sock"
    uds.write_text("")
    monkeypatch.setattr(sandbox, "_egress_uds", lambda sid="": uds)
    argv2 = sandbox.wrap_loadn(["loadn", "hi"], {"https_proxy":
                                                 "http://127.0.0.1:8793"},
                               sid_session="s", cwd=tmp_path)
    joined = " ".join(argv2)
    assert "--unshare-net" in argv2 and "--share-net" not in argv2
    assert "socat" in joined and "8793" in joined     # 真桥前缀（非早退直跑）
    assert str(uds) in argv2                          # socket ro-bind 进沙箱
