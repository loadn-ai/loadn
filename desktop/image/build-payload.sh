#!/bin/bash
# 平台负载构建：仓库树 → desktop/image/payload/{release.tar.gz, wheelhouse/, VERSION}
# 与 ops.py release build 同构（git archive + ui/dist + requirements.prod.lock
# 离线 wheelhouse），但产物进 docker 构建上下文而非 /opt/loadn/releases。
set -euo pipefail
cd "$(dirname "$0")/../.."          # 仓库根

VER="${LOADN_VERSION:-$(git describe --tags --always)}"
OUT="${1:-desktop/image/payload}"
PIP_BIN="$( [ -x .venv/bin/pip ] && echo .venv/bin/pip || echo pip3 )"

if [ -n "$(git status --porcelain)" ]; then
    echo "[build-payload] ⚠ 工作树有未提交改动——release.tar.gz 只含 HEAD（提交后再构建可打包当前改动）" >&2
fi

echo "[build-payload] 版本 $VER → $OUT"
rm -rf "$OUT"
mkdir -p "$OUT/wheelhouse" /tmp/loadn-payload-stage

echo "[build-payload] 1/3 git archive HEAD"
git archive HEAD | tar -x -C /tmp/loadn-payload-stage

echo "[build-payload] 2/3 ui build（dist 并入 release）"
( cd ui && npm ci --silent && npm run build >/dev/null )
rm -rf /tmp/loadn-payload-stage/ui/dist
cp -r ui/dist /tmp/loadn-payload-stage/ui/dist

echo "[build-payload] 3/3 wheelhouse 离线轮子"
"$PIP_BIN" download -q -r requirements.prod.lock -d "$OUT/wheelhouse"

tar -czf "$OUT/release.tar.gz" -C /tmp/loadn-payload-stage .
echo "$VER" > "$OUT/VERSION"
rm -rf /tmp/loadn-payload-stage

echo "[build-payload] 完成：$(du -sh "$OUT" | cut -f1) $OUT"
