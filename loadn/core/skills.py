"""Skills 发现单一真相：ContextAssembler 索引与 SkillTool 共用。

发现序（先到先得，同名去重——项目覆盖全局）：cwd/.claude/skills >
cwd/.loadn/skills（兼容旧 .agent/skills）> $LOADN_HOME/skills。SkillInfo 带 path 供按需
加载正文（SkillTool），索引侧只用 name+description。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loadn import loadn_home
from loadn.util import get_logger, parse_frontmatter

log = get_logger(__name__)


@dataclass
class SkillInfo:
    name: str
    description: str
    path: Path                    # SKILL.md 全路径


def skill_bases(cwd: Path) -> list[Path]:
    """发现根（顺序即优先级；~/.claude/skills = Claude 用户全局 skill，
    双引擎共享同一套全局 skill）。

    P0-2 信任门：项目级三个根未过信任门即剔除（clone 的仓库不得自带
    注入指令的 SKILL.md）；用户全局两个根不受影响。
    """
    from loadn.core import trust
    bases = [cwd / ".claude" / "skills",
             cwd / ".loadn" / "skills",
             cwd / ".agent" / "skills"]
    ok, why = trust.gate(cwd)
    if not ok:
        log.warning("信任门：项目级 skills 已跳过（%s）", why)
        bases = []
    return bases + [Path.home() / ".claude" / "skills",
                    loadn_home() / "skills"]


def discover_skills(cwd: Path) -> dict[str, SkillInfo]:
    """扫描发现根：name → SkillInfo（同名先到先得；坏 SKILL.md 跳过）。"""
    out: dict[str, SkillInfo] = {}
    for base in skill_bases(cwd):
        try:
            entries = sorted(base.iterdir(), key=lambda x: x.name)
        except OSError:
            continue
        for d in entries:
            skill_md = d / "SKILL.md"
            if not (d.is_dir() or d.is_symlink()) or not skill_md.exists():
                continue
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, _ = parse_frontmatter(text)
            name = str(meta.get("name") or d.name)
            if name in out:
                continue
            out[name] = SkillInfo(name=name,
                                  description=str(meta.get("description") or ""),
                                  path=skill_md)
    return out


def load_skill_body(info: SkillInfo, args: str = "",
                    max_chars: int = 16_000) -> str:
    """SKILL.md 正文（剥 frontmatter、$ARGUMENTS 替换、clip）。"""
    try:
        text = info.path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"skill 正文读取失败：{info.path}（{e}）"
    _, body = parse_frontmatter(text)
    if args:
        body = body.replace("$ARGUMENTS", args)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…[截断]"
    return body
