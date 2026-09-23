#!/bin/bash
# loadn desktop 执行域：服务启动器（容器 CMD / systemd ExecStart / WSL shell
# exec 三态同一路径）。幂等：确保安装 → 首启 config → exec serve。
#
# 首启 config 要点：
#   - server.host=0.0.0.0：WSL2 localhostForwarding / VZ vsock 中继两侧都要求
#     非 loopback 绑定；VM 网络仅宿主可达 + token 强制，无暴露面
#   - server.token：首启机生（无宽限期；桌面壳经 VM exec 通道读回注入 webview）
#   - security.sandbox=vm-bwrap：档位枚举的桌面 VM 标记值（执行语义=bwrap，
#     /api/health 与安全中心据此报告「VM 执行域」）
set -euo pipefail

DATA="${LOADN_WEBUI_HOME:-/home/loadn/.loadn-data}"

# root 运行时降权到 loadn（保持数据根属主一致；docker/chroot 验证态无该用户则原样跑）
if [ "$(id -un)" = "root" ] && id loadn >/dev/null 2>&1; then
    exec runuser -u loadn -- env \
        LOADN_WEBUI_HOME="$DATA" LOADN_PORT="${LOADN_PORT:-8792}" "$0"
fi

export LOADN_WEBUI_HOME="$DATA"
export PATH="/opt/loadn/current/.venv/bin:$PATH"
mkdir -p "$DATA"

/usr/local/sbin/loadn-vm-install.sh

# 首启 config（存在即不动——用户在 UI/手工的改动永远优先）
if [ ! -f "$DATA/config.yaml" ]; then
    TOKEN="$(/opt/loadn/current/.venv/bin/python -c \
        'import secrets; print(secrets.token_urlsafe(32))')"
    cat > "$DATA/config.yaml" <<EOF
# loadn desktop 执行域（首启生成；可自由修改，重启生效）
server:
  host: 0.0.0.0
  port: ${LOADN_PORT:-8792}
  token: "$TOKEN"
security:
  sandbox: vm-bwrap
  egress_mode: enforce
EOF
    chmod 0600 "$DATA/config.yaml"
    echo "[loadn-vm-start] 首启 config 已生成（token 见 $DATA/config.yaml）"
fi

exec /opt/loadn/current/.venv/bin/python -m loadn_webui serve \
    --host 0.0.0.0 --port "${LOADN_PORT:-8792}"
