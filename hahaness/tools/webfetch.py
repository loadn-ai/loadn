"""WebFetch 工具——httpx 抓取网页 + 正文提取（trafilatura 优先，stdlib 兜底）。

正文提取两级：仓库装有 trafilatura 就用其主内容抽取；否则 stdlib
HTMLParser 去 script/style/标签取文本（无第三方依赖也能跑）。网络层失败
（超时/连接拒绝/DNS）一律收敛成 ToolError 不抛穿——单次抓取失败不该
炸掉 agent 循环。域名黑名单不在此处：权限层管。
"""
from __future__ import annotations

from html.parser import HTMLParser

import httpx

from hahaness.constants import WEBFETCH_MAX_CHARS
from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.tools.truncate import Truncator

try:
    import trafilatura
except ImportError:                          # pragma: no cover - 环境差异
    trafilatura = None

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


class _PlainTextExtractor(HTMLParser):
    """stdlib 兜底提取器：跳过 script/style/head 等无正文区，块级标签换行。"""

    _SKIP = {"script", "style", "head", "noscript", "template", "svg"}
    _BLOCK = {"p", "div", "br", "li", "tr", "table", "section", "article",
              "header", "footer", "nav", "aside", "h1", "h2", "h3", "h4",
              "h5", "h6", "ul", "ol", "blockquote", "pre", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        out: list[str] = []
        for ln in raw.splitlines():
            ln = ln.strip()
            if ln or (out and out[-1]):      # 压掉连续空行
                out.append(ln)
        return "\n".join(out).strip()


def _extract_fallback(html: str) -> str:
    parser = _PlainTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


class WebFetchTool(Tool):
    """抓单个 URL 的可见正文（不做 JS 渲染，交互场景走浏览器栈）。"""

    name = "WebFetch"
    description = (
        "抓取一个 http(s) URL 并提取页面正文（跟随重定向，30s 超时）。"
        "prompt 参数可选（本实现固定返回正文前段，不按提示词改写）。"
        "需要 JS 渲染/登录/交互的页面不在本工具能力内。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL（http/https）"},
            "prompt": {"type": "string",
                       "description": "关注点提示（可选；本实现返回正文原文）"},
        },
        "required": ["url"],
    }
    read_only = True

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        url = args.get("url")
        if not url or not isinstance(url, str):
            raise ToolError("缺少必填参数 url")
        if not url.startswith(("http://", "https://")):
            raise ToolError(f"url 需以 http:// 或 https:// 开头：{url}")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0,
                                         headers={"User-Agent": _UA}) as client:
                resp = await client.get(url)
        except (httpx.HTTPError, OSError) as e:
            raise ToolError(f"抓取失败（网络层）：{url}\n{type(e).__name__}: {e}") from None
        if resp.status_code != 200:
            raise ToolError(f"抓取失败：HTTP {resp.status_code}（{url}）")
        body = resp.text
        ctype = resp.headers.get("content-type", "").lower()
        if "html" in ctype or body.lstrip()[:1] == "<":
            text = self._extract(body)
        else:
            text = body                     # json/plain 等非 HTML 直取
        if not text.strip():
            raise ToolError(f"页面无可见正文（{url}）")
        return Truncator.clip_head(text, WEBFETCH_MAX_CHARS)

    @staticmethod
    def _extract(html: str) -> str:
        """trafilatura 主内容抽取；缺失/抽取为空时 HTMLParser 兜底。"""
        if trafilatura is not None:
            try:
                extracted = trafilatura.extract(html, include_comments=False,
                                                 include_tables=True)
            except Exception:
                extracted = None
            if extracted and extracted.strip():
                return extracted
        return _extract_fallback(html)


tool = WebFetchTool()         # ToolRegistry.default() 收集的模块级实例
