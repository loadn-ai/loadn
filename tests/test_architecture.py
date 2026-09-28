"""架构铁律测试（R4）：进程边界——CI 红牌级。

§0 铁律：webui 驱动引擎永远走子进程 + stream-json 契约；
loadn_webui 包内禁止 import loadn.core（反向 loadn 也不得依赖 webui）。
改契约=独立 PR + PROTOCOL bump，不得以本测试例外的方式绕过。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FORBIDDEN = {
    "loadn_webui": ("loadn.core", "loadn.tools", "loadn.providers",
                    "loadn.persistence", "loadn.supervisor", "loadn.cli",
                    "loadn.mcp"),        # 进程边界：引擎内部模块全部禁入
    "loadn": ("loadn_webui",),           # 反向：引擎不得依赖平台
}


def _imports_of(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_process_boundary_import_ban():
    """loadn_webui 内不得 import 引擎内部模块；引擎不得 import 平台。"""
    violations: list[str] = []
    for pkg, banned in FORBIDDEN.items():
        for py in (REPO / pkg).rglob("*.py"):
            tree = ast.parse(py.read_text(errors="replace"))
            for imp in _imports_of(tree):
                for b in banned:
                    if imp == b or imp.startswith(b + "."):
                        violations.append(f"{py.relative_to(REPO)}: import {imp}")
    assert not violations, "进程边界铁律被违反（PROTOCOL.md §0）:\n" + "\n".join(violations)


def test_no_string_form_bypass():
    """字符串形态旁路也拦：__import__('loadn.core') / importlib 动态导入。"""
    import re
    violations: list[str] = []
    pat = re.compile(r"__(\"|')?import(?:\"|')\(|importlib\.import_module")
    for pkg, banned in FORBIDDEN.items():
        for py in (REPO / pkg).rglob("*.py"):
            s = py.read_text(errors="replace")
            if pat.search(s):
                # 命中动态导入——再查是否指向被禁模块
                for b in banned:
                    if b.split(".")[0] in s:
                        violations.append(f"{py.relative_to(REPO)}: 动态导入痕迹")
                        break
    assert not violations, "疑似动态导入绕过进程边界:\n" + "\n".join(violations)
