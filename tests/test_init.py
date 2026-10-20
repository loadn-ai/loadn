"""loadn-web init 安装向导测试（R9-1）。"""
from __future__ import annotations

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


def test_r12_constitution_has_exec_env_block():
    """十二轮对赌（生产实证：直跑档位下模型选了 sandbox MCP 的 bash 进
    外置容器找不到宿主代码仓）：新建会话宪法必须带「执行环境与 Bash
    路标」节，且直跑档位下明确「内建 Bash=宿主 / mcp__sandbox__*=容器，
    读本机代码不用它」。"""
    from loadn_webui import profile as profile_mod
    from loadn_webui.workspace import render_claude_md
    prof = profile_mod.get("coder") or profile_mod.auto_match("整理代码")
    md = render_claude_md("test-r12", "架构整理", prof, [])
    assert "执行环境与 Bash 路标" in md
    assert "直跑档位" in md
    assert "mcp__sandbox__" in md and "内建 Bash" in md
    assert "绝不用它" in md          # 读本机代码不走 sandbox MCP


# ------------------------------------------------ init 批：model/端点参数
def test_init_with_model_params(tmp_path, capsys):
    """init --model/--base-url/--api-key → config.yaml 实值段（可设置 model）。"""
    from loadn_webui import init_cmd
    rc = init_cmd.cmd_init(str(tmp_path), model="glm-4.6",
                           base_url="https://open.bigmodel.cn/api/paas/v4",
                           api_key="sk-test-123")
    assert rc == 0
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "model: glm-4.6" in text
    assert "base_url: https://open.bigmodel.cn/api/paas/v4" in text
    assert "api_key: sk-test-123" in text


def test_init_params_skipped_when_config_exists(tmp_path, capsys):
    """已有 config 传参 → 明示未写入（不覆盖用户配置）。"""
    from loadn_webui import init_cmd
    init_cmd.cmd_init(str(tmp_path))
    (tmp_path / "var" / ".loadn-init-done").unlink()   # 撤幂等标记：验证 config 保护
    rc = init_cmd.cmd_init(str(tmp_path), model="glm-4.6")
    assert rc == 0
    out = capsys.readouterr().out
    assert "未写入" in out


def test_engine_env_injects_config_endpoints(tmp_path, monkeypatch):
    """engines.loadn.base_url/api_key（config.yaml）→ build_env 注入
    LOADN_BASE_URL/LOADN_API_KEY（引擎 provider_config 环境层消费）。"""
    from dataclasses import replace

    from loadn_webui import config as cfg_mod
    from loadn_webui.engines import loadn as eng_mod
    new_cfg = replace(
        cfg_mod.CONFIG,
        engines=replace(
            cfg_mod.CONFIG.engines,
            loadn=replace(cfg_mod.CONFIG.engines.loadn,
                          base_url="https://gw.example/v1",
                          api_key="sk-xyz")))
    monkeypatch.setattr(eng_mod, "CONFIG", new_cfg)   # 引擎模块 from-import 绑定
    env = eng_mod.LoadnSpec().build_env()
    assert env.get("LOADN_BASE_URL") == "https://gw.example/v1"
    assert env.get("LOADN_API_KEY") == "sk-xyz"
