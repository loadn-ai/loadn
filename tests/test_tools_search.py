"""Grep/Glob/WebFetch/WebSearch/TodoWrite/ToolRegistry 测试。

Grep 双后端：本机无 rg 二进制（shutil.which 为 None）天然走纯 Python
兜底；rg 路径用假 rg 脚本（回显收到的参数 + 一条 canned 命中）验证
透传的 flags。WebFetch/WebSearch 全部离线：假 httpx 模块注入。
"""
from __future__ import annotations

import json
import os
import stat
import types
from pathlib import Path

import httpx
import pytest

import hahaness.tools.grep as grep_mod
import hahaness.tools.webfetch as webfetch_mod
import hahaness.tools.websearch as websearch_mod
from hahaness.constants import GLOB_MAX_HITS, GREP_MAX_HITS, WEBFETCH_MAX_CHARS, WEBSEARCH_TOP_K
from hahaness.tools import ToolRegistry
from hahaness.tools.base import ToolContext, ToolError
from hahaness.tools.glob import GlobTool
from hahaness.tools.grep import GrepTool
from hahaness.tools.todowrite import TodoWriteTool
from hahaness.tools.webfetch import WebFetchTool
from hahaness.tools.websearch import WebSearchTool
from hahaness.types import SessionState


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def _make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src" / ".git").mkdir(parents=True)
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "src" / "app.py").write_text("import os\nneedle here\ndef f():\n    return\n")
    (root / "src" / "lib.js").write_text("needle in js\nother line\n")
    (root / "node_modules" / "dep" / "bad.py").write_text("needle in node_modules\n")
    (root / "src" / ".git" / "cfg.py").write_text("needle in git\n")
    return root


# ---------------------------------------------------------------- Grep（Python 兜底）
async def test_grep_py_fallback_content(ctx: ToolContext, tmp_path: Path,
                                        monkeypatch):
    monkeypatch.setattr(grep_mod, "_rg_binary", lambda: None)
    root = _make_tree(tmp_path)
    out = await GrepTool().execute({"pattern": "needle", "path": str(root)},
                                   ctx)
    lines = out.splitlines()
    assert any(ln.endswith(":2:needle here") and str(root / "src" / "app.py") in ln
               for ln in lines)
    assert any("lib.js:1:needle in js" in ln for ln in lines)
    assert "node_modules" not in out and ".git" not in out   # 排除目录


async def test_grep_py_fallback_modes_and_filters(ctx: ToolContext,
                                                  tmp_path: Path, monkeypatch):
    monkeypatch.setattr(grep_mod, "_rg_binary", lambda: None)
    root = _make_tree(tmp_path)
    files_out = await GrepTool().execute(
        {"pattern": "needle", "path": str(root),
         "output_mode": "files_with_matches"}, ctx)
    assert len(files_out.splitlines()) == 2               # 两个命中文件
    assert "app.py" in files_out and "lib.js" in files_out

    count_out = await GrepTool().execute(
        {"pattern": "needle", "path": str(root), "output_mode": "count"}, ctx)
    assert "app.py:1" in count_out and "lib.js:1" in count_out

    glob_out = await GrepTool().execute(
        {"pattern": "needle", "path": str(root), "glob": "*.py"}, ctx)
    assert "app.py" in glob_out and "lib.js" not in glob_out

    ic_out = await GrepTool().execute(
        {"pattern": "NEEDLE", "path": str(root), "ignore_case": True}, ctx)
    assert "needle here" in ic_out

    no_hit = await GrepTool().execute({"pattern": "needle",
                                       "path": str(root / "src" / "lib.js"),
                                       "glob": "*.py"}, ctx)
    assert "无命中" in no_hit


async def test_grep_py_fallback_multiline(ctx: ToolContext, tmp_path: Path,
                                          monkeypatch):
    monkeypatch.setattr(grep_mod, "_rg_binary", lambda: None)
    f = tmp_path / "m.txt"
    f.write_text("start\nmiddle\nend\n")
    hit = await GrepTool().execute(
        {"pattern": "start.*end", "path": str(f), "multiline": True}, ctx)
    assert "m.txt:1:" in hit and "start" in hit
    miss = await GrepTool().execute(
        {"pattern": "start.*end", "path": str(f)}, ctx)
    assert "无命中" in miss


