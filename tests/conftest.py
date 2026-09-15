"""hahaness 测试基建：HAHANESS_HOME 隔离 + repo 根入 sys.path。

独立测试树（pytest 按路径收集，不与宿主测试互扰）。
asyncio_mode=auto 由仓库根 pytest.ini 提供。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import os  # noqa: E402

_HOME = Path(tempfile.mkdtemp(prefix="hahaness_test_home_"))
os.environ["HAHANESS_HOME"] = str(_HOME)
