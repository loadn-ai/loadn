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
    with pytest.raises(_Stop):
        ops_mod.cmd_upgrade("v9.9.9", wait_idle=0, yes=False)
    err = capsys.readouterr().err
    assert "跳过等待" in err and "打断" in err, "跳过等待必须有中断语义警告"
