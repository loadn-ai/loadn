"""SkillHub 市场发现层（只读，安装不经过市场）。

- search：skillhub.club 公开搜索 API（无文档，schema 变化靠规范化层兜底，
  失败降级返回 error 由 UI 提示；安装链路纯 GitHub codeload，不受其影响）
- catalog：jsdelivr data API 列 anthropics/skills 官方技能（api.github.com
  限流严格，浏览目录走 jsdelivr——实测无限流可用）

repo_url 片段约定（#a~b~c = 仓库内子目录）由 skills.parse_repo_url 解析。
"""
from __future__ import annotations

import re

from ..util import get_logger

log = get_logger(__name__)

SKILLHUB_SEARCH = "https://www.skillhub.club/api/search"
JSDELIVR_FLAT = "https://data.jsdelivr.com/v1/packages/gh/{repo}@main?structure=flat"
OFFICIAL_REPO = "anthropics/skills"

_GH_REPO = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)")


def _http_get(url: str, timeout: float = 10.0) -> dict | list | None:
    import httpx
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "loadn-webui/0.1"})
        if r.status_code != 200:
            log.warning("市场请求 %s → HTTP %s", url, r.status_code)
            return None
        return r.json()
    except Exception as e:    # noqa: BLE001 —— 市场不可达是常态，降级不炸
        log.warning("市场请求失败 %s: %s", url, e)
        return None


def search(q: str, limit: int = 30) -> dict:
    """skillhub 搜索 → 规范化列表；非 GitHub 条目标 installable=False。"""
    q = (q or "").strip()
    if not q:
        return {"skills": []}
    data = _http_get(f"{SKILLHUB_SEARCH}?q={q}")
    if data is None:
        return {"error": "skillhub 暂不可达（稍后再试，或用 GitHub 链接直装）", "skills": []}
    out = []
    for s in (data.get("skills") or [])[:limit]:
        if not isinstance(s, dict):
            continue
        repo_url = str(s.get("repo_url") or "")
        gh = _GH_REPO.match(repo_url)
        out.append({
            "name": str(s.get("name") or "")[:80],
            "slug": str(s.get("slug") or "")[:120],
            "description": str(s.get("description_zh") or s.get("description") or "")[:300],
            "category": str(s.get("category") or ""),
            "author": str(s.get("author") or ""),
            "stars": s.get("github_stars") or 0,
            "repo_url": repo_url,
            "installable": bool(gh and gh.group(2)),
        })
    return {"skills": out}


def catalog(repo: str = OFFICIAL_REPO) -> dict:
    """官方/任意仓库的技能目录（jsdelivr flat 文件树里找 */SKILL.md）。"""
    data = _http_get(JSDELIVR_FLAT.format(repo=repo))
    if data is None:
        return {"error": f"目录拉取失败（{repo}）", "skills": []}
    names: list[str] = []
    for f in data.get("files") or []:
        name = (f or {}).get("name") or ""
        if name.endswith("/SKILL.md"):
            parts = [p for p in name.strip("/").split("/") if p]
            if len(parts) >= 2:            # …/<skill>/SKILL.md → 取 skill 名
                names.append(parts[-2])
    return {"repo": repo, "skills": sorted(set(names))}
