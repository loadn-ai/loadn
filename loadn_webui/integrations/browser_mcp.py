"""浏览器 CUA MCP server（P2-5，ZCode Z5 同构：平台侧宿主，引擎零改动）。

形态：stdio MCP server（JSON-RPC，与 loadn/mcp/client.py StdioMCPConnection
对话）——webui 把它注入会话 .mcp.json（write_mcp_json 的 browser 条目），
引擎经 MCP 动态工具。P13 视觉 GUI 层：browser_screenshot/click/type/scroll 四工具（纯视觉——截图不注 DOM 信息；坐标点击），敏感 URL 冻结（支付/登录/验证码模式 → 审批+单步放行）、动作预算（screenshot≤20/click≤30）、步间隔 ≥800ms、click 后自动补 screenshot 供自纠。审计 browser_cua。获得 browser.open/navigate/click/fill/screenshot/
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
import re
import sys
import time
from urllib.parse import urlsplit as _usplit

from ..config import CONFIG
from ..security.audit import audit


# ---------------------------------------------------------------- 域策略
def _host_allowed(url: str) -> tuple[bool, str]:
    """导航域判定：本地前端（loopback/私网）放行；公网须出口白名单。"""
    from urllib.parse import urlsplit  # noqa: F401  （P13 域名审计用）
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
    from ..security.net_policy import is_non_public_ip
    if is_non_public_ip(host):
        return True, "local"
    # 公网域：出口白名单（egress_proxy._allowed 同源）
    from ..security.egress_proxy import _allowed
    if _allowed(host):
        return True, "allowlisted"
    return False, "not-allowlisted"


async def _require_approval(host: str, sid: str) -> bool:
    """公网域首次打开 → 审批卡（approve.py 消费侧——平台进程内直调）。"""
    from ..security import approve
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
    # 二轮修#19：新导航=新任务面——预算随 open 重置（原是进程级计数，
    # 长会话第二个任务的预算被第一个任务吃剩的腰斩）
    _budget.update(screenshot=0, click=0)
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


# ---------------------------------------------------------------- P13 视觉 GUI
SENSITIVE_URL_RE = re.compile(
    r"pay|login|signin|auth|captcha|verify|password|checkout|bank|otp|2fa",
    re.I)
MAX_SCREENSHOTS = 20          # 单任务截图预算（超限终止）
MAX_CLICKS = 30               # 单任务点击预算
STEP_INTERVAL_MS = 800        # 动作节流（含人味+防抖）
_budget = {"screenshot": 0, "click": 0}


def _consume_single_step_ticket(url: str) -> bool:
    """P13 复查修#4：验票+消费（一次性）。票=approve.decide 批准 browser_open
    时落在 cwd/.loadn/browser-allow-once.json（host 匹配 + 5 分钟内）。
    先删再判保证原子一次性——只放行下一个动作，再下一步重新冻结。"""
    import time as _t
    from pathlib import Path as _P
    tp = _P.cwd() / ".loadn" / "browser-allow-once.json"
    try:
        ticket = json.loads(tp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    try:
        tp.unlink()                         # 消费即删（验票原子一次性）
    except OSError:
        pass
    host = (_usplit(url).hostname or "").lower()
    ok_host = str(ticket.get("host") or "").lower() in ("", host)
    return bool(ok_host and _t.time() < float(ticket.get("exp") or 0))


def _p13_page(url_required: bool = False):
    """P13 门链：敏感 URL 冻结（审批+单步票放行）→ 预算 → 节流。"""
    page = _SESSION.page()
    if url_required:
        url = page.url or ""
        if SENSITIVE_URL_RE.search(url):
            if _consume_single_step_ticket(url):
                audit("browser_cua", {"action": "single_step_released",
                                      "url": url[:120]})
            else:
                # 敏感页：拒绝本步 + 审批请求（批准后经工作区单步票放行
                # 下一个动作——票一次性，连续两步敏感操作永不自动连放）
                sid = os.environ.get("LOADN_BROWSER_SID", "")
                audit("browser_cua", {"action": "sensitive_freeze",
                                      "url": url[:120]})
                if sid:
                    from ..security import approve as _ap
                    try:
                        _ap.create(sid, "browser_open",
                                   {"host": (_usplit(url).hostname or "")},
                                   note=f"敏感页视觉动作冻结：{url[:80]}")
                    except ValueError:
                        pass
                raise RuntimeError(
                    f"敏感页冻结：当前页面疑似支付/登录/验证码（{url[:80]}）。"
                    "已生成审批请求——批准后重试本步（仅放行这一步）")
    time.sleep(STEP_INTERVAL_MS / 1000.0)
    return page


def tool_p13_screenshot(args: dict) -> str:
    """纯视觉截图（viewport png→base64；不注入 DOM 信息保持纯视觉）。"""
    if _budget["screenshot"] >= MAX_SCREENSHOTS:
        raise RuntimeError(f"截图预算耗尽（{MAX_SCREENSHOTS}）——任务终止并汇报")
    _budget["screenshot"] += 1
    page = _p13_page()                      # 截图不吃敏感冻结（只读）
    data = _shot_b64(page)
    audit("browser_cua", {"action": "screenshot",
                          "n": _budget["screenshot"],
                          "domain": (_usplit(page.url or "").hostname or "")})
    return data


def tool_p13_click(args: dict) -> str:
    if _budget["click"] >= MAX_CLICKS:
        raise RuntimeError(f"点击预算耗尽（{MAX_CLICKS}）——任务终止并汇报")
    x, y = int(args.get("x", -1)), int(args.get("y", -1))
    if x < 0 or y < 0:
        raise RuntimeError("x/y 需为非负像素坐标（先 browser_screenshot 看画面）")
    page = _p13_page(url_required=True)      # 敏感页冻结（拒步不烧预算——
                                             # 二轮修#19：预算在过门后才 +1）
    _budget["click"] += 1
    page.mouse.click(x, y)
    audit("browser_cua", {"action": "click", "x": x, "y": y,
                          "domain": (_usplit(page.url or "").hostname or "")})
    # 自纠：click 后自动补 screenshot 供模型验证（预算内）
    if _budget["screenshot"] < MAX_SCREENSHOTS:
        _budget["screenshot"] += 1
        return _shot_b64(page)
    return "已点击（截图预算已尽，无法自动回图）"


def tool_p13_type(args: dict) -> str:
    text = str(args.get("text") or "")
    if not text:
        raise RuntimeError("text 不能为空")
    page = _p13_page(url_required=True)
    page.keyboard.type(text, delay=30)
    audit("browser_cua", {"action": "type", "len": len(text),
                          "domain": (_usplit(page.url or "").hostname or "")})
    return f"已输入 {len(text)} 字符"


def tool_p13_scroll(args: dict) -> str:
    dy = int(args.get("dy", 0))
    page = _p13_page()
    page.mouse.wheel(0, dy)
    audit("browser_cua", {"action": "scroll", "dy": dy})
    return f"已滚动 {dy}px"


def _shot_b64(page) -> str:
    import base64
    import tempfile
    from pathlib import Path
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    page.screenshot(path=tmp)               # viewport（非 full_page——纯视觉）
    data = "data:image/png;base64," + base64.b64encode(
        Path(tmp).read_bytes()).decode()
    Path(tmp).unlink(missing_ok=True)       # 截图不留存（审计快照临时保留=同款）
    return data


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
    # P13 视觉 GUI 四工具（纯视觉：坐标/像素，无 DOM 选择器）
    "p13_screenshot": ("browser_screenshot", tool_p13_screenshot,
                       "视口截图（纯视觉 png，无 DOM 信息）。先看再动。",
                       {}),
    "p13_click": ("browser_click", tool_p13_click,
                  "按像素坐标点击（先 browser_screenshot 看画面定坐标；"
                  "点击后自动回图验证）；敏感页（支付/登录/验证码）冻结待审批",
                  {"x": {"type": "integer"}, "y": {"type": "integer"}}),
    "p13_type": ("browser_type", tool_p13_type,
                 "在当前焦点输入文本（敏感页冻结待审批）",
                 {"text": {"type": "string"}}),
    "p13_scroll": ("browser_scroll", tool_p13_scroll,
                   "滚动 dy 像素（正=向下）", {"dy": {"type": "integer"}}),
}


# ---------------------------------------------------------------- stdio MCP server
def _playwright_ok() -> bool:
    """运行能力探测：venv 无 playwright 时浏览器工具全部不可用。"""
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _tools_payload() -> list:
    """tools/list 载荷——能力缺失时**空表**（死工具不上工具面）。

    2026-10-09 生产实证：venv 缺 playwright 时 9 个浏览器工具照常广告，
    模型调用必死（not_installed）——诱发了逐像素递增 50 连败的搅动循环
    （守卫盲区另修）。死工具在场=纯诱饵，fail-closed 不广告；tools/call
    兜底错误保留（防陈旧枚举残留）。
    """
    if not _playwright_ok():
        print("browser-mcp: playwright 缺失（webui extras），工具面空置",
              file=sys.stderr, flush=True)
        return []
    return [{"name": v[0], "description": v[2],
             "inputSchema": {"type": "object", "properties": v[3]}}
            for v in TOOLS.values()]


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
            send({"jsonrpc": "2.0", "id": rid,
                  "result": {"tools": _tools_payload()}})
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
