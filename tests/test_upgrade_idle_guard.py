"""upgrade 非空闲守卫（require-idle / 跳过等待可见性）。

背景（2026-10-10 用户实证）：升级重启对活跃 turn 两档影响——直跑档
KillMode=process 幸存+收养续跑；沙箱档 --die-with-parent 同死必被打断
（DB 历史 18 例 server_restart 中断）。--wait-idle 0 静默强切是主因。

对赌：
① --require-idle 有活跃 turn：preflight 后、DB 备份/资产重刷**之前**拒绝
   （零副作用退出）；
② --require-idle 空闲：放行到后续步骤（这里 mock 备份抛标记异常截断）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from loadn_webui import ops as ops_mod


def _patch_stubs(monkeypatch, *, active: list[dict]) -> None:
    monkeypatch.setattr(ops_mod, "_list_releases", lambda: ["v9.9.9"])
    monkeypatch.setattr(ops_mod, "_current_version", lambda: "v0.0.1")
    monkeypatch.setattr(ops_mod, "_preflight", lambda _p: [])
    monkeypatch.setattr(ops_mod, "_active_turns", lambda: active)


def test_require_idle_rejects_before_any_side_effect(monkeypatch, capsys):
    """>>> 有活跃 turn：EXIT_PRECONDITION 且未动 DB 备份/资产。"""
    _patch_stubs(monkeypatch, active=[{"id": 42, "session_id": "s-x"}])
    touched = []
    monkeypatch.setattr(ops_mod, "_backup_db",
                        lambda *a, **k: touched.append("backup") or Path("/tmp/x"))
    rc = ops_mod.cmd_upgrade("v9.9.9", wait_idle=0, yes=True, require_idle=True)
    assert rc == ops_mod.EXIT_PRECONDITION
    assert not touched, "严格模式必须在 DB 备份之前拒绝（零副作用）"
    err = capsys.readouterr().err
    assert "42" in err and "--require-idle" in err


def test_require_idle_passes_when_idle(monkeypatch):
    """>>> 空闲：放行（用备份标记异常截断流程，证走到 [2/5]）。"""
    _patch_stubs(monkeypatch, active=[])

    class _Stop(Exception):
        pass

    monkeypatch.setattr(ops_mod, "_backup_db",
                        lambda *a, **k: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        ops_mod.cmd_upgrade("v9.9.9", wait_idle=0, yes=True, require_idle=True)


def test_skip_wait_warns_on_active_turns(monkeypatch, capsys):
    """>>> --wait-idle 0 且活跃 turn：非 --yes 时 stderr 强警告（含中断语义）。"""
    _patch_stubs(monkeypatch, active=[{"id": 7, "session_id": "s-y"}])

    class _Stop(Exception):
        pass

    # 拦在 restart 之前（警告发生在 [3/5]，随后切指针——用换指针函数截断）
    monkeypatch.setattr(ops_mod, "_switch_current",
                        lambda *a, **k: (_ for _ in ()).throw(_Stop()))
    monkeypatch.setattr(ops_mod, "_backup_db", lambda *a, **k: Path("/tmp/x"))
    monkeypatch.setattr(ops_mod, "refresh_default_assets", lambda _p: 0)
    monkeypatch.setattr(ops_mod, "_refresh_session_settings", lambda: 0)
    monkeypatch.setattr(ops_mod, "_graceful_stop_active", lambda **k: [])
    with pytest.raises(_Stop):
        ops_mod.cmd_upgrade("v9.9.9", wait_idle=0, yes=False)
    err = capsys.readouterr().err
    assert "跳过等待" in err and "优雅停" in err, "跳过等待必须有 stop 优雅停语义警告"


def test_yes_skip_wait_also_warns(monkeypatch, capsys):
    """>>> --wait-idle 0 --yes 强切也打印警告（v0.7.51 教训：静默强切
    打断两个生产任务且被标 done 假完成——可见性不随 --yes 丢失）。"""
    _patch_stubs(monkeypatch, active=[{"id": 9, "session_id": "s-z"}])

    class _Stop(Exception):
        pass

    monkeypatch.setattr(ops_mod, "_switch_current",
                        lambda *a, **k: (_ for _ in ()).throw(_Stop()))
    monkeypatch.setattr(ops_mod, "_backup_db", lambda *a, **k: Path("/tmp/x"))
    monkeypatch.setattr(ops_mod, "refresh_default_assets", lambda _p: 0)
    monkeypatch.setattr(ops_mod, "_refresh_session_settings", lambda: 0)
    monkeypatch.setattr(ops_mod, "_graceful_stop_active", lambda **k: [])
    with pytest.raises(_Stop):
        ops_mod.cmd_upgrade("v9.9.9", wait_idle=0, yes=True)
    err = capsys.readouterr().err
    assert "跳过等待" in err and "turn 9" in err, "--yes 强切也必须点名活跃 turn"


def test_graceful_stop_called_before_switch(monkeypatch):
    """>>> 3.5 步优雅停在换指针/restart 之前（顺序对赌）。"""
    _patch_stubs(monkeypatch, active=[{"id": 11, "session_id": "s-o"}])
    calls: list[str] = []

    class _Stop(Exception):
        pass

    monkeypatch.setattr(ops_mod, "_graceful_stop_active",
                        lambda **k: calls.append("stop") or [])
    monkeypatch.setattr(ops_mod, "_backup_db", lambda *a, **k: Path("/tmp/x"))
    monkeypatch.setattr(ops_mod, "refresh_default_assets", lambda _p: 0)
    monkeypatch.setattr(ops_mod, "_refresh_session_settings", lambda: 0)
    monkeypatch.setattr(ops_mod, "_switch_current",
                        lambda *a, **k: calls.append("switch") or (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        ops_mod.cmd_upgrade("v9.9.9", wait_idle=0, yes=True)
    assert calls == ["stop", "switch"], f"stop 必须先于换指针：{calls}"


# ------------------------------------------------------ _graceful_stop_active 单元
def test_graceful_stop_happy_and_404(monkeypatch):
    """>>> 200 受理→轮询收尾完成；404=已结束同视为成功；均无 lingering。"""
    import urllib.error as ue

    monkeypatch.setattr(ops_mod, "_health_token", lambda: "T")
    monkeypatch.setattr(ops_mod, "_health_url",
                        lambda: "http://127.0.0.1:8792/api/health")
    monkeypatch.setattr(ops_mod, "_active_turns", lambda: (
        [{"id": 1, "session_id": "a"}, {"id": 2, "session_id": "b"}]
        if not hasattr(test_graceful_stop_happy_and_404, "done") else []))
    calls: list[str] = []

    def fake(req, timeout=0):                    # noqa: ANN001
        calls.append(req.full_url)
        if req.full_url.endswith("/api/turns/2/stop"):
            raise ue.HTTPError(req.full_url, 404, "gone", None, None)
        test_graceful_stop_happy_and_404.done = True   # type: ignore[attr-defined]
        return "resp"

    monkeypatch.setattr(ops_mod.urllib.request, "urlopen", fake)
    out = ops_mod._graceful_stop_active(timeout_s=1)
    assert out == [], "200 受理（1）+404 已结束（2）都不应 lingering"
    assert len(calls) == 2 and calls[0].endswith("/api/turns/1/stop")
    assert calls[1].endswith("/api/turns/2/stop")
    del test_graceful_stop_happy_and_404.done    # type: ignore[attr-defined]


def test_graceful_stop_timeout_and_http_error(monkeypatch, capsys):
    """>>> 超时未收尾/HTTP 5xx：如实 lingering（restart 兜底+调用方可见）。"""
    import urllib.error as ue

    monkeypatch.setattr(ops_mod, "_health_token", lambda: "T")
    monkeypatch.setattr(ops_mod, "_health_url",
                        lambda: "http://127.0.0.1:8792/api/health")

    # 场景 A：恒活跃（超时路径；timeout_s=0 直落终判，不等 2s 轮询）
    monkeypatch.setattr(ops_mod, "_active_turns",
                        lambda: [{"id": 5, "session_id": "s"}])
    monkeypatch.setattr(ops_mod.urllib.request, "urlopen",
                        lambda req, timeout=0: "resp")
    out = ops_mod._graceful_stop_active(timeout_s=0)
    assert [t["id"] for t in out] == [5], "恒活跃超时必须上报 lingering"

    # 场景 B：HTTP 500 → 立即 lingering（stderr 有失败点名）
    def boom(req, timeout=0):                    # noqa: ANN001
        raise ue.HTTPError(req.full_url, 500, "boom", None, None)

    monkeypatch.setattr(ops_mod.urllib.request, "urlopen", boom)
    out = ops_mod._graceful_stop_active(timeout_s=1)
    assert [t["id"] for t in out] == [5]
    assert "HTTP 500" in capsys.readouterr().err
