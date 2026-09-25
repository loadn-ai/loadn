"""浏览器 CUA MCP server（P2-5，ZCode Z5 同构：平台侧宿主，引擎零改动）。

形态：stdio MCP server（JSON-RPC，与 loadn/mcp/client.py StdioMCPConnection
对话）——webui 把它注入会话 .mcp.json（write_mcp_json 的 browser 条目），
引擎经 MCP 动态工具获得 browser.open/navigate/click/fill/screenshot/
read_console。

架构决策（ZCode 同构）：
- **浏览器进程在沙箱外、由平台管理**——引擎只持 client 契约，进程边界即
  安全边界（引擎 compromise 不直接拿到浏览器）
- **导航域过出口白名单**（复用 egress _allowed：私网/回环放行=本地前端
  验证场景；公网域须白名单内，不然浏览器成为绕网后门）
- 首次 open 公网域 → 审批码门（approve.create browser_open——headless
  fail-closed 同 OAuth 语义；localhost/127.x/内网 直接放行）
- CDP：playwright connect_over_cdp（复用 fetch_page 的经验：Bearer token
  env 可选）；CDP 端点取 CONFIG.resources.cdp_url
- playwright 是**平台 venv 的可选依赖**（webui extras 语义——引擎单依赖
  红线不涉；缺省时 server 起得来但工具报 not_installed）
"""
from __future__ import annotations

import json
import os
import sys

from .audit import audit
from .config import CONFIG


# ---------------------------------------------------------------- 域策略
def _host_allowed(url: str) -> tuple[bool, str]:
    """导航域判定：本地前端（loopback/私网）放行；公网须出口白名单。"""
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False, "bad-url"
    if not host:
        return False, "no-host"
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") \
            or host.endswith(".local"):
        return True, "local"
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback:
            return True, "local"
    except ValueError:
        pass
    from .net_policy import is_non_public_ip
    if is_non_public_ip(host):
        return True, "local"
    # 公网域：出口白名单（egress_proxy._allowed 同源）
    from .egress_proxy import _allowed
    if _allowed(host):
        return True, "allowlisted"
    return False, "not-allowlisted"


async def _require_approval(host: str, sid: str) -> bool:
    """公网域首次打开 → 审批卡（approve.py 消费侧——平台进程内直调）。"""
    from . import approve
    try:
        approve.create(sid, "browser_open", {"host": host},
                       note="browser.open 公网域")
        return False                       # 卡挂起：本轮拒（headless fail-closed）
    except ValueError:
        return False


# ---------------------------------------------------------------- 浏览器会话
class BrowserSession:
    """playwright CDP 会话（惰性连接；工具调用粒度复用）。"""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None

    def _ensure(self):
        if self._browser is not None:
            return
        # 配置检查先于依赖 import（T5 修）：cdp 未配置时给配置错误文案，
        # 而非让未装 playwright 的环境先炸 ImportError（fail-fast 语义）
        cdp = CONFIG.resources.cdp_url
        if not cdp:
            raise RuntimeError("resources.cdp_url 未配置（AIO 沙箱浏览器）")
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        token = os.environ.get("LOADN_CDP_TOKEN", "")
        kw = {"headers": {"Authorization": f"Bearer {token}"}} if token else {}
        self._browser = self._pw.chromium.connect_over_cdp(cdp, **kw)
        ctx = self._browser.contexts[0] if self._browser.contexts \
            else self._browser.new_context()
        self._page = ctx.new_page()

    def page(self):
        self._ensure()
        return self._page

    def close(self):
        for closer in (lambda: self._page.close(), lambda: self._browser.close(),
                       lambda: self._pw.stop()):
            try:
                closer()
            except Exception:                              # noqa: BLE001
                pass
        self._page = self._browser = self._pw = None


_SESSION = BrowserSession()


# ---------------------------------------------------------------- 工具实现
def tool_open(args: dict) -> str:
    url = str(args.get("url") or "")
    ok, why = _host_allowed(url)
    if not ok:
        if why == "not-allowlisted":
            return (f"拒绝：{url} 的域名不在出口白名单（浏览器不得成为绕网"
                    "后门）。用户可在安全中心放行该域后重试")
        return f"拒绝：URL 非法（{why}）"
    page = _SESSION.page()
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    audit("browser_cua", {"action": "open", "url": url, "zone": why})
    return f"已打开 {url}（title={page.title()[:60]}）"


def tool_click(args: dict) -> str:
    sel = str(args.get("selector") or "")
    page = _SESSION.page()
    page.click(sel, timeout=10000)
    return f"已点击 {sel}"


def tool_fill(args: dict) -> str:
    sel = str(args.get("selector") or "")
    val = str(args.get("value") or "")
    page = _SESSION.page()
    page.fill(sel, val, timeout=10000)
    return f"已填写 {sel}"


def tool_screenshot(args: dict) -> str:
    page = _SESSION.page()
    import base64
    import tempfile
    from pathlib import Path
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    page.screenshot(path=tmp, full_page=bool(args.get("full_page", False)))
    data = base64.b64encode(Path(tmp).read_bytes()).decode()
    Path(tmp).unlink(missing_ok=True)
    return f"data:image/png;base64,{data}"       # MCP 工具结果直回图


def tool_read_console(args: dict) -> str:
    page = _SESSION.page()
    logs = page.evaluate(
        "() => window.__loadnLogs ? window.__loadnLogs : []")
    return json.dumps(logs[-50:], ensure_ascii=False) if logs else "（无注入日志）"


TOOLS = {
    "open": ("browser.open", tool_open,
             "打开 URL（本地前端直接放行；公网域须出口白名单）",
             {"url": {"type": "string"}, "full_page": {"type": "boolean"}}),
    "click": ("browser.click", tool_click,
              "按 CSS 选择器点击元素",
              {"selector": {"type": "string"}}),
    "fill": ("browser.fill", tool_fill,
             "按 CSS 选择器填写输入框",
             {"selector": {"type": "string"},
              "value": {"type": "string"}}),
    "screenshot": ("browser.screenshot", tool_screenshot,
                   "整页截图（base64 PNG 回注工具结果）",
                   {"full_page": {"type": "boolean"}}),
    "read_console": ("browser.read_console", tool_read_console,
                     "读页面 console 注入日志（错误验证）", {}),
}


# ---------------------------------------------------------------- stdio MCP server
def main() -> int:
    """极简 stdio JSON-RPC（与 StdioMCPConnection 协议对话）。"""
    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "loadn-browser-cua",
                               "version": "0.1.0"}}})
        elif method == "notifications/initialized":
            # loadn StdioMCPConnection 对通知也带 id 等回执（其 _rpc 恒带
            # id）——回空 result 防对端 30s 等待（实测发现）
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            tools = [{"name": v[0], "description": v[2],
                      "inputSchema": {"type": "object", "properties": v[3]}}
                     for v in TOOLS.values()]
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "tools/call":
            name = (req.get("params") or {}).get("name") or ""
            args = (req.get("params") or {}).get("arguments") or {}
            entry = next((v for v in TOOLS.values() if v[0] == name), None)
            if entry is None:
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": f"未知工具 {name}"}]}})
                continue
            try:
                text = entry[1](args)
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": text}]}})
            except ImportError:
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "isError": True,
                    "content": [{"type": "text",
                                 "text": "not_installed: 平台 venv 缺 "
                                         "playwright（webui extras）"}]}})
            except Exception as e:                          # noqa: BLE001
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": repr(e)[:400]}]}})
    _SESSION.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
