#!/usr/bin/env python3
"""图标全家生成器：从母本 512 PNG 重做 PWA/favicon 全套。

母本来源：release/mac/workdaddy.app 的 AppIcon.icns（ic09 512px，蓝→紫渐变 W，
2026-09-03 老版样式——用户钦点比 09-04 靛蓝版好看）。归档在 scripts/assets/icon-master-512.png。

产物（frontend/public/icons/）：
  - icon-192.png / icon-512.png     圆角透明（PWA 标准，直接缩放）
  - apple-touch-icon.png 180        全幅不透明（iOS 自己套圆角，透明角会露底色）
  - icon-maskable-512.png           全幅渐变底 + 母本 90% 居中（Android 任意形状裁切）

全幅化用「alpha 边缘扩散填充」：把圆角外的透明区用邻近渐变色外推补齐，
比放大裁切稳（macOS 圆角半径 ~15%，放大法要 >30% 才能盖住）。

用法：python3 scripts/gen_icons.py [master.png]
"""
import pathlib
import sys

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
MASTER = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'scripts/assets/icon-master-512.png'
OUT = ROOT / 'frontend/public/icons'


def shift_sum(ch: np.ndarray, k: np.ndarray):
    """3x3 邻域内已知像素的颜色和 + 权重和（零填充，不卷绕）"""
    pad_ch = np.pad(ch, 1)
    pad_k = np.pad(k, 1)
    s = np.zeros_like(ch)
    w = np.zeros_like(ch)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            s += pad_ch[dy:dy + ch.shape[0], dx:dx + ch.shape[1]]
            w += pad_k[dy:dy + k.shape[0], dx:dx + k.shape[1]]
    return s, w


def fullbleed(im: Image.Image) -> Image.Image:
    """圆角透明 → 全幅不透明：迭代向外扩散填充透明区颜色"""
    a = np.asarray(im.convert('RGBA')).astype(np.float32)
    known = a[..., 3] >= 250
    rgb = a[..., :3].copy()
    rgb[~known] = 0
    while not known.all():
        k = known.astype(np.float32)          # 每轮用最新已知集，扩散环才能外推
        for c in range(3):
            s, w = shift_sum(rgb[..., c] * k, k)
            new = ~known & (w > 0)
            rgb[..., c][new] = s[new] / w[new]
        _, w = shift_sum(k, k)
        known |= w > 0
    out = np.dstack([rgb, np.full(rgb.shape[:2], 255.0)]).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


def main():
    master = Image.open(MASTER).convert('RGBA')
    assert master.size == (512, 512), f'母本应为 512x512，实际 {master.size}'
    OUT.mkdir(parents=True, exist_ok=True)

    master.resize((192, 192), Image.LANCZOS).save(OUT / 'icon-192.png')
    master.save(OUT / 'icon-512.png')

    fb = fullbleed(master)
    fb.resize((180, 180), Image.LANCZOS).save(OUT / 'apple-touch-icon.png')

    # maskable：全幅渐变底 + 母本 90% 居中（内容收进 80% 安全区）
    msk = fb.copy()
    small = master.resize((460, 460), Image.LANCZOS)
    msk.paste(small, ((512 - 460) // 2, (512 - 460) // 2), small)
    msk.save(OUT / 'icon-maskable-512.png')

    print('生成：icon-192 / icon-512 / apple-touch-icon(180) / icon-maskable-512 →', OUT)


if __name__ == '__main__':
    main()
