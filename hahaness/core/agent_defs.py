"""自定义 subagent 定义加载：.claude/agents/*.md（对位 claude CLI）。

frontmatter：name（缺省用文件名）/ description / tools（逗号串或列表，
缺省 = GENERAL_TOOLS）/ model（可选，经 model_provider_factory 生效）。
正文即该类型的 system_add。发现序（项目覆盖全局、同名覆盖内建）：
$HAHANESS_HOME/agents < cwd/.agent/agents < cwd/.claude/agents。
坏文件跳过 + warning（headless 不因单个定义拒启）。
"""
from __future__ import annotations

from pathlib import Path

from hahaness import hahaness_home
from hahaness.constants import AGENT_DEF_MAX_CHARS
from hahaness.util import get_logger, parse_frontmatter

log = get_logger(__name__)


def load_agent_defs(cwd: Path, home: Path | None = None) -> dict[str, dict]:
    """返回 name → {"tools": [...], "system_add": str, "model": str,
    "description": str}（空表 = 无自定义）。"""
    out: dict[str, dict] = {}
    bases = [home or hahaness_home() / "agents",
             Path.home() / ".claude" / "agents",
             cwd / ".agent" / "agents",
             cwd / ".claude" / "agents"]      # 后者覆盖前者
    for base in bases:
        try:
            files = sorted(base.glob("*.md"))
        except OSError:
            continue
        for f in files:
            try:
                meta, body = parse_frontmatter(
                    f.read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                log.warning("agent 定义读取失败（跳过）：%s %s", f, e)
                continue
            name = str(meta.get("name") or f.stem).strip()
            if not name:
                continue
            tools = meta.get("tools")
            if isinstance(tools, str):
                tools = [t.strip() for t in tools.split(",") if t.strip()]
            if not isinstance(tools, list) or not tools:
                tools = None                    # 缺省 = 通用工具面（task() 兜底）
            out[name] = {
                "tools": [str(t) for t in tools] if tools else None,
                "system_add": body.strip()[:AGENT_DEF_MAX_CHARS],
                "model": str(meta.get("model") or "") or None,
                "description": str(meta.get("description") or ""),
            }
    return out
