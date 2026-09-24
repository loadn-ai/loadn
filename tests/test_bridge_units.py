"""T4：bridge 进程内单测（conn 拒绝 + env 寻址）。

双向转发面由 tests/test_daemon.py::test_bridge_relays_stdio 的子进程
真中继用例稳定覆盖——进程内对拍（线程泵 × session loop 复用）实测
非确定，去重不赌运气。"""
from __future__ import annotations

from loadn.transport import bridge as bridge_mod


# ---------------------------------------------------------------- 桥
async def test_bridge_conn_refused(tmp_path, monkeypatch, capsys):
    """连不上 UDS → rc 1 + 可读 stderr（宿主 spawn 失败自诊断）。"""
    sock = tmp_path / "nope.sock"
    rc = await bridge_mod.run_bridge(sock)
    assert rc == 1
    assert "失败" in capsys.readouterr().err


def test_bridge_main_env_sock(monkeypatch, tmp_path):
    """main()：LOADN_ENGINE_SOCK 环境变量指定 socket 路径。"""
    sock = tmp_path / "env.sock"
    monkeypatch.setenv("LOADN_ENGINE_SOCK", str(sock))
    monkeypatch.setattr(bridge_mod, "run_bridge",
                        lambda p: (_ for _ in ()).throw(AssertionError(str(p))))

    async def fake_rb(p):
        assert p == sock
        return 7

    monkeypatch.setattr(bridge_mod, "run_bridge", fake_rb)
    assert bridge_mod.main() == 7
