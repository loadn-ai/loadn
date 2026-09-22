"""杂项：时间戳、日志、slug。"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso() -> str:
    """UTC ISO 时间戳（DB 统一口径，含时区后缀）。"""
    return utcnow().isoformat(timespec="seconds")


def local_ts() -> str:
    """本地时间紧凑戳（备份目录名用）。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_logger(name: str) -> logging.Logger:
    lg = logging.getLogger(name)
    if not lg.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
    return lg


def slugify(text: str, max_len: int = 48) -> str:
    """保留中文的 slug（承袭 papergo/workspace.py）。"""
    s = re.sub(r"[^\w一-鿿-]+", "-", text.strip()).strip("-").lower()
    return s[:max_len].rstrip("-") or "task"


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def ensure_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o755)
