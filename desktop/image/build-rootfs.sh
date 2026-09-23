#!/bin/bash
# rootfs 构建 + 双平台产物（本机/CI linux 通用）：
#   dist/rootfs-wsl-<ver>.tar.gz   → win `wsl --import`（WSL2，宿主内核）
#   dist/vmlinuz-<ver> / initrd-<ver>-<ver>.img / rootfs-ext4-<ver>.tar.gz
#                                  → mac VZ/vfkit（ext4 由 build-ext4.sh 从 tar 展开制作）
# mac ext4 需要 loop mount（sudo），独立脚本以便 CI 单独调用。
set -euo pipefail
cd "$(dirname "$0")/.."               # desktop/
VER="${LOADN_VERSION:-$(cd .. && git describe --tags --always)}"
IMAGE="loadn-vm:$VER"
OUT="image/dist"

echo "[build-rootfs] docker build $IMAGE"
docker build -f image/Dockerfile.rootfs \
    --build-arg LOADN_VERSION="$VER" -t "$IMAGE" image

mkdir -p "$OUT"
echo "[build-rootfs] docker export → WSL tarball"
CID=$(docker create "$IMAGE")
docker export "$CID" | gzip -1 > "$OUT/rootfs-wsl-$VER.tar.gz"
docker rm "$CID" >/dev/null

echo "[build-rootfs] 提取 VZ 启动件（vmlinuz/initrd）"
docker run --rm --entrypoint bash "$IMAGE" -c \
    'cat /boot/vmlinuz-*' > "$OUT/vmlinuz-$VER"
docker run --rm --entrypoint bash "$IMAGE" -c \
    'cat /boot/initrd.img-*' > "$OUT/initrd-$VER.img"

echo "[build-rootfs] ext4 root 磁盘（sudo loop mount）"
"image/build-ext4.sh" "$VER"

(cd "$OUT" && sha256sum "rootfs-wsl-$VER.tar.gz" "rootfs-ext4-$VER.tar.gz" \
    "vmlinuz-$VER" "initrd-$VER.img" > "SHA256SUMS-$VER.txt")
echo "[build-rootfs] 完成："
ls -lh "$OUT" | grep -E "$VER|total"
