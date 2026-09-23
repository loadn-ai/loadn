#!/bin/bash
# rootfs 本机全链验证（无 mac/win 也能验「VM 执行域」镜像契约）：
# docker --privileged 作为 VM 替身（bwrap user namespace 需特权放行；真实
# WSL2/VZ 内核原生允许）——起服务 → 等健康 → 档位断言 → doctor → token
# 通道 → 带 token 建/查会话。这是 desktop 侧唯一的本机可执行验收。
#
# W0 之下 /api/health 同样要鉴权：等待循环以「任意 HTTP 响应（含 401）」为
# 已起信号，契约断言全部带 token。
set -euo pipefail
cd "$(dirname "$0")/.."               # desktop/
VER="${LOADN_VERSION:-$(cd .. && git describe --tags --always)}"
IMAGE="loadn-vm:$VER"
NAME="loadn-vm-verify"
PORT="${VERIFY_PORT:-18792}"

docker rm -f "$NAME" >/dev/null 2>&1 || true
echo "[verify] 启动 $IMAGE（--privileged：容器内 bwrap userns）→ 127.0.0.1:$PORT"
docker run -d --name "$NAME" --privileged -p "$PORT:8792" "$IMAGE" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null' EXIT

# 等服务起（首启含 venv 离线装配，给足 300s；401 也算「已起」——W0 生效即拒匿名）
# 注意 -w 在连接失败时也输出 000，不能再 || echo 叠加（000000 会假通过）
ok=0
for i in $(seq 1 150); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)
    if [ "$code" = "200" ] || [ "$code" = "401" ]; then ok=1; break; fi
    sleep 2
done
[ "$ok" = 1 ] || { echo "FAIL: 健康检查 300s 超时"; docker logs "$NAME" | tail -30; exit 1; }

echo "[verify] 0/5 token 通道（模拟桌面壳经 exec 读回注入）"
TOK=""
for i in $(seq 1 30); do
    TOK=$(docker exec "$NAME" sh -c \
        "sed -n 's/^  token: \"\(.*\)\"/\1/p' /home/loadn/.loadn-data/config.yaml" 2>/dev/null || true)
    [ -n "$TOK" ] && break
    sleep 2
done
[ -n "$TOK" ] || { echo "FAIL: 首启 config 未含 token"; docker logs "$NAME" | tail -20; exit 1; }

echo "[verify] 1/5 /api/health 契约（带 token）"
curl -sf "http://127.0.0.1:$PORT/api/health" -H "X-Loadn-Token: $TOK" > /tmp/vm-health.json
python3 - <<'EOF'
import json
d = json.load(open("/tmp/vm-health.json"))
sbx, rel = d["sandbox"], d.get("release", {})
assert sbx["requested"] == "vm-bwrap", sbx
assert sbx["effective"] == "vm-bwrap", f"档位降档：{sbx}"
assert rel.get("version"), rel
eng = d["engines"]
assert eng["claude"]["ok"], f"claude 引擎不可用：{eng['claude']}"
assert eng["loadn"]["ok"], f"loadn 引擎不可用：{eng['loadn']}"
print(f"  ok release={rel.get('version')} sandbox={sbx['effective']}"
      f" claude={str(eng['claude'].get('version'))[:24]}")
EOF

echo "[verify] 2/5 VM 内 doctor（以服务用户与数据根——exec 通道默认不继承服务 env）"
docker exec -u loadn -e LOADN_WEBUI_HOME=/home/loadn/.loadn-data \
    "$NAME" /opt/loadn/current/.venv/bin/loadn-web doctor | grep -E "bwrap|claude:|loadn:|profiles|prompts|skills|result" | head -10

echo "[verify] 3/5 带鉴权建会话 + 查详情"
SID=$(curl -sf -X POST "http://127.0.0.1:$PORT/api/sessions" \
    -H "X-Loadn-Token: $TOK" -H "Content-Type: application/json" \
    -d '{"title": "verify-local"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -sf "http://127.0.0.1:$PORT/api/sessions/$SID" -H "X-Loadn-Token: $TOK" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["id"], d; print(f"  ok sid={d[\"id\"]}")'
curl -sf -X DELETE "http://127.0.0.1:$PORT/api/sessions/$SID?purge=1" \
    -H "X-Loadn-Token: $TOK" -H "X-Loadn-Admin: $TOK" >/dev/null

echo "[verify] 4/5 安全中心档位三态"
curl -sf "http://127.0.0.1:$PORT/api/admin/security" -H "X-Loadn-Token: $TOK" \
    | python3 -c 'import json,sys; s=json.load(sys.stdin)["sandbox"]; assert s["requested"]=="vm-bwrap" and s["effective"]=="vm-bwrap", s; print(f"  ok posture={s[\"requested\"]}")'

echo "[verify] 5/5 匿名拒绝（W0 生效证明）"
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/sessions")
[ "$code" = "401" ] && echo "  ok 匿名 401" || { echo "FAIL: 匿名请求未拒（$code）"; exit 1; }

echo "[verify] ✅ 全链通过：镜像可作为 win/mac 执行域分发"
