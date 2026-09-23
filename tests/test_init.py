"""loadn-web init 安装向导测试（R9-1）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadn_webui import init_cmd


def test_init_creates_structure(tmp_path):
    """init：目录+config+token+systemd unit 全到位。"""
    root = tmp_path / "data"
    assert init_cmd.cmd_init(str(root)) == 0
    # 目录结构
    for d in ("var", "var/logs", "var/run", "workspace", "skills",
              "profiles", "prompts"):
        assert (root / d).is_dir(), d
    # config.yaml 含引导注释
    cfg = (root / "config.yaml").read_text()
    assert "engines" in cfg and "api_key" in cfg
    # token
    token = (root / "var" / "server_token").read_text().strip()
    assert len(token) >= 30
    assert (root / "var" / "server_token").stat().st_mode & 0o777 == 0o600
    # systemd unit
    unit = (root / "var" / "loadn.service").read_text()
    assert "LOADN_WEBUI_HOME" in unit and "loadn_webui serve" in unit
    # 完成标记
    assert (root / "var" / ".loadn-init-done").exists()


def test_init_idempotent(tmp_path):
    """init 已初始化的目录：跳过不覆盖。"""
    root = tmp_path / "data"
    init_cmd.cmd_init(str(root))
    token1 = (root / "var" / "server_token").read_text()
    cfg1 = (root / "config.yaml").read_text()
    # 二次 init
    assert init_cmd.cmd_init(str(root)) == 0
    assert (root / "var" / "server_token").read_text() == token1
    assert (root / "config.yaml").read_text() == cfg1


def test_init_force_regenerates(tmp_path):
    """init --force：重新生成 config/token。"""
    root = tmp_path / "data"
    init_cmd.cmd_init(str(root))
    token1 = (root / "var" / "server_token").read_text()
    assert init_cmd.cmd_init(str(root), force=True) == 0
    token2 = (root / "var" / "server_token").read_text()
    assert token1 != token2       # --force 重新生成