async def test_grep_py_fallback_hit_cap(ctx: ToolContext, tmp_path: Path,
                                        monkeypatch):
    monkeypatch.setattr(grep_mod, "_rg_binary", lambda: None)
    f = tmp_path / "many.txt"
    f.write_text("".join(f"needle line {i}\n" for i in range(GREP_MAX_HITS + 50)))
    out = await GrepTool().execute({"pattern": "needle", "path": str(f)}, ctx)
    lines = out.splitlines()
    assert len(lines) == GREP_MAX_HITS + 1
    assert "还有 50 条" in lines[-1] and "收窄" in lines[-1]


async def test_grep_invalid_regex_rejected(ctx: ToolContext):
    with pytest.raises(ToolError) as ei:
        await GrepTool().execute({"pattern": "([unclosed"}, ctx)
    assert "正则" in str(ei.value)


# ---------------------------------------------------------------- Grep（rg shell out）
async def test_grep_rg_flags_passthrough(ctx: ToolContext, tmp_path: Path,
                                         monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    record = tmp_path / "rg_args.txt"
    fake_rg = fake_bin / "rg"
    fake_rg.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" > "{record}"\n'
        'printf "canned.txt:9:fake-needle\\n"\n')
    fake_rg.chmod(fake_rg.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(grep_mod, "_rg_binary", lambda: str(fake_rg))

    root = _make_tree(tmp_path)
    out = await GrepTool().execute(
        {"pattern": "needle", "path": str(root), "ignore_case": True,
         "glob": "*.py", "multiline": True}, ctx)
    assert "canned.txt:9:fake-needle" in out
    args = record.read_text().split()
    assert "--no-heading" in args and "-n" in args and "-i" in args
    gi = args.index("-g")
    assert args[gi + 1] == "*.py"
    assert "--multiline" in args and "--" in args
    assert args[args.index("--") + 1] == "needle"


# ---------------------------------------------------------------- Glob
async def test_glob_mtime_order_and_exclusions(ctx: ToolContext,
                                               tmp_path: Path):
    root = tmp_path / "site"
    (root / "sub" / "node_modules").mkdir(parents=True)
    (root / ".git").mkdir()
    old = root / "a.py"
    old.write_text("old")
    new = root / "sub" / "b.py"
    new.write_text("new")
    (root / "sub" / "node_modules" / "c.py").write_text("dep")
    (root / ".git" / "d.py").write_text("git")
    (root / "e.txt").write_text("txt")
    os.utime(old, (1000000, 1000000))               # 确定性 mtime：old 最老
    os.utime(new, (2000000, 2000000))
    out = await GlobTool().execute({"pattern": "**/*.py", "path": str(root)},
                                   ctx)
    lines = out.splitlines()
    assert lines[0].endswith("b.py")                # 新改的在前
    assert len(lines) == 2
    assert all("node_modules" not in ln and ".git" not in ln for ln in lines)


async def test_glob_hit_cap(ctx: ToolContext, tmp_path: Path):
    for i in range(GLOB_MAX_HITS + 5):
        (tmp_path / f"f{i}.txt").write_text("x")
    out = await GlobTool().execute({"pattern": "*.txt"}, ctx)
    lines = out.splitlines()
    assert len(lines) == GLOB_MAX_HITS + 1
    assert "还有 5 个" in lines[-1]


async def test_glob_no_match(ctx: ToolContext):
    out = await GlobTool().execute({"pattern": "*.xyz"}, ctx)
    assert "无匹配" in out


# ---------------------------------------------------------------- WebFetch（离线假 httpx）
def _fake_httpx_webfetch(response=None, exc=None):
    calls: list[dict] = []

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {}

    class _Client:
        def __init__(self, **kw):
            self.kw = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            calls.append({"url": url, "kw": self.kw})
            if exc is not None:
                raise exc
            return response or _Resp()

    return types.SimpleNamespace(AsyncClient=lambda **kw: _Client(**kw),
                                 HTTPError=httpx.HTTPError), calls


async def test_webfetch_extracts_main_text(ctx: ToolContext, monkeypatch):
    html = ("<html><head><title>t</title><script>evil()</script></head>"
            "<body><h1>标题甲</h1><p>正文段落乙丙丁。</p></body></html>")
    fake, calls = _fake_httpx_webfetch(
        response=type("R", (), {"status_code": 200, "text": html,
                                "headers": {"content-type": "text/html"}})())
    monkeypatch.setattr(webfetch_mod, "httpx", fake)
    out = await WebFetchTool().execute({"url": "https://ex.com/a",
                                        "prompt": "总结"}, ctx)
    assert "标题甲" in out and "正文段落乙丙丁" in out
    assert "evil" not in out                          # script 剥除
    assert calls[0]["kw"]["follow_redirects"] is True
    assert "Mozilla/5.0" in calls[0]["kw"]["headers"]["User-Agent"]


async def test_webfetch_fallback_parser_without_trafilatura(
        ctx: ToolContext, monkeypatch):
    monkeypatch.setattr(webfetch_mod, "trafilatura", None)
    html = ("<html><body><style>.x{color:red}</style>"
            "<p>兜底提取段落。</p><script>bad()</script></body></html>")
    fake, _ = _fake_httpx_webfetch(
        response=type("R", (), {"status_code": 200, "text": html,
                                "headers": {"content-type": "text/html"}})())
    monkeypatch.setattr(webfetch_mod, "httpx", fake)
    out = await WebFetchTool().execute({"url": "https://ex.com/b"}, ctx)
    assert "兜底提取段落" in out
    assert "color:red" not in out and "bad()" not in out


async def test_webfetch_non_200_is_toolerror(ctx: ToolContext, monkeypatch):
    fake, _ = _fake_httpx_webfetch(
        response=type("R", (), {"status_code": 503, "text": "oops",
                                "headers": {}})())
    monkeypatch.setattr(webfetch_mod, "httpx", fake)
    with pytest.raises(ToolError) as ei:
        await WebFetchTool().execute({"url": "https://ex.com/c"}, ctx)
    assert "503" in str(ei.value)


async def test_webfetch_network_error_is_toolerror(ctx: ToolContext,
                                                   monkeypatch):
    fake, _ = _fake_httpx_webfetch(exc=httpx.ConnectError("refused"))
    monkeypatch.setattr(webfetch_mod, "httpx", fake)
    with pytest.raises(ToolError) as ei:
        await WebFetchTool().execute({"url": "https://ex.com/d"}, ctx)
    assert "网络层" in str(ei.value)


async def test_webfetch_long_text_clipped(ctx: ToolContext, monkeypatch):
    html = "<html><body><p>" + "字" * (WEBFETCH_MAX_CHARS + 5000) + "</p></body></html>"
    fake, _ = _fake_httpx_webfetch(
        response=type("R", (), {"status_code": 200, "text": html,
                                "headers": {"content-type": "text/html"}})())
    monkeypatch.setattr(webfetch_mod, "httpx", fake)
    out = await WebFetchTool().execute({"url": "https://ex.com/e"}, ctx)
    assert len(out) <= WEBFETCH_MAX_CHARS + 40
    assert "…[截断" in out


# ---------------------------------------------------------------- WebSearch（离线假 httpx）
def _fake_httpx_websearch(payload=None, status=200, exc=None):
    calls: list[dict] = []

    class _Resp:
        def __init__(self):
            self.status_code = status
            self.text = json.dumps(payload or {})

        def json(self):
            return payload or {}

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append({"url": url, "headers": headers, "json": json})
            if exc is not None:
                raise exc
            return _Resp()

    return types.SimpleNamespace(AsyncClient=lambda **kw: _Client(**kw),
                                 HTTPError=httpx.HTTPError), calls


async def test_websearch_unconfigured(ctx: ToolContext, monkeypatch):
    monkeypatch.delenv("HAHANESS_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("HAHANESS_SEARCH_KEY", raising=False)
    with pytest.raises(ToolError) as ei:
        await WebSearchTool().execute({"query": "q"}, ctx)
    assert "搜索后端未配置" in str(ei.value)


async def test_websearch_unknown_provider(ctx: ToolContext, monkeypatch):
    monkeypatch.setenv("HAHANESS_SEARCH_PROVIDER", "google")
    monkeypatch.setenv("HAHANESS_SEARCH_KEY", "k")
    with pytest.raises(ToolError) as ei:
        await WebSearchTool().execute({"query": "q"}, ctx)
    assert "未知搜索后端" in str(ei.value)


async def test_websearch_bocha(ctx: ToolContext, monkeypatch):
    payload = {"data": {"webPages": {"value": [
        {"name": "结果甲", "url": "https://ex.com/1", "summary": "摘要甲"},
        {"name": "结果乙", "url": "https://ex.com/2", "summary": "摘要乙"}]}}}
    fake, calls = _fake_httpx_websearch(payload)
    monkeypatch.setattr(websearch_mod, "httpx", fake)
    monkeypatch.setenv("HAHANESS_SEARCH_PROVIDER", "bocha")
    monkeypatch.setenv("HAHANESS_SEARCH_KEY", "test-key")
    out = await WebSearchTool().execute({"query": "测试"}, ctx)
    assert "1. 结果甲\nhttps://ex.com/1\n摘要甲" in out
    assert "2. 结果乙" in out
    req = calls[0]
    assert req["url"] == "https://api.bochaai.com/v1/web-search"
    assert req["headers"]["Authorization"] == "Bearer test-key"
    assert req["json"] == {"query": "测试", "count": WEBSEARCH_TOP_K}


async def test_websearch_zhipu(ctx: ToolContext, monkeypatch):
    payload = {"search_result": [
        {"title": "智谱结果", "link": "https://z.cn/1", "content": "内容甲"}]}
    fake, calls = _fake_httpx_websearch(payload)
    monkeypatch.setattr(websearch_mod, "httpx", fake)
    monkeypatch.setenv("HAHANESS_SEARCH_PROVIDER", "zhipu")
    monkeypatch.setenv("HAHANESS_SEARCH_KEY", "zk")
    out = await WebSearchTool().execute({"query": "智谱查询"}, ctx)
    assert "1. 智谱结果\nhttps://z.cn/1\n内容甲" in out
    req = calls[0]
    assert req["url"] == "https://open.bigmodel.cn/api/paas/v4/web_search"
    assert req["json"]["search_engine"] == "search_std"
    assert req["json"]["search_query"] == "智谱查询"
    assert req["json"]["count"] == WEBSEARCH_TOP_K


async def test_websearch_network_error(ctx: ToolContext, monkeypatch):
    fake, _ = _fake_httpx_websearch(exc=httpx.ReadTimeout("t"))
    monkeypatch.setattr(websearch_mod, "httpx", fake)
    monkeypatch.setenv("HAHANESS_SEARCH_PROVIDER", "bocha")
    monkeypatch.setenv("HAHANESS_SEARCH_KEY", "k")
    with pytest.raises(ToolError) as ei:
        await WebSearchTool().execute({"query": "q"}, ctx)
    assert "网络层" in str(ei.value)


# ---------------------------------------------------------------- TodoWrite
async def test_todowrite_full_overwrite(ctx: ToolContext):
    state = SessionState()
    ctx.extras["state"] = state
    out = await TodoWriteTool().execute({"todos": [
        {"content": "调研", "status": "completed"},
        {"content": "写代码", "status": "in_progress", "activeForm": "正在写代码"},
        {"content": "测试", "status": "pending"}]}, ctx)
    assert "3 项" in out
    assert [t.id for t in state.todos] == ["t1", "t2", "t3"]
    assert state.todos[0].subject == "调研"
    assert state.todos[1].status == "in_progress"
    assert state.todos[1].activeForm == "正在写代码"
    # 二次全量覆盖：只剩 2 项，id 稳定重排
    out2 = await TodoWriteTool().execute({"todos": [
        {"content": "新A", "status": "pending"},
        {"content": "新B", "status": "pending"}]}, ctx)
    assert "2 项" in out2
    assert [t.subject for t in state.todos] == ["新A", "新B"]
    assert [t.id for t in state.todos] == ["t1", "t2"]


async def test_todowrite_invalid_status(ctx: ToolContext):
    ctx.extras["state"] = SessionState()
    with pytest.raises(ToolError) as ei:
        await TodoWriteTool().execute(
            {"todos": [{"content": "x", "status": "doing"}]}, ctx)
    assert "status 非法" in str(ei.value)


async def test_todowrite_requires_state(ctx: ToolContext):
    with pytest.raises(ToolError) as ei:
        await TodoWriteTool().execute(
            {"todos": [{"content": "x", "status": "pending"}]}, ctx)
    assert "SessionState" in str(ei.value)


# ---------------------------------------------------------------- Registry
def test_registry_default_collects_all_except_task():
    reg = ToolRegistry.default()
    names = reg.names()
    assert names == ["Bash", "Edit", "Glob", "Grep", "Read",
                     "TodoWrite", "WebFetch", "WebSearch", "Write"]
    assert "Task" not in names


def test_registry_read_only_only():
    reg = ToolRegistry.default(read_only_only=True)
    assert reg.names() == ["Glob", "Grep", "Read", "WebFetch", "WebSearch"]


def test_registry_disallow_and_defs():
    reg = ToolRegistry.default(disallow=["Bash"])
    assert "Bash" not in reg.names()
    full = ToolRegistry.default()
    defs = full.defs(disallow=["WebFetch"])
    names = [d.name for d in defs]
    assert "WebFetch" not in names and "Bash" in names
    d = next(x for x in defs if x.name == "Read")
    assert d.input_schema["required"] == ["file_path"]
    assert d.description and d.input_schema


def test_registry_register_duplicate_rejected():
    reg = ToolRegistry.default()
    from hahaness.tools.read import ReadTool
    with pytest.raises(ValueError):
        reg.register(ReadTool())
