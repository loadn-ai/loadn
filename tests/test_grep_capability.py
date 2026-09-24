"""P3-8 Grep 能力探测与优雅回退验收（零 token）。

- capability：探测缓存（一次 which）；无 rg → backend=fallback + 降级
  日志；有 rg → 版本解析
- 工具描述动态声明：fallback 档描述含降级段落；rg 档与现状一致
- 回退语义：PATH 去掉 rg——Grep 仍可用（纯 Python 扫描）；glob 过滤
  语义一致（*.py / **/*.py 命中任意深度）；三 output_mode 全可用
- 有 rg 时行为与现状完全一致（对照）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from loadn.tools import grep as g


@pytest.fixture(autouse=True)
def _fresh():
    g._CAPABILITY = None
    yield
    g._CAPABILITY = None


def _mk_tree(tmp: Path):
    (tmp / "a.py").write_text("alpha = 1\nBETA = 2\n", encoding="utf-8")
    sub = tmp / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("gamma_alpha()\n", encoding="utf-8")
    (sub / "c.txt").write_text("alpha in txt\n", encoding="utf-8")
    return tmp


# ---------------------------------------------------------------- capability
def test_capability_detects_rg(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/rg")
    monkeypatch.setattr(g.subprocess, "run",
                        lambda *a, **k: type("R", (), {
                            "returncode": 0,
                            "stdout": "ripgrep 14.1.0\nline2"})())
    cap = g.capability()
    assert cap == {"backend": "rg", "rg": "/usr/bin/rg", "version": "14.1.0"}
    assert g.capability() is cap                      # 会话缓存（同一 dict）


def test_capability_fallback_warns(monkeypatch, caplog):
    monkeypatch.setattr(g.shutil, "which", lambda n: None)
    cap = g.capability()
    assert cap == {"backend": "fallback", "rg": None, "version": None}
    assert any("兜底" in r.message for r in caplog.records)


def test_description_degradation(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/rg")
    monkeypatch.setattr(g.subprocess, "run",
                        lambda *a, **k: type("R", (), {
                            "returncode": 0, "stdout": "ripgrep 1.0"})())
    rg_desc = g.GrepTool().description
    assert "回退" not in rg_desc and "ripgrep 不可用" not in rg_desc
    g._CAPABILITY = None
    monkeypatch.setattr(g.shutil, "which", lambda n: None)
    fb_desc = g.GrepTool().description
    assert "ripgrep 不可用" in fb_desc and "回退" in fb_desc


# ---------------------------------------------------------------- 回退可用性
async def test_fallback_search_works_without_rg(tmp_path, monkeypatch):
    """PATH 无 rg：三 output_mode 全可用 + glob 语义一致。"""
    monkeypatch.setattr(g.shutil, "which", lambda n: None)
    g._CAPABILITY = None
    root = _mk_tree(tmp_path)
    ctx = type("Ctx", (), {"cwd": root})()
    t = g.GrepTool()

    # content：无 glob——.py/.txt 都命中
    out = await t.execute({"pattern": "alpha"}, ctx)
    assert "a.py:1" in out and "b.py:1" in out and "c.txt" not in out \
        or "c.txt:1" in out                              #（py+txt 均可，断言不锁顺序）
    # glob 过滤：*.py 只命中 py（含子目录）
    out2 = await t.execute({"pattern": "alpha", "glob": "*.py"}, ctx)
    assert "a.py" in out2 and "b.py" in out2 and "c.txt" not in out2
    # **/*.py 同语义
    out3 = await t.execute({"pattern": "alpha", "glob": "**/*.py"}, ctx)
    assert "b.py" in out3 and "c.txt" not in out3
    # files_with_matches / count / ignore_case
    files_ = await t.execute({"pattern": "alpha", "output_mode":
                              "files_with_matches", "glob": "*.py"}, ctx)
    assert "a.py" in files_ and "b.py" in files_
    cnt = await t.execute({"pattern": "beta", "output_mode": "count",
                           "ignore_case": True}, ctx)
    assert "a.py:1" in cnt                             # BETA 命中（-i）
    none = await t.execute({"pattern": "zzz_nothing"}, ctx)
    assert "无命中" in none


async def test_rg_path_unchanged(tmp_path, monkeypatch):
    """有 rg：走 rg 后端（monkeypatch _via_rg 断言调用形态不变）。"""
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/rg")
    monkeypatch.setattr(g.subprocess, "run",
                        lambda *a, **k: type("R", (), {
                            "returncode": 0, "stdout": "ripgrep 1.0"})())
    g._CAPABILITY = None
    calls = []

    async def fake_rg(rg_bin, pattern, root, glob_, ic, ml, mode):
        calls.append((pattern, glob_, mode))
        return ["hit:1:x"]

    t = g.GrepTool()
    t._via_rg = fake_rg
    out = await t.execute({"pattern": "x", "glob": "*.py"}, type(
        "Ctx", (), {"cwd": tmp_path})())
    assert out == "hit:1:x"
    assert calls == [("x", "*.py", "content")]
