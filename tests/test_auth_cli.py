"""P3-5b loadn auth 验收。

- login（getpass 注入）→ auth.json 0600 原子写；**挂载面扫描零命中**
  （cwd/workspace/transcript 全扫 token 无一处命中——凭据不出现在
  任何会话可见面）
- provider_config 解析链接入：auth.json 条目生效；config.json 仍最高优
- logout 删条目；status 打码（明文 token 不出现在输出）
- check --credentials 导出 JSON（脚本消费面）；无凭据 rc=1
- vault 同步：有 loadn-web → subprocess 带对参数（mock 不真写）；
  无 loadn-web → 静默跳过不炸
"""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from loadn.cli import auth as A


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """引擎 HOME 隔离 + 摘掉真实 vault/claude 设置的干扰。

    ws = 挂载面形态目录（workspace）；home = 引擎侧（不进挂载）。
    Path.home 指到空假 HOME：~/.claude/settings.json 的真实网关配置
    不泄进测试断言。
    """
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("LOADN_HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
              "ANTHROPIC_BASE_URL", "LOADN_API_KEY", "LOADN_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(A, "_loadn_web_bin", lambda: None)   # 默认无平台
    return home


def _login(monkeypatch, provider="anthropic", token="sk-test-abcdef1234",
           base_url=""):
    monkeypatch.setattr(A.getpass, "getpass", lambda *a: token)
    rc = A.main(["login", provider] + (["--base-url", base_url]
                                       if base_url else []))
    assert rc == 0


# ---------------------------------------------------------------- 存储
def test_login_writes_0600_and_masked_output(tmp_path, monkeypatch, capsys):
    _login(monkeypatch)
    p = A.auth_path()
    assert p.exists()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600                                  # 0600
    data = json.loads(p.read_text())
    assert data["providers"]["anthropic"]["auth_token"] == "sk-test-abcdef1234"
    out = capsys.readouterr().out
    assert "sk-test-abcdef1234" not in out                # 输出打码
    assert "sk-…" in out or "sk-" in out
    assert not p.with_suffix(".json.tmp").exists()        # 原子写无残渣


def test_mount_surface_scan_zero_hit(tmp_path, monkeypatch):
    """验收主项：登录后挂载面（workspace 形态目录）扫描 token 零命中。"""
    _login(monkeypatch, token="sk-MOUNTSECRET-xyz999")
    ws = tmp_path / "ws"                                  # 挂载面 = workspace
    (ws / "CLAUDE.md").write_text("宪法\n", encoding="utf-8")
    (ws / ".loadn").mkdir(exist_ok=True)
    (ws / ".loadn" / "settings.json").write_text("{}", encoding="utf-8")
    (ws / ".mcp.json").write_text("{}", encoding="utf-8")
    hits = [p for p in ws.rglob("*") if p.is_file()
            and "MOUNTSECRET" in p.read_text(errors="replace")]
    assert hits == []                                     # 挂载面零命中
    # 全 tmp 范围：auth.json 是唯一持有者（引擎 HOME 在挂载面之外）
    holders = [p for p in tmp_path.rglob("*")
               if p.is_file() and "MOUNTSECRET" in p.read_text(errors="replace")]
    assert holders == [A.auth_path()]


def test_empty_token_rejected(monkeypatch, capsys):
    monkeypatch.setattr(A.getpass, "getpass", lambda *a: "")
    assert A.main(["login", "anthropic"]) == 1
    assert not A.auth_path().exists()


# ---------------------------------------------------------------- 解析链
def test_provider_config_reads_auth(tmp_path, monkeypatch):
    _login(monkeypatch, base_url="https://gw.example/v1")
    from loadn.providers import provider_config
    cfg = provider_config()
    assert cfg["api_key"] == "sk-test-abcdef1234"
    assert cfg["base_url"] == "https://gw.example/v1"


def test_config_json_still_wins(tmp_path, monkeypatch):
    _login(monkeypatch, token="sk-auth-side")
    (tmp_path / "home" / "config.json").write_text(
        '{"api_key": "sk-config-side"}', encoding="utf-8")
    from loadn.providers import provider_config
    assert provider_config()["api_key"] == "sk-config-side"


def test_provider_named_entry_only(tmp_path, monkeypatch):
    """auth.json 只吃与 provider 同名条目（openai 条目不串到 anthropic）。"""
    _login(monkeypatch, provider="openai", token="sk-oai-only")
    from loadn.providers import provider_config
    cfg = provider_config()
    assert cfg.get("api_key") != "sk-oai-only"            # 不串味


# ---------------------------------------------------------------- 生命周期
def test_logout_and_status(tmp_path, monkeypatch, capsys):
    _login(monkeypatch)
    _login(monkeypatch, provider="openai")
    rc = A.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0 and "anthropic" in out and "openai" in out
    assert "sk-test-abcdef1234" not in out                # status 永远打码
    assert A.main(["logout", "anthropic"]) == 0
    assert "anthropic" not in json.loads(A.auth_path().read_text())["providers"]
    assert A.main(["logout", "anthropic"]) == 1           # 未配置


def test_check_credentials_export(tmp_path, monkeypatch, capsys):
    _login(monkeypatch, base_url="https://gw.example/v1")
    assert A.main(["check", "anthropic", "--credentials"]) == 0
    d = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert d["auth_token"] == "sk-test-abcdef1234"        # 明文导出（脚本面）
    assert d["base_url"] == "https://gw.example/v1"
    # 无凭据：rc=1 + 可读提示
    monkeypatch.setattr(A, "read_auth", lambda: {})
    assert A.main(["check", "anthropic", "--credentials"]) == 1


def test_refresh_same_path(tmp_path, monkeypatch, capsys):
    _login(monkeypatch, token="sk-old-token-1111")
    _login(monkeypatch, token="sk-new-token-2222")        # refresh=login 同路
    data = json.loads(A.auth_path().read_text())
    assert data["providers"]["anthropic"]["auth_token"] == "sk-new-token-2222"


# ---------------------------------------------------------------- vault 同步
def test_vault_sync_invokes_loadn_web(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(A, "_loadn_web_bin", lambda: "/bin/loadn-web")
    monkeypatch.setattr(A.subprocess, "run", fake_run)
    ok = A.sync_vault("Anthropic", "sk-tok")
    assert ok is True
    assert calls == [["/bin/loadn-web", "r", "account",
                      "--platform", "llm-anthropic",
                      "--set", "password=sk-tok"]]        # 大小写归一


def test_vault_sync_silent_without_bin(monkeypatch):
    monkeypatch.setattr(A, "_loadn_web_bin", lambda: None)
    assert A.sync_vault("anthropic", "sk") is False       # 静默降级
