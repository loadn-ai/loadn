"""loadn init：新用户安装向导（R9-1）。

三条命令起步：
    pip install "loadn[webui]"
    loadn-web init
    loadn-web serve

init 做什么：
  1. 创建数据根（~/.loadn-data/，含 var/workspace/config.yaml）
  2. 从 example 生成 config.yaml（引导用户填 LLM API key）
  3. 生成 server_token（API 认证）
  4. 检测可选依赖（bwrap/socat/node/claude CLI）并提示
  5. 输出 systemd unit 模板（填好 LOADN_WEBUI_HOME）
  6. 幂等：已初始化的目录跳过，不覆盖
"""
from __future__ import annotations

import os
import secrets
import shutil
import sys
from pathlib import Path

SYSTEMD_TEMPLATE = """\
# loadn systemd unit（由 `loadn-web init` 生成）
# 安装：sudo cp /tmp/loadn.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl start loadn
[Unit]
Description=loadn webui - multi-agent task platform
After=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={code_root}
ExecStart={python} -m loadn_webui serve
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillMode=process
Environment=PATH={path}
Environment=HOME={home}
Environment=LOADN_WEBUI_HOME={data_root}

[Install]
WantedBy=multi-user.target
"""

CONFIG_TEMPLATE = """\\
# loadn 配置（loadn-web init 生成）——编辑后重启服务生效
# 完整字段见 config.yaml.example

server:
  host: 127.0.0.1
  port: 8792
  # token 已自动生成（var/server_token）；轮换：loadn-web token rotate

# LLM 引擎配置（必填——不填无法启动引擎 turn）
engines:
  default: loadn
  loadn:
    # OpenAI 兼容端点（如 GLM/Doubao/DeepSeek/本地 vLLM）
    # 也支持 Anthropic 原生端点——见 README.md
    # provider: openai_compat
    # model: glm-4.6
    # base_url: https://open.bigmodel.cn/api/paas/v4
    # api_key: YOUR_KEY_HERE

security:
  # 全部有安全默认值——不需要可整段删掉
  # 档位枚举：off（直跑）| bwrap（Linux 隔离）| vm-bwrap（桌面 VM 执行域，
  # loadn desktop 置备）| seatbelt/appcontainer/remote（占位，未实现）
  sandbox: bwrap
  egress_mode: enforce  # enforce=白名单 | warn=只记录
"""


def cmd_init(data_root: str = "", *, force: bool = False) -> int:
    """初始化数据根。data_root 缺省 ~/.loadn-data。"""
    root = Path(data_root).expanduser().resolve() if data_root else (
        Path(os.environ.get("LOADN_WEBUI_HOME") or Path.home() / ".loadn-data")
    ).resolve()

    print("loadn init")
    print(f"  数据根: {root}")
    print()

    # 幂等检查
    marker = root / "var" / ".loadn-init-done"
    if marker.exists() and not force:
        print("✓ 已初始化（--force 重新生成 config/token）")
        _print_next_steps(root)
        return 0

    # 1) 目录结构
    dirs = ["var", "var/logs", "var/run", "workspace", "skills", "profiles",
            "prompts"]
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    print("✓ 目录结构")

    # 2) config.yaml
    cfg = root / "config.yaml"
    if not cfg.exists() or force:
        cfg.write_text(CONFIG_TEMPLATE)
        print(f"✓ config.yaml（填 LLM API key 后可用——{cfg}）")
    else:
        print("✓ config.yaml 已存在（跳过）")

    # 3) server_token
    token_file = root / "var" / "server_token"
    if not token_file.exists() or force:
        token = secrets.token_urlsafe(32)
        token_file.write_text(token + "\n")
        os.chmod(token_file, 0o600)
        print("✓ API token 已生成（var/server_token）")
    else:
        print("✓ server_token 已存在（跳过）")

    # 4) 可选依赖检测
    print()
    print("可选依赖检测：")
    checks = [
        ("bwrap", "文件系统沙箱（security.sandbox=bwrap 需要）",
         shutil.which("bwrap")),
        ("socat", "网络隔离桥（unshare-net 需要）", shutil.which("socat")),
        ("node", "claude CLI / opencode 引擎需要", shutil.which("node")),
        ("claude", "claude CLI 引擎", shutil.which("claude")),
        ("opencode", "opencode 引擎", shutil.which("opencode")),
    ]
    for name, desc, found in checks:
        status = "✓" if found else "—（可选）"
        print(f"  {name:10s} {status}  {desc}")
    if not shutil.which("bwrap"):
        print("  提示：apt install bubblewrap wrapexec  # 启用沙箱")

    # 5) systemd unit 模板
    code_root = Path(__file__).resolve().parent.parent
    python = sys.executable
    unit = SYSTEMD_TEMPLATE.format(
        user=os.environ.get("USER", "user"),
        code_root=code_root, python=python,
        path=os.environ.get("PATH", "/usr/bin:/bin"),
        home=str(Path.home()), data_root=root)
    unit_file = root / "var" / "loadn.service"
    unit_file.write_text(unit)
    print(f"\n✓ systemd unit 模板 → {unit_file}")

    # 6) 完成
    marker.write_text("ok\n")
    _print_next_steps(root)
    return 0


def _print_next_steps(root: Path) -> None:
    print()
    print("=" * 50)
    print("下一步：")
    print(f"  1. 编辑配置：vim {root}/config.yaml")
    print("     （填 LLM API key——engines.loadn 段）")
    print(f"  2. 前台测试：LOADN_WEBUI_HOME={root} loadn-web serve")
    print("     浏览器打开 http://127.0.0.1:8792")
    print(f"  3. 生产部署：sudo cp {root}/var/loadn.service"
          " /etc/systemd/system/")
    print("     sudo systemctl daemon-reload && sudo systemctl start loadn")
    print("=" * 50)
