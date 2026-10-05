"""P2 MCP 工具懒加载（ToolSearch）：阈值分桶 / 索引物化 / 回归对赌。

验收三件（fake server，零 token）：
① 50 工具 server → API tools 载荷（ToolSearch 索引）较全量注入降 >60%
② 任一延迟工具经 ToolSearch 物化后同一会话可调用（loop 级一轮两调用）
③ ≤15 工具 server 行为与旧行为逐字节一致（全量注入、无延迟索引）
暗礁负路径：内部豁免（mcp__lsp__diagnostics）/disallow 拒物化/未知名
报错列可用/tool_repair 已知集含延迟名/result 可选字段/env 阈值覆盖。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.helpers as H
from loadn.core.loop import AgentCore, LoopSettings
from loadn.core.session import SessionManager
from loadn.tools.base import ToolError


# ---------------------------------------------------------------- fake server
class _FakeConn:
    """零 token 假连接：list_tools 按名单出 spec，call_tool 记录并回显。"""

    def __init__(self, name: str, tool_names: list[str], *, fat: bool = False):
        self.name = name
        self.calls: list[tuple[str, dict]] = []
        self._specs = [_fat_spec(n) if fat else _slim_spec(n)
                       for n in tool_names]

    async def start(self):
        return {}

    async def stop(self):
        return None

    async def list_tools(self):
        return self._specs

    async def call_tool(self, tool: str, args: dict):
        self.calls.append((tool, args))
        return {"content": [{"type": "text",
                             "text": f"called {tool}({json.dumps(args)})"}]}


def _slim_spec(name: str) -> dict:
    return {"name": name, "description": f"{name} 的描述。第二句忽略",
            "inputSchema": {"type": "object",
                            "properties": {"x": {"type": "string"}},
                            "required": ["x"]}}


def _fat_spec(name: str) -> dict:
    """真实大 server 形状（每工具约 1k token 的 schema+描述）。"""
    props = {f"arg_{k}": {"type": "string", "description": "参数" + "细" * 80}
             for k in range(8)}
    return {"name": name, "description": f"{name} 工具。" + "用途" * 120,
            "inputSchema": {"type": "object", "properties": props,
                            "required": list(props)[:3]}}


async def _discover(monkeypatch, tmp_path: Path, servers: dict[str, list[str]],
                    *, fat: bool = False, threshold: int | None = None):
    import loadn.mcp.client as mc
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {n: {"type": "stdio", "command": "x"}
                        for n in servers}}), encoding="utf-8")
    conns = {}

    def factory(name, command, args, env):
        conns[name] = _FakeConn(name, servers[name], fat=fat)
        return conns[name]

    monkeypatch.setattr(mc, "StdioMCPConnection", factory)
    if threshold is not None:
        monkeypatch.setenv("LOADN_MCP_LAZY_TOOL_THRESHOLD", str(threshold))
    tools, deferred, live = await mc.discover(tmp_path)
    return tools, deferred, live, conns


def _payload(tools_dict) -> str:
    """API tools 字段载荷（anthropic 形态，Token 计入 input）。"""
    return json.dumps([t.def_().to_api() for t in tools_dict.values()],
                      ensure_ascii=False)


# ---------------------------------------------------------------- 验收①③
async def test_threshold_split_and_token_drop(monkeypatch, tmp_path):
    """大 server（50）延迟 + 小 server（5）全量；API 载荷降 >60%。"""
    tools, deferred, conns, _ = await _discover(
        monkeypatch, tmp_path,
        {"big": [f"t{i:02d}" for i in range(50)], "small": ["s0", "s1", "s2", "s3", "s4"]},
        fat=True)
    assert sorted(tools) == [f"mcp__small__s{i}" for i in range(5)]
    assert len(deferred) == 50 and "mcp__big__t07" in deferred
    assert len(conns) == 2                       # 延迟 server 连接保活（schema 缓存）
    # 载荷对照：旧行为 = 55 工具全量；新行为 = 5 小工具 + ToolSearch 索引
    from loadn.tools.tool_search import ToolSearchTool
    old = _payload({**tools, **deferred})
    new_tools = dict(tools)
    new_tools["ToolSearch"] = ToolSearchTool(deferred, new_tools)
    new = _payload(new_tools)
    assert len(new) < len(old) * 0.4, \
        f"载荷仅降 {100 - len(new) * 100 // len(old)}%（需 >60%）"


async def test_small_server_regression_byte_identical(monkeypatch, tmp_path):
    """≤15 工具：全量注入、零延迟，ToolDef 载荷与旧行为逐字节一致。"""
    names = [f"u{i:02d}" for i in range(15)]
    tools, deferred, _, _ = await _discover(monkeypatch, tmp_path, {"srv": names})
    assert deferred == {}
    expected = json.dumps(
        [{"name": f"mcp__srv__{n}", "description": f"{n} 的描述。第二句忽略",
          "input_schema": {"type": "object",
                           "properties": {"x": {"type": "string"}},
                           "required": ["x"]}} for n in sorted(names)],
        ensure_ascii=False, sort_keys=True)
    got = json.dumps([t.def_().to_api() for t in tools.values()],
                     ensure_ascii=False, sort_keys=True)
    assert got == expected


async def test_env_threshold_override(monkeypatch, tmp_path):
    """env 覆盖：0=关（50 工具也全量）；3=收紧（5 工具也延迟）。"""
    tools, deferred, _, _ = await _discover(
        monkeypatch, tmp_path, {"a": [f"n{i}" for i in range(50)]}, threshold=0)
    assert len(tools) == 50 and deferred == {}
    tools, deferred, _, _ = await _discover(
        monkeypatch, tmp_path, {"a": ["n0", "n1", "n2", "n3", "n4"]}, threshold=3)
    assert tools == {} and len(deferred) == 5


# ---------------------------------------------------------------- 暗礁
async def test_internal_keep_exemption(monkeypatch, tmp_path):
    """引擎内部直用名（mcp__lsp__diagnostics）所属 server 超阈值也照常注入
    ——loop 的 LSP 诊断回注依赖它，懒加载不得静默打断。"""
    names = ["diagnostics"] + [f"d{i:02d}" for i in range(29)]
    tools, deferred, _, _ = await _discover(monkeypatch, tmp_path,
                                            {"lsp": names})
    assert "mcp__lsp__diagnostics" in tools
    assert "mcp__lsp__diagnostics" not in deferred
    assert len(deferred) == 29


async def test_toolsearch_execute_guards(monkeypatch, tmp_path):
    """物化语义：命中→原地插入 tools + 返回全 schema；未知名/disallow 拒。"""
    from loadn.tools.tool_search import ToolSearchTool
    _, deferred, _, conns = await _discover(
        monkeypatch, tmp_path, {"big": [f"t{i:02d}" for i in range(20)]})
    tools: dict = {}
    ts = ToolSearchTool(deferred, tools, disallow={"mcp__big__t01"})
    # enum 索引：name + 首句 + @server
    desc = ts.input_schema["properties"]["tool"]["description"]
    assert "mcp__big__t00：t00 的描述@big" in desc
    out = await ts.execute({"tool": "mcp__big__t00"}, None)
    assert "mcp__big__t00" in tools and "required" in out
    assert "来源 server：big" in out
    again = await ts.execute({"tool": "mcp__big__t00"}, None)
    assert "此前已物化" in again
    with pytest.raises(ToolError, match="未知或已全量注入"):
        await ts.execute({"tool": "nope"}, None)
    with pytest.raises(ToolError, match="disallow"):
        await ts.execute({"tool": "mcp__big__t01"}, None)
    assert "mcp__big__t01" not in tools


# ---------------------------------------------------------------- 验收②（loop 级）
async def test_loop_materialize_then_call_one_round(monkeypatch, tmp_path):
    """一轮完成：模型调 ToolSearch（拿 schema）→ 同轮直接调真工具成功；
    result 事件带 mcp_deferred 观测字段。"""
    from loadn.tools.tool_search import ToolSearchTool
    _, deferred, _, conns = await _discover(
        monkeypatch, tmp_path, {"big": [f"t{i:02d}" for i in range(30)]})
    big = conns["big"]
    tools: dict = {}
    ts = ToolSearchTool(deferred, tools)
    tools["ToolSearch"] = ts
    rounds = [H.tool_round("tu_0", "ToolSearch", {"tool": "mcp__big__t07"}),
              H.tool_round("tu_1", "mcp__big__t07", {"x": "hi"}),
              H.text_round("完成")]
    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider(rounds), tools=tools,
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=10),
                     mcp_deferred=deferred)
    summary = await core.run_turn("用 t07 干活", emit=lambda e: None)
    assert summary.subtype == "success"
    # 物化生效：真工具被调用（不是 unknown tool），连接收到真参数
    assert big.calls == [("t07", {"x": "hi"})]
    assert "mcp__big__t07" in core.tools
    # result 可选字段（v1 消费方忽略未知键；事件为信封结构，内容在 payload）
    result = [e for e in session.transcript.read_events()
              if e["type"] == "result"][0]["payload"]
    assert result["mcp_deferred"]["tools"] == 30
    assert result["mcp_deferred"]["est_tokens_deferred"] > 0


async def test_loop_direct_call_auto_materializes(monkeypatch, tmp_path):
    """复查补强：模型从索引看到名字直接调用（未经 ToolSearch）——loop 当场
    物化并执行，不吃「未知工具」错误。"""
    _, deferred, _, conns = await _discover(monkeypatch, tmp_path,
                                            {"big": [f"t{i:02d}" for i in range(30)]})
    big = conns["big"]
    rounds = [H.tool_round("tu_0", "mcp__big__t09", {"x": "直呼"}),
              H.text_round("完成")]
    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider(rounds), tools={},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=10),
                     mcp_deferred=deferred)
    summary = await core.run_turn("直接用 t09", emit=lambda e: None)
    assert summary.subtype == "success"
    assert big.calls == [("t09", {"x": "直呼"})]
    assert "mcp__big__t09" in core.tools


async def test_build_wires_toolsearch_and_disallow_prefilter(tmp_path, monkeypatch):
    """build 全链：延迟非空注册 ToolSearch + AgentCore 带索引；disallow 的
    工具不进延迟索引（enum 不可见——fail-closed 而非调用时报错）。"""
    import loadn.mcp.client as mc
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"big": {"type": "stdio", "command": "x"}}}),
        encoding="utf-8")
    conn = _FakeConn("big", [f"t{i:02d}" for i in range(20)])
    monkeypatch.setattr(mc, "StdioMCPConnection",
                        lambda n, c, a, e: conn)
    from loadn.core.build import build_agent
    bundle = await build_agent(tmp_path, cfg={"provider": "fake"})
    assert "ToolSearch" in bundle.core.tools
    assert len(bundle.core.mcp_deferred) == 20
    for c in bundle.mcp_conns:
        await c.stop()
    bundle2 = await build_agent(tmp_path, cfg={"provider": "fake"},
                                disallow=["mcp__big__t03", "mcp__big__t04"])
    idx = bundle2.core.mcp_deferred
    assert "mcp__big__t03" not in idx and "mcp__big__t04" not in idx
    assert len(idx) == 18
    assert "ToolSearch" in bundle2.core.tools
    enum = bundle2.core.tools["ToolSearch"].input_schema["properties"]["tool"]["enum"]
    assert "mcp__big__t03" not in enum
    for c in bundle2.mcp_conns:
        await c.stop()


async def test_repair_known_tools_include_deferred(tmp_path, monkeypatch):
    """tool-call-repair 的已知工具集含延迟索引名——廉价网关把调用写成
    正文时，未物化工具的修复不得被拒。"""
    _, deferred, _, _ = None, {"mcp__big__greet": object()}, None, None
    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider([]), tools={},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=1),
                     mcp_deferred=deferred)
    from loadn.core import tool_repair as tr
    seen: dict = {}
    orig = tr.try_repair

    def spy(merged, known):
        seen["known"] = known
        return orig(merged, known)

    monkeypatch.setattr(tr, "try_repair", spy)
    core._repair_tool_calls([type("B", (), {"text": "纯文本"})()])
    assert "mcp__big__greet" in seen["known"]
