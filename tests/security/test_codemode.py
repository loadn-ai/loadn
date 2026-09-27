"""P3-4 codemode 受限执行域验收。

- 白名单：正常脚本（循环/分支/容器/函数/print）跑通；批量重命名场景
  （fs_write ×N 一段代码完成）
- 越权全拒：import os / eval / open / __import__ / _private 属性 /
  dunder / 未列 builtins（open 变体）；拒绝记 codemode_blocked 审计
- 资源限：死循环 2s 超时终止 + anomaly 审计；print 过量截断
- 工具通道：fs_read/fs_write cwd 限定（../ 越界拒）+ 尺寸限
- 默认关：codemode_enabled=False 时 tool_run 拒；.mcp.json 不注入；
  开启后注入 codemode 条目
"""
from __future__ import annotations

import json

import pytest

from loadn_webui.config import CONFIG
from loadn_webui.integrations import codemode_mcp as cm

pytestmark = [pytest.mark.coverage("engine.codemode")]


@pytest.fixture
def _on(monkeypatch):
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", True)


# ---------------------------------------------------------------- 正常路径
def test_basic_script(tmp_path):
    out = cm.run("""
total = 0
for i in range(10):
    total += i
print("sum:", total)
""", sid="s1", cwd=tmp_path)
    assert "sum: 45" in out


def test_batch_rename_scenario(tmp_path):
    """批量重命名：一段代码 fs_write ×N（对比逐文件 Edit 的 N 轮调用）。"""
    for i in range(5):
        (tmp_path / f"old_{i}.txt").write_text(f"data{i}", encoding="utf-8")
    out = cm.run("""
for i in range(5):
    content = fs_read(f"old_{i}.txt")
    fs_write(f"new_{i}.txt", content + "-renamed")
""", sid="s2", cwd=tmp_path)
    assert (tmp_path / "new_3.txt").read_text() == "data3-renamed"
    assert (tmp_path / "old_3.txt").exists()       # 原文件保留（转换非移动）


# ---------------------------------------------------------------- 越权全拒
@pytest.mark.parametrize("bad", [
    "import os\nprint(1)",
    "from os import path",
    "eval('1+1')",
    "exec('x = 1')",
    "open('/etc/passwd')",
    "__import__('os')",
    "input()",
    "compile('1', 'x', 'eval')",
    "class Evil:\n    pass",                        # class def 不在白名单
    "x = (lambda: 1)\nimport json",                # 混入 import
])
def test_privilege_escalation_denied(tmp_path, bad):
    with pytest.raises(cm.CodemodeError):
        cm.run(bad, sid="s3", cwd=tmp_path)


def test_private_attr_denied(tmp_path):
    with pytest.raises(cm.CodemodeError):
        cm.run('x = "a".__class__', sid="s4", cwd=tmp_path)


def test_denial_audited_as_anomaly(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", True)
    from loadn_webui.security import audit as audit_mod
    out = cm.tool_run({"code": "import os"}, sid="s-audit")
    assert "拒绝" in out
    rows = audit_mod.tail(20, "anomaly")
    assert any("codemode_blocked" in r["detail_json"] for r in rows)


# ---------------------------------------------------------------- 资源限
def test_timeout_kills_loops(tmp_path):
    with pytest.raises(cm.CodemodeError, match="超时"):
        cm.run("while True:\n    pass", sid="s5", cwd=tmp_path,
               timeout_s=0.3)
    from loadn_webui.security import audit as audit_mod
    assert any("codemode_timeout" in r["detail_json"]
               for r in audit_mod.tail(3, "anomaly"))


def test_output_cap(tmp_path):
    """print 过量：_print 内即拒（print 是唯一的量出口——fs_write 另有上限）。"""
    with pytest.raises(cm.CodemodeError, match="超上限"):
        cm.run("""
for i in range(500):
    print("x" * 100)
""", sid="s6", cwd=tmp_path)


# ---------------------------------------------------------------- 工具通道
def test_fs_tools_cwd_confined(tmp_path):
    (tmp_path / "in.txt").write_text("ok", encoding="utf-8")
    assert "ok" in cm.run('print(fs_read("in.txt"))', sid="s7", cwd=tmp_path)
    with pytest.raises(cm.CodemodeError, match="越界"):
        cm.run('fs_read("../outside.txt")', sid="s8", cwd=tmp_path)
    with pytest.raises(cm.CodemodeError, match="越界"):
        cm.run('fs_write("/tmp/evil.txt", "x")', sid="s9", cwd=tmp_path)


# ---------------------------------------------------------------- 开关与注入
def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", False)
    out = cm.tool_run({"code": "print(1)"}, sid="s10")
    assert "未启用" in out


def test_mcp_json_injection_gate(tmp_path, monkeypatch):
    from loadn_webui import workspace as ws_mod
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", True)
    monkeypatch.setattr(CONFIG.resources, "cdp_url", "")   # 隔离 browser 注入
    monkeypatch.setattr(CONFIG, "mcp", type("M", (), {"servers": {}})())
    ws_mod.write_mcp_json(tmp_path, None)   # write_mcp 自建目录
    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert servers["codemode"]["args"] == ["_codemode-mcp"]
    # 关 → 不注入（无其他 server 时 .mcp.json 整体不落——merged 空）
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", False)
    ws_mod.write_mcp_json(tmp_path, None)
    if (tmp_path / ".mcp.json").exists():
        servers2 = json.loads(
            (tmp_path / ".mcp.json").read_text())["mcpServers"]
        assert "codemode" not in servers2
