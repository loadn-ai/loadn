#!/usr/bin/env bash
# AIO 沙箱（火山 vefaaS all-in-one-sandbox）部署脚本——workdaddy 浏览器能力的底座。
#
# 来历：2026-09-03 首次部署时用的是 `--rm -it` 前台命令，2026-09-10 机器重启
# 容器被连带删除，21111 无人监听、平台真浏览器/CDP/MCP 静默瘫痪两天才被发现。
# 现固化为 -d --restart unless-stopped：重启自动回来；本脚本幂等可重复跑。
#
# 注意：
# - 浏览器登录态存在容器文件系统里——重启机器不丢，docker rm 才丢（别删）。
# - 绑定收紧在 127.0.0.1（早期 -p 21111:8080 是全网卡暴露，key 又弱）。
# - 健康验证：python3 bin/wd.py r ping --only sandbox,sandbox_mcp
set -euo pipefail

NAME=aio-sandbox
PORT=127.0.0.1:21111:8080
KEY=***REDACTED***
IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0

if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "$NAME 已在运行"; exit 0
fi
docker rm -f "$NAME" >/dev/null 2>&1 || true   # 清掉退出/创建失败的残骸（会丢旧登录态）

docker run -d --name "$NAME" --restart unless-stopped \
    --security-opt seccomp=unconfined \
    -e SANDBOX_API_KEY="$KEY" \
    -p "$PORT" \
    "$IMAGE"

echo "已启动（浏览器全家桶初始化约 1-2 分钟），稍后验证："
echo "  python3 bin/wd.py r ping --only sandbox,sandbox_mcp"
