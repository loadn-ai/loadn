#!/bin/bash
# loadn desktop 执行域：payload → /opt/loadn 幂等安装器。
# 布局与 ops.py release 模型同构（releases/<ver> + current 符号链 + editable
# 安装——sandbox.py 对 editable 源码树的 ro-bind 挂载因此与生产逐字节一致）。
# 三态通用：VZ systemd 首启 / WSL2 shell exec / 本机 docker|chroot 验证。
#
# 关键不变量：editable 安装必须**就地**进行——`pip install -e` 把目录绝对路径
# 写进 venv 的 .pth，装完再 mv 会把指针弄断。所以解压直接落在最终 releases/
# <ver>，原子性由 current 符号链 rename(2) 保证（ops.py 同款）；半程崩溃由
# `.installed` 哨兵识别并重来。
#
# payload 布局（build-payload.sh 产出，Dockerfile COPY 进 /opt/loadn-payload）：
#   release.tar.gz   git archive 仓库树 + ui/dist
#   wheelhouse/      requirements.prod.lock 离线轮子
#   VERSION          版本串（tag 或 dev-<sha>）
set -euo pipefail

PAYLOAD="${LOADN_PAYLOAD_DIR:-/opt/loadn-payload}"
DEPLOY="${LOADN_DEPLOY_ROOT:-/opt/loadn}"
VER="$(cat "$PAYLOAD/VERSION")"
REL_DIR="$DEPLOY/releases/$VER"

log() { echo "[loadn-vm-install] $*"; }

if [ -e "$REL_DIR/.installed" ] && [ -L "$DEPLOY/current" ] \
   && [ "$(readlink "$DEPLOY/current")" = "releases/$VER" ]; then
    log "已安装 $VER（current 指向一致），跳过"
    exit 0
fi

log "安装 $VER → $DEPLOY/releases/$VER"
mkdir -p "$DEPLOY/releases"
# 半程残留（无 .installed）或旧版本目录：清掉重来
rm -rf "$REL_DIR"
mkdir -p "$REL_DIR"
tar -xzf "$PAYLOAD/release.tar.gz" -C "$REL_DIR"

# venv 离线装配（镜像 ops.py：先工具链后 lock 后 editable 本体——全就地）
python3 -m venv "$REL_DIR/.venv"
PIP=("$REL_DIR/.venv/bin/pip" install --no-index --find-links "$PAYLOAD/wheelhouse")
"${PIP[@]}" setuptools wheel pip >/dev/null
"${PIP[@]}" -r "$REL_DIR/requirements.prod.lock" >/dev/null
(cd "$REL_DIR" && "${PIP[@]}" -e . >/dev/null)

# RELEASE.json（R7：/api/health 回显版本；source 标记桌面负载来源）
printf '{"version": "%s", "source": "desktop-payload"}\n' "$VER" \
    > "$REL_DIR/RELEASE.json"

touch "$REL_DIR/.installed"
# 原子切换：current 符号链 rename(2)
ln -sfn "releases/$VER" "$DEPLOY/.current.tmp"
mv -T "$DEPLOY/.current.tmp" "$DEPLOY/current"
chmod -R a+rX "$DEPLOY/releases/$VER"
log "完成：$DEPLOY/current → releases/$VER"
