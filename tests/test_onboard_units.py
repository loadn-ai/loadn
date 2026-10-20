"""首跑引导（onboard）单测：纯函数判定矩阵 + config 合并写 + 交互全流程。

交互流程经 monkeypatch input/getpass 驱动（无 tty 依赖）；provider_config
的兜底源（~/.claude/settings.json、env）由 HOME/monkeypatch.delenv 隔离
——防本机真实配置渗入断言（conftest 只隔了 LOADN_HOME，没隔 claude 源）。
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from loadn.cli import onboard

# ---------------- 纯函数 ----------------

def test_mask_key_shapes():
    assert onboard.mask_key("") == "（未设）"
    assert onboard.mask_key("short") == "****"          # ≤12 全打码
    assert onboard.mask_key("sk-1234567890abcdef") == "sk-1…cdef"


def test_needs_onboarding_matrix():
    full = {"base_url": "https://x", "api_key": "k"}
    partial = {"base_url": "https://x", "api_key": ""}
    # marker 在 → 永不引导（配置缺也不问——已引导过）
    assert onboard.needs_onboarding(full, True) is False
    assert onboard.needs_onboarding(partial, True) is False
    # marker 无 → 看缺口
    assert onboard.needs_onboarding(full, False) is False       # 已配置
    assert onboard.needs_onboarding(partial, False) is True
    assert onboard.needs_onboarding({}, False) is True
    # fake provider（测试/回放通道）无需端点——不引导（TUI e2e 回归实证）
    assert onboard.needs_onboarding({"provider": "fake"}, False) is False


def test_presets_contract():
    """预设表可引导性：键唯一、provider 合法、除自定义外端点模型齐。"""
    keys = [p["key"] for p in onboard.PRESETS]
    assert keys == [str(i) for i in range(1, len(onboard.PRESETS) + 1)]
    for p in onboard.PRESETS:
        assert p["provider"] in ("anthropic", "openai_compat")
        if p["key"] != "6":   # 自定义=手填，允许空
            assert p["base_url"].startswith("https://")
            assert p["model"]


# ---------------- merge_write_config ----------------

def test_merge_write_creates_0600(tmp_path: Path):
    p = onboard.merge_write_config(tmp_path, {
        "provider": "openai_compat", "base_url": "https://api.x",
        "api_key": "sk-1", "model": "m1"})
    data = json.loads(p.read_text())
    assert data["model"] == "m1" and data["provider"] == "openai_compat"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert not (tmp_path / "config.json.tmp").exists()   # 原子写不留残骸


def test_merge_write_preserves_and_partial(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"small_model": "keep-me", "extra": {"a": 1},
                               "model": "old"}))
    onboard.merge_write_config(tmp_path, {"model": "new", "api_key": ""})
    data = json.loads(cfg.read_text())
    assert data["model"] == "new"            # 非空键更新
    assert "api_key" not in data             # 空键不落
    assert data["small_model"] == "keep-me"  # 既有键不碰
    assert data["extra"] == {"a": 1}


def test_merge_write_survives_corrupt_file(tmp_path: Path):
    (tmp_path / "config.json").write_text("{not yaml or json")
    p = onboard.merge_write_config(tmp_path, {"model": "m"})
    assert json.loads(p.read_text())["model"] == "m"


# ---------------- run_onboarding 交互全流程 ----------------

@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离 provider_config 的全部现实源：HOME+LOADN_HOME 指临时根、
    清 ANTHROPIC_*/LOADN_* 端点密钥变量（LOADN_HOME conftest 已设，但
    覆盖成每用例独立目录防 marker/config 串档）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOADN_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    for k in list(os.environ):
        if k.startswith(("ANTHROPIC_", "LOADN_BASE", "LOADN_API", "LOADN_MODEL",
                         "LOADN_PROVIDER")):
            monkeypatch.delenv(k, raising=False)
    return home


def _drive(monkeypatch, answers: list[str], key: str = "sk-test-123456"):
    """问答驱动：answers 依序喂 input()；getpass 恒返回 key。"""
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *_: next(it, ""))
    monkeypatch.setattr(onboard.getpass, "getpass",
                        lambda *_: key)


def test_onboard_full_flow_writes_config(isolated_env: Path,
                                         monkeypatch: pytest.MonkeyPatch,
                                         capsys):
    _drive(monkeypatch, ["1", "", "", ""])     # 预设1 → base_url/model 回车默认
    assert onboard.run_onboarding() == 0
    cfg = json.loads((isolated_env / "config.json").read_text())
    assert cfg["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert cfg["api_key"] == "sk-test-123456"
    assert cfg["model"] == "glm-4.6"
    assert cfg["provider"] == "openai_compat"
    assert (isolated_env / ".onsite_done").exists()
    out = capsys.readouterr().out
    assert "已写入" in out and "sk-test-123456" not in out   # 密钥不回显正文


def test_onboard_jump_writes_marker_only(isolated_env: Path,
                                         monkeypatch: pytest.MonkeyPatch):
    _drive(monkeypatch, ["s"], key="")
    assert onboard.run_onboarding() == 0
    assert not (isolated_env / "config.json").exists()
    assert (isolated_env / ".onsite_done").exists()


def test_onboard_skip_default_and_custom_url(isolated_env: Path,
                                             monkeypatch: pytest.MonkeyPatch):
    _drive(monkeypatch, ["6", "https://my.vllm:8000/v1", "my-model"],
           key="k-1234567890abcdef")
    assert onboard.run_onboarding() == 0
    cfg = json.loads((isolated_env / "config.json").read_text())
    assert cfg["base_url"] == "https://my.vllm:8000/v1"
    assert cfg["model"] == "my-model"
    assert cfg["provider"] == "openai_compat"


def test_onboard_configured_no_ask(isolated_env: Path,
                                   monkeypatch: pytest.MonkeyPatch, capsys):
    """marker 无但配置齐（env 已带）→ 零问询，补 marker 直接返回。"""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gw")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-existing-xyz")
    called = []
    monkeypatch.setattr("builtins.input", lambda *a: called.append(a) or "")
    assert onboard.run_onboarding() == 0
    assert not called                      # 未问任何问题
    assert (isolated_env / ".onsite_done").exists()
    assert "已配置" in capsys.readouterr().out


def test_onboard_force_asks_even_configured(isolated_env: Path,
                                            monkeypatch: pytest.MonkeyPatch):
    onboard.write_marker()
    _drive(monkeypatch, ["2", "", "", ""])
    assert onboard.run_onboarding(force=True) == 0
    cfg = json.loads((isolated_env / "config.json").read_text())
    assert cfg["base_url"] == "https://api.deepseek.com"


def test_maybe_onboard_respects_marker_and_tty(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    # marker 在 → 零输出零动作
    onboard.write_marker()
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda s: True})())
    onboard.maybe_onboard()
    assert capsys.readouterr().out == ""
    # marker 无 + 未配置 + 非 tty → 跳过（headless/管道不引导）
    (isolated_env / ".onsite_done").unlink()
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda s: False})())
    onboard.maybe_onboard()
    assert capsys.readouterr().out == ""
    assert not (isolated_env / ".onsite_done").exists()
