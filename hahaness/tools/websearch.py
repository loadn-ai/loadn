"""WebSearch 工具——env 配置驱动的 bocha/智谱两后端网页搜索。

后端选择在 env（HAHANESS_SEARCH_PROVIDER=bocha|zhipu + HAHANESS_SEARCH_KEY），
两者都缺 → ToolError（能力显式缺失，模型会转告用户而不是瞎猜）。请求/
响应字段与 宿主平台.resources 的 bocha_search/zhipu_search 同构（该实现
已在生产跑通）：bocha 结果在 data.webPages.value（name/url/summary），
智谱在 search_result（title/link/content）。search_std 常无 link 是上游
已知边界，保留空 URL 只给标题摘要。
"""
from __future__ import annotations

import os

import httpx

from hahaness.constants import WEBSEARCH_TOP_K
from hahaness.tools.base import Tool, ToolContext, ToolError

BOCHA_URL = "https://api.bochaai.com/v1/web-search"
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"


class WebSearchTool(Tool):
    """中文网页搜索（bocha / 智谱，env 配置后端）。"""

    name = "WebSearch"
    description = (
        "网页搜索：返回编号列表（标题/URL/摘要），拿到 URL 后用 WebFetch "
        "或浏览器抓正文。后端由部署方 env 配置，未配置时本工具不可用。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索词"},
        },
        "required": ["query"],
    }
    read_only = True

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        query = args.get("query")
        if not query or not isinstance(query, str):
            raise ToolError("缺少必填参数 query")
        provider = os.environ.get("HAHANESS_SEARCH_PROVIDER", "").strip().lower()
        key = os.environ.get("HAHANESS_SEARCH_KEY", "").strip()
        if not provider or not key:
            raise ToolError("搜索后端未配置（HAHANESS_SEARCH_PROVIDER/KEY）")
        if provider == "bocha":
            body = {"query": query, "count": WEBSEARCH_TOP_K}
            hits = await self._search(BOCHA_URL, key, body, self._parse_bocha)
        elif provider == "zhipu":
            body = {"search_engine": "search_std", "search_query": query,
                    "count": WEBSEARCH_TOP_K}
            hits = await self._search(ZHIPU_URL, key, body, self._parse_zhipu)
        else:
            raise ToolError(f"未知搜索后端 {provider!r}（支持 bocha|zhipu）")
        if not hits:
            return f"（无搜索结果：{query}）"
        blocks = [f"{i}. {h['name']}\n{h['url']}\n{h['snippet']}"
                  for i, h in enumerate(hits, 1)]
        return "\n\n".join(blocks)

    async def _search(self, url: str, key: str, body: dict, parser) -> list[dict]:
        """公共请求层：Bearer key、20s 超时、网络/非 200 一律 ToolError。"""
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    url, headers={"Authorization": f"Bearer {key}"}, json=body)
        except (httpx.HTTPError, OSError) as e:
            raise ToolError(f"搜索请求失败（网络层）：{type(e).__name__}: {e}") from None
        if resp.status_code != 200:
            raise ToolError(f"搜索失败：HTTP {resp.status_code} "
                            f"{resp.text[:200]}")
        try:
            return parser(resp.json())
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"搜索响应解析失败：{type(e).__name__}: {e}") from None

    @staticmethod
    def _parse_bocha(d: dict) -> list[dict]:
        pages = ((d.get("data") or {}).get("webPages") or {}).get("value") or []
        return [{"name": it.get("name") or "",
                 "url": it.get("url") or "",
                 "snippet": it.get("summary") or it.get("snippet") or ""}
                for it in pages]

    @staticmethod
    def _parse_zhipu(d: dict) -> list[dict]:
        return [{"name": it.get("title") or "",
                 "url": it.get("link") or "",
                 "snippet": it.get("content") or ""}
                for it in d.get("search_result") or []]


tool = WebSearchTool()        # ToolRegistry.default() 收集的模块级实例
