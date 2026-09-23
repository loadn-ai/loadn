#!/bin/bash
# ext4 root 磁盘制作（mac VZ 启动件）：docker export 的 tar → 8G ext4 镜像。
# 需要 sudo loop mount（本机/CI linux runner 均可；mac 侧不做此步）。
set -euo pipefail
cd "$(dirname "$0")/.."               # desktop/
VER="${1:?用法: build-ext4.sh <version>}"
IMAGE="loadn-vm:$VER"
OUT="image/dist"
SIZE_GB="${ROOTFS_SIZE_GB:-8}"

TAR="$OUT/rootfs-$VER.tar"
docker export "$(docker create "$IMAGE")" > "$TAR" \
    && docker rm "$(docker ps -aq --filter ancestor="$IMAGE" --filter status=created | head -1)" >/dev/null 2>&1 || true

IMG="$OUT/rootfs-ext4-$VER.raw"
rm -f "$IMG"
truncate -s "${SIZE_GB}G" "$IMG"
mkfs.ext4 -q -F -L loadn-root -E lazy_itable_init=0 "$IMG"

MNT=$(mktemp -d)
sudo mount -o loop "$IMG" "$MNT"
trap 'sudo umount "$MNT" && rmdir "$MNT"' EXIT
sudo tar -xf "$TAR" -C "$MNT" --numeric-owner
rm -f "$TAR"

echo "[build-ext4] $VER → $(du -h "$IMG" | cut -f1)（gzip 打包）"
gzip -1 "$IMG"
