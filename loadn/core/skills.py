"""Skills 发现单一真相：ContextAssembler 索引与 SkillTool 共用。

发现序（先到先得，同名去重——项目覆盖全局）：cwd/.agents/skills
（agentskills.io 开放标准目录，Vercel `npx skills add` 等工具的落点）>
cwd/.claude/skills > cwd/.loadn/skills（兼容旧 .agent/skills）>
$LOADN_HOME/skills。SkillInfo 带 path 供按需
加载正文（SkillTool），索引侧只用 name+description。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loadn import loadn_home
from loadn.skilllock import (  # noqa: F401  再导出：锁存储在顶层（webui 共用），消费者导入路径不变
    LOCK_VERSION,
    load_locks,
    lock_paths,
    skill_hash,
)
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

    P0-2 信任门：项目级四个根（.agents/.claude/.loadn/.agent）未过信任门
    即剔除（clone 的仓库不得自带注入指令的 SKILL.md——agentskills.io
    供给不豁免）；用户全局两个根不受影响。
    """
    from loadn.core import trust
    bases = [cwd / ".agents" / "skills",
             cwd / ".claude" / "skills",
             cwd / ".loadn" / "skills",
             cwd / ".agent" / "skills"]
    ok, why = trust.gate(cwd)
    if not ok:
        log.warning("信任门：项目级 skills 已跳过（%s）", why)
        bases = []
    return bases + [Path.home() / ".claude" / "skills",
                    loadn_home() / "skills"]


def discover_skills(cwd: Path, *, enforce_lock: bool = True) -> dict[str, SkillInfo]:
    """扫描发现根：name → SkillInfo（同名先到先得；坏 SKILL.md 跳过）。

    外部来源 skill（SKILL.md frontmatter 带 source 字段）走供应链锁
    （_lock_ok）：未命中锁文件或 sha256 不符 → 拒索引（fail-closed，
    rug-pull 防护）。锁校验在 P0-2 信任门之后（bases 已过 gate）。
    enforce_lock=False 供 `skills lock` 生成器扫全量（否则未锁外部 skill
    进不了锁——鸡生蛋）。
    """
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
            if enforce_lock and str(meta.get("source") or "").strip() \
                    and not _lock_ok(name, skill_md):
                continue                       # 外部来源未过锁：拒索引
            out[name] = SkillInfo(name=name,
                                  description=str(meta.get("description") or ""),
                                  path=skill_md)
    return out


# ---------------------------------------------------------------- 供应链锁（P0-3）
# 存储原语（lock_paths/load_locks/skill_hash/写入面）在顶层 loadn/skilllock.py
# ——进程边界禁 webui 入 loadn.core，而安装/编辑面要写锁（P1 打通）。


def _lock_ok(name: str, skill_md: Path) -> bool:
    """外部来源 skill 的锁校验：命中锁且哈希一致才放行。"""
    entry = load_locks().get(name)
    if entry is None:
        log.warning("skill 供应链锁：%s 声明外部来源但不在 skills.lock.json——"
                    "拒索引（防 rug-pull）。信任当前内容请运行 "
                    "`loadn skills lock` 生成锁", name)
        return False
    try:
        ok = entry.get("computedHash") == skill_hash(skill_md)
    except OSError:
        return False
    if not ok:
        log.warning("skill 供应链锁：%s 内容与锁不符（rug-pull 风险）——拒索引。"
                    "确认为有意变更后 `loadn skills lock --update` 更新锁", name)
    return ok


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
