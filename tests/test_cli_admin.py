"""cli.py 管理命令 dispatch 单测（54% 覆盖补强——不触外部资源服务的分支）。"""
from __future__ import annotations

import json

from loadn_webui import cli


def test_token_show(capsys, monkeypatch, tmp_path):
    from loadn_webui.config import PATHS
    monkeypatch.setitem(PATHS, "var", tmp_path / "var")
    (tmp_path / "var").mkdir()
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.server, "token", "t0")
    monkeypatch.setattr(CONFIG.server, "token_grace_until", 0.0)
    assert cli.main(["token", "show"]) == 0
    out = capsys.readouterr().out
    assert "t0" in out and "宽限" in out


def test_vault_verify_cli(capsys):
    rc = cli.main(["vault", "verify"])
    out = capsys.readouterr().out
    assert "encrypted" in out      # 空库也回结构（ok=False 是合法态）


def test_audit_tail_and_verify(capsys):
    from loadn_webui import audit
    audit.audit("anomaly", {"k": 1})
    assert cli.main(["audit", "tail", "-n", "3"]) == 0
    assert "anomaly" in capsys.readouterr().out
    assert cli.main(["audit", "verify"]) in (0, 1)


def test_audit_export_out(capsys, tmp_path):
    out = tmp_path / "audit.jsonl"
    assert cli.main(["audit", "export", "--out", str(out)]) == 0
    assert out.exists() and out.read_text().strip()


def test_policy_check_cli_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(
        json.dumps({"tool_name": "Bash",
                    "tool_input": {"command": "rm -rf /"}})))
    assert cli.main(["policy-check"]) == 2
    assert "policy:block" in capsys.readouterr().err


def test_kill_all_cli_offline(monkeypatch, tmp_path, capsys):
    """API 不可达 → 落 KILL_ALL 标记（离线熔断路径）。"""
    import httpx

    from loadn_webui.config import PATHS
    monkeypatch.setitem(PATHS, "run", tmp_path / "run")
    (tmp_path / "run").mkdir(exist_ok=True)
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(
                            httpx.ConnectError("down")))
    assert cli.main(["kill-all"]) == 0
    assert (tmp_path / "run" / "KILL_ALL").exists()
    assert "KILL_ALL" in capsys.readouterr().out
