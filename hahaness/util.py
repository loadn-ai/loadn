"""轻量工具函数（logging 门面——零第三方依赖）。"""
from __future__ import annotations

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    lg = logging.getLogger(name)
    if not lg.handlers and not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            stream=sys.stderr)
    return lg
