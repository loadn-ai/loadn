#!/usr/bin/env python3
"""models.json 目录校验（P1-5，CI 发布门）。

断言：JSON 可解析且无重复键（object_pairs_hook 检测——重复键后者覆盖
前者是静默陷阱）；顶层 {version, models}；每个条目 context_window 为
正整数、可选字段类型正确（null=未知合法，臆造数值不合法）。
退出码：0=通过 / 1=失败（CI 拦截）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "loadn" / "models.json"   # scripts/ = 仓库根脚本目录
REQUIRED = ("context_window",)
INT_FIELDS = ("context_window", "max_output", "prompt_cache_ttl_s")
NUM_FIELDS = ("input_price_per_m", "output_price_per_m")


def _no_dup_keys(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise ValueError(f"重复键：{k!r}（后者静默覆盖前者——目录禁止）")
        seen.add(k)
    return dict(pairs)


def main() -> int:
    problems: list[str] = []
    try:
        data = json.loads(CATALOG.read_text(encoding="utf-8"),
                          object_pairs_hook=_no_dup_keys)
    except (OSError, ValueError) as e:
        print(f"✗ models.json 解析失败：{e}", file=sys.stderr)
        return 1
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict) or not models:
        print("✗ 顶层需为 {version, models:{...}} 且 models 非空", file=sys.stderr)
        return 1
    if not isinstance(data.get("version"), int):
        problems.append("version 需为 int")
    for name, entry in models.items():
        if not isinstance(entry, dict):
            problems.append(f"{name}: 条目需为对象")
            continue
        for f in REQUIRED:
            v = entry.get(f)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                problems.append(f"{name}: {f} 需为正整数（收到 {v!r}）")
        for f in INT_FIELDS:
            v = entry.get(f)
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)
                                  or v <= 0):
                problems.append(f"{name}: {f} 需为正整数或 null（收到 {v!r}）")
        for f in NUM_FIELDS:
            v = entry.get(f)
            if v is not None and not isinstance(v, (int, float)):
                problems.append(f"{name}: {f} 需为数值或 null（收到 {v!r}）")
        if entry.get("context_window") and entry["context_window"] < 1000:
            problems.append(f"{name}: context_window={entry['context_window']}"
                            " 疑似 token 数写错量级")
    if problems:
        for p in problems:
            print(f"✗ {p}", file=sys.stderr)
        return 1
    print(f"✓ models.json：{len(models)} 个模型，schema 通过，无重复键")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
