"""C2 前半对抗用例（§8）：执行域与盘面零平台资源秘密（W3.1/3.3）。

完整 C2（沙箱不挂载+policy deny）随 W2 落地；本版锁定：
- spawn env 白名单：宿主历史泄漏（假想的 AWS/GITHUB/任意 SECRET）不进引擎子进程
- vault 盘面：vault.enc 格式正确、无明文残留、密钥文件 0600
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("sec.c2")]

from loadn_webui.claude_runner import _spawn_env
from loadn_webui.security import vault as vault_mod


def test_c2_env_whitelist_blocks_host_leaks(monkeypatch):
    """宿主 env 泄漏不继承：只白名单前缀/精确名进子进程。"""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIA-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-leak")
    monkeypatch.setenv("DUMMY_EVIL_SECRET", "leak")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    env = _spawn_env({"LOADN_STEER_FILE": "/x"}, {"LOADN_SESSION_ID": "s1"})
    for k in ("AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "DUMMY_EVIL_SECRET",
              "OPENAI_API_KEY"):
        assert k not in env, f"{k} 泄入子进程"
    assert env["LOADN_STEER_FILE"] == "/x"      # spec env 显式注入仍可达
    assert env["LOADN_SESSION_ID"] == "s1"
    assert "PATH" in env and "HOME" in env      # 基础运行时在


def test_c2_env_whitelist_keeps_engine_creds_m1(monkeypatch):
    """M1 过渡（known-gap）：LLM 引擎凭证白名单内（M2 代理接管后收回）。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gw")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "{}")
    env = _spawn_env({}, {})
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-x"
    assert env["OPENCODE_CONFIG_CONTENT"] == "{}"


def test_c2_vault_encrypted_at_rest(tmp_path, monkeypatch):
    """vault 落盘零明文：LDV1 头 + 内容不含任何字段值；密钥 0600。"""
    monkeypatch.setenv("LOADN_WEBUI_HOME", str(tmp_path))
    from loadn_webui.config import PATHS
    monkeypatch.setitem(PATHS, "var", tmp_path / "var")
    PATHS["var"].mkdir(parents=True, exist_ok=True)
    # 绕过惰性迁移直接写加密库
    vault_mod.save({"topsecret": {"username": "alice", "password": "hunter2-九头蛇"}})
    enc = (PATHS["var"] / "vault.enc").read_bytes()
    assert enc[:4] == b"LDV1"
    assert b"alice" not in enc and b"hunter2" not in enc and "九头蛇".encode() not in enc
    assert oct((PATHS["var"] / ".vault_key").stat().st_mode)[-3:] == "600"
    assert not (PATHS["var"] / "vault.json").exists()   # 无明文残留
    # 回读一致
    assert vault_mod.load()["topsecret"]["password"] == "hunter2-九头蛇"


def test_c2_vault_legacy_migration_wipes_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_WEBUI_HOME", str(tmp_path))
    from loadn_webui.config import PATHS
    monkeypatch.setitem(PATHS, "var", tmp_path / "var")
    PATHS["var"].mkdir(parents=True, exist_ok=True)
    (PATHS["var"] / "vault.json").write_text(
        '{"legacy": {"password": "oldpass"}}')
    data = vault_mod.load()
    assert data["legacy"]["password"] == "oldpass"
    assert not (PATHS["var"] / "vault.json").exists()   # 三次覆写后删除
    assert vault_mod.verify()["ok"]
