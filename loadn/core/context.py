"""ContextAssembler（工程详设 §4.3）：system 提示六段注入，预算 ~12k token。

注入顺序（就近优先/常驻优先）：
  1. 核心 system prompt（角色/工作流/工具规范引用/安全规则）
  2. 工具使用规范段（每工具 1-2 句；完整 schema 走 tools 字段不占 system）
  3. 环境块：cwd / os / git branch+status（截断）/ 目录树（2 层 ≤100 项）
  4. 宪法合并：用户级回退链 $LOADN_HOME/AGENT.md → ~/.claude/CLAUDE.md
     （双引擎共享用户宪法）置顶 + 祖先链 CLAUDE.md/AGENTS.md（边界=git 根；
     repo 外 home 之下到 home；再外只读 cwd；远在前近在后；@import 行级展开）
  5. Skills 索引：仅 name+description（正文经 Skill 工具按需注入）；
     发现根含 ~/.claude/skills（Claude 用户全局 skill 共享）
  6. 记忆块：per-project ~/.claude/projects/<slug>/memory/MEMORY.md（Claude
     自动记忆，双引擎共享演化）+ 全局 $LOADN_HOME/MEMORY.md
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loadn import loadn_home
from loadn.constants import (
    BASH_AUTO_BG_S,
    CONTEXT_BUDGET_TOKENS,
    ENV_TREE_DEPTH,
    ENV_TREE_MAX_ENTRIES,
    MEMORY_FILE_MAX_CHARS,
    MEMORY_IMPORT_DEPTH,
    MEMORY_IMPORT_MAX_CHARS,
    MEMORY_TOTAL_MAX_CHARS,
)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _core_prompt_for(model: str | None) -> str:
    """per-model prompt 变体（P1-7，codex per-model prompt 文件同构）。

    slug 归一化复用 P1-5（glm-5.3[1m]→glm-5.3）；未知 slug 回退 generic。
    """
    slug = (model or "").split("[", 1)[0].strip() or "generic"
    p = _PROMPTS_DIR / "models" / f"{slug}.md"
    if not p.exists():
        p = _PROMPTS_DIR / "models" / "generic.md"
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return "你是 loadn——一个在终端里干活的工程 agent。"
CORE_PROMPT = _core_prompt_for(None)      # 兼容旧引用（伪装变换等）

TOOL_NOTES = {
    "Bash": f"执行 shell 命令（前台 {BASH_AUTO_BG_S}s 自动转后台；可带 cwd/env 每调用参数，cwd 限工作区子树内）。预计 >1 分钟的命令主动 run_in_background；转后台后先干别的、稍后 tail 输出文件收结果。编译/make/安装必带 -j$(nproc)。输出超 3 万字符截断。",
    "Read": "读文件（带行号输出，默认前 2000 行；图片返回视觉内容）。",
    "Write": "整文件覆盖写（已存在的文件必须先 Read 过）。",
    "Edit": "精确串替换（old_string 必须唯一含缩进；支持 replace_all）。",
    "MultiEdit": "单文件多处原子编辑（按序应用，任一步失败全部不落盘；成组小改动优先用它）。",
    "NotebookEdit": "编辑 Jupyter notebook 的 cell（replace/insert/delete，按 cell_id）。",
    "Grep": "内容搜索（默认 rg；命中超 200 条会截断，建议收窄）。",
    "Glob": "文件名模式匹配（按修改时间倒序，≤100 条）。",
    "Task": "派子代理（独立上下文，只回传最终结果；搜索/调研优先用它防污染主上下文）。",
    "WebFetch": "抓网页正文（≤5 万字符）。",
    "WebSearch": "网页搜索（top10）。",
    "TodoWrite": "任务清单全量覆盖写（复杂任务先列清单，推进即更新）。",
    "InteractiveShell": "pty 会话跑交互程序（REPL/游戏/向导）：一次调用 steps 多轮 send/expect，别用 Bash 硬等交互程序。",
}

MEMORY_NOTE = "以上记忆是长期偏好，适用时遵循；与用户当次指令冲突时以当次为准。"

# 自报身份句（CORE_PROMPT 首句）：伪装模式下剥掉，换 CC 身份句
_IDENTITY_SENTENCE = "你是 loadn——一个在终端里干活的工程 agent。"


def _stealth_system(core_prompt: str) -> str:
    """伪装模式（LOADN_STEALTH=cc）下的 system 变换：

    剥自报身份句（provider 层另前置 CC 官方身份句），全量清扫 "loadn"
    字样——自曝客户端身份是最直接的 prompt 指纹。未开启时原样返回。
    """
    from loadn.providers.fingerprint import stealth_mode
    if not stealth_mode():
        return core_prompt.strip()
    text = core_prompt.strip()
    if text.startswith(_IDENTITY_SENTENCE):
        text = text[len(_IDENTITY_SENTENCE):].lstrip()
    return text.replace("loadn", "Claude Code")


# ---------------------------------------------------------------- P2-6 节
@dataclass(frozen=True)
class SectionSpec:
    """节声明（数据表驱动——顺序即提示词最终顺序）。

    budget_tokens：节内截断预算（0=不限；2 chars/token 粗估）。
    prune_priority：总预算超限时整节裁的次序，**大者先裁；0=永不裁**
    （宪法与核心身份节为 0——工作区规则不因预算让路）。
    """
    id: str
    budget_tokens: int = 0
    prune_priority: int = 0


# 默认节表（序=最终序；与 P2-6 前的 build() 追加顺序逐位一致——默认零变更）
SECTION_SPECS: list[SectionSpec] = [
    SectionSpec("core"),                                   # 身份/角色（永不裁）
    SectionSpec("tools", prune_priority=50),               # 工具使用要点
    SectionSpec("constitution"),                           # 宪法（永不裁）
    SectionSpec("repomap", prune_priority=80),             # 仓库地图（P1-8）
    SectionSpec("memory_project", prune_priority=60),      # P1-4 项目记忆
    SectionSpec("memory", prune_priority=70),              # 长期记忆
    SectionSpec("env", prune_priority=90),                 # 环境块
    SectionSpec("skills", prune_priority=100),             # skills 索引（先裁）
]


def _pos_int(v, default: int = 0) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _section_config(cwd: Path) -> dict:
    """context_sections.json（全局 < 项目 .loadn/，项目覆盖；纯数据面）。"""
    import json
    merged: dict = {}
    for p in (loadn_home() / "context_sections.json",
              cwd / ".loadn" / "context_sections.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict) or isinstance(v, list):
                        merged[k] = v
        except (OSError, json.JSONDecodeError):
            continue
    return merged


def _resolve_specs(cfg: dict) -> tuple[list[SectionSpec], dict]:
    """配置覆盖 → (节序, 覆盖面)。未知节 id 忽略（笔误不炸）。"""
    by_id = {s.id: s for s in SECTION_SPECS}
    overrides = {k: v for k, v in cfg.items()
                 if k in by_id and isinstance(v, dict)}
    order = list(SECTION_SPECS)
    want = cfg.get("order")
    if isinstance(want, list):                    # 序覆盖：已知节按给定序前置
        seen = [w for w in want if w in by_id]
        seen_set = set(seen)
        order = ([by_id[w] for w in seen]
                 + [s for s in SECTION_SPECS if s.id not in seen_set])
    return order, overrides


class ContextAssembler:
    """P2-6 起分节组装（ZCode builder.ts 同构）：节声明数据表驱动。

    - 节顺序 = 提示词最终顺序（:data:`SECTION_SPECS`；context_sections.json
      可覆盖 enabled/budget_tokens/prune_priority/order）
    - 每节独立构建，**单节失败只跳过该节 + warning**（不崩组装）
    - 节预算超限 → 节内截断标 ``[section truncated]``
    - 总预算（CONTEXT_BUDGET_TOKENS）超限 → 按 prune_priority **整节裁**：
      大者先裁（skills 索引最先、环境块次之）；**宪法/核心节永不裁**
      （priority 0）
    - ``last_sections``：本次组装的节元数据（id/chars/truncated/dropped
      ——P2-3 事件化的打样面：节=事件单位）
    """

    def __init__(self, cwd: Path, tools: list[str] | None = None,
                 model: str | None = None, *,
                 with_repomap: bool = False,
                 mentioned_files: set[str] | None = None) -> None:
        self.model = model             # P1-7：per-model prompt 变体选择
        self.with_repomap = with_repomap       # P1-8：仓库地图节
        self.mentioned_files = mentioned_files or set()
        self.cwd = Path(cwd)
        self.tools = tools or []
        self.last_sections: list[dict] = []    # P2-6：节元数据（打样面）

    # ------------------------------------------------------------ 组装
    def build(self, *, with_env: bool = True) -> str:
        self.last_memory_hits: list = []   # P7：本轮实际注入的记忆清单
        # （memory_project 节构建时填充；节被裁/关则保持空——hits 恰为注入）
        """组装完整 system（节序即注入顺序；预算纪律见类 docstring）。"""
        from loadn.util import get_logger
        log = get_logger(__name__)
        order, overrides = _resolve_specs(_section_config(self.cwd))
        built: list[tuple[SectionSpec, str]] = []
        for spec in order:
            o = overrides.get(spec.id) or {}
            if o.get("enabled") is False:          # 节开关（数据表/配置面）
                continue
            if spec.id == "env" and not with_env:
                continue
            if spec.id == "repomap" and not self.with_repomap:
                continue
            try:
                text = self._build_section(spec.id)
            except Exception as e:  # noqa: BLE001 — 单节失败只跳过该节
                log.warning("context 节 %s 构建失败（跳过）：%r", spec.id, e)
                continue
            if not text or not text.strip():
                continue
            budget = _pos_int(o.get("budget_tokens"), spec.budget_tokens)
            if budget and len(text) > budget * 2:   # 2 chars/token 粗估
                text = text[:budget * 2] + "\n[section truncated]"
            built.append((spec, text))
        # 总预算：可裁节按优先级降序整节丢，直到回到预算内（无可裁即止——
        # 宪法/核心 priority 0 永不裁）
        total = sum(len(t) for _, t in built)
        drop_ids: set[str] = set()
        prunable = sorted((b for b in built if b[0].prune_priority > 0),
                          key=lambda b: -b[0].prune_priority)
        for spec, text in prunable:
            if total <= CONTEXT_BUDGET_TOKENS * 2:
                break
            drop_ids.add(spec.id)
            total -= len(text)
        self.last_sections = [
            {"id": s.id, "chars": len(t),
             "truncated": t.endswith("[section truncated]"),
             "dropped": s.id in drop_ids} for s, t in built]
        if "memory_project" in drop_ids:
            self.last_memory_hits = []   # 节被预算裁掉=没注入，hits 清空
        return "\n\n".join(t for s, t in built if s.id not in drop_ids)

    def _build_section(self, sid: str) -> str:
        """单节构建（异常由 build 捕获——单节失败不崩组装）。"""
        if sid == "core":
            return _stealth_system(_core_prompt_for(self.model))
        if sid == "tools":
            notes = [f"- {name}：{TOOL_NOTES[name]}" for name in self.tools
                     if name in TOOL_NOTES]
            return "## 工具使用要点\n" + "\n".join(notes) if notes else ""
        if sid == "constitution":
            return self.constitution_block()
        if sid == "repomap":
            from loadn.core import repomap as rm
            return rm.get_repo_map(self.cwd,
                                   mentioned=self.mentioned_files) or ""
        if sid == "memory_project":
            from loadn.core import memory as mem_mod
            self.last_memory_hits = mem_mod.injected_hits(self.cwd)
            return mem_mod.render_block(self.cwd) or ""
        if sid == "memory":
            memory_parts = []
            proj_mem = _read_first([_claude_project_memory(self.cwd)])
            if proj_mem:
                memory_parts.append(proj_mem.strip()[:4000])
            global_mem = _read_first([loadn_home() / "MEMORY.md"])
            if global_mem:
                memory_parts.append(global_mem.strip()[:4000])
            if not memory_parts:
                return ""
            return ("## 长期记忆\n" + "\n\n---\n\n".join(memory_parts)
                    + "\n\n" + MEMORY_NOTE)
        if sid == "env":
            return self.env_block()
        if sid == "skills":
            return self.skills_block()
        return ""

    # ------------------------------------------------------------ 分段
    def constitution_block(self) -> str:
        """宪法合并（就近覆盖语义）：

        ① 用户级 $LOADN_HOME/AGENT.md（置顶）
        ② 祖先链 CLAUDE.md/AGENTS.md：从边界根（git repo 根；repo 外为 home
           之下到 home；再外只读 cwd——避免捡跨项目噪声）到 cwd 逐层收集，
           远在前近在后；每层 CLAUDE.md 优先 AGENTS.md（双写同文取其一）。
        链上文件支持 @import（相对所在文件解析，深度/环受控）。
        """
        sections: list[str] = []
        # 用户级宪法回退链：自有 AGENT.md → Claude 用户级 ~/.claude/CLAUDE.md
        # （双引擎共用同一份用户宪法——切引擎行为一致）
        for base, name in ((loadn_home(), "AGENT.md"),
                           (Path.home() / ".claude", "CLAUDE.md")):
            user_agent = _read_first([base / name])
            if user_agent:
                sections.append("### 用户级宪法\n"
                                + _clip(_expand_imports(user_agent, base),
                                        MEMORY_FILE_MAX_CHARS))
                break
        for path in _chain_files(_memory_boundary(self.cwd, Path.home()), self.cwd):
            text = _read_first([path])
            if not text:
                continue
            where = "." if path.parent == self.cwd else str(path.parent)
            sections.append(f"### 项目宪法：{where}（{path.name}）\n"
                            + _clip(_expand_imports(text, path.parent),
                                    MEMORY_FILE_MAX_CHARS))
        if not sections:
            return ""
        total = 0
        kept: list[str] = []
        for sec in sections:   # 总量超限截最远端（从后往前保近处）
            total += len(sec)
            kept.append(sec)
        while kept and total > MEMORY_TOTAL_MAX_CHARS:
            total -= len(kept[0])
            kept.pop(0)
        return "## 宪法（最高优先级的工作区规则）\n\n" + "\n\n".join(kept)

    def env_block(self) -> str:
        lines = ["## 环境",
                 f"- cwd: {self.cwd}",
                 f"- os: {os.uname().sysname} {os.uname().release}"]
        branch, status = _git_info(self.cwd)
        if branch:
            lines.append(f"- git: {branch}" + (f"（{status}）" if status else ""))
        tree = _dir_tree(self.cwd)
        if tree:
            lines.append("- 目录结构（2 层）：\n" + tree)
        return "\n".join(lines)

    def skills_block(self) -> str:
        from loadn.core.skills import discover_skills
        skills = discover_skills(self.cwd)
        if not skills:
            return ""
        entries = [f"- {s.name}：{_clip(s.description, 160)}"
                   for s in sorted(skills.values(), key=lambda s: s.name)]
        return ("## 可用 Skills（调 Skill 工具（command=<name>）加载正文，"
                "再按其指引干活）\n" + "\n".join(entries))


# ---------------------------------------------------------------- 辅助
def _read_first(paths: list[Path]) -> str:
    for p in paths:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _claude_project_memory(cwd: Path) -> Path:
    """Claude Code 的 per-project 自动记忆路径（~/.claude/projects/<slug>/memory/
    MEMORY.md；slug = cwd 绝对路径 "/"→"-"，实测路径 slug 化即此规则）。
    双引擎共享：claude 跑出来的记忆 loadn 直接继承，反之亦然。"""
    slug = str(cwd.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory" / "MEMORY.md"


def _memory_boundary(cwd: Path, home: Path) -> Path:
    """宪法收集上界：git repo 根 > home（cwd 在 home 下时）> cwd。

    .git 可以是 file（worktree 形态）。两者皆非（如 /tmp 裸目录）时只读
    cwd 一份——保守，避免一路捡到 / 下的跨项目噪声。
    """
    for d in (cwd, *cwd.parents):
        if (d / ".git").exists():
            return d
        if d == home:
            return home
        if d == d.parent:   # 到 /
            break
    return cwd


def _chain_files(boundary: Path, cwd: Path) -> list[Path]:
    """boundary → cwd 逐层收集 CLAUDE.md/AGENTS.md（远→近；每层取一；去重）。"""
    if boundary != cwd and boundary not in cwd.parents:
        return []
    dirs: list[Path] = []
    d = cwd
    while True:
        dirs.append(d)
        if d == boundary or d == d.parent:
            break
        d = d.parent
    dirs.reverse()
    out: list[Path] = []
    seen: set[Path] = set()
    for dd in dirs:
        for name in ("CLAUDE.md", "AGENTS.md"):
            p = (dd / name).resolve()
            if p in seen:
                continue
            if p.is_file():
                seen.add(p)
                out.append(p)
                break
    return out


_IMPORT_RE = re.compile(r"^@(\S+)\s*$", re.MULTILINE)


def _expand_imports(text: str, base_dir: Path, depth: int = 0,
                    seen: set | None = None) -> str:
    """@import 解析：行级 `@path` 替换为文件内容（相对 base_dir；~ 展开）。

    纪律：深度 ≤ MEMORY_IMPORT_DEPTH、跨收集全程共享 seen 防环、缺失文件
    留一行注释跳过（headless 不因缺引用拒启）、单片 clip。
    """
    seen = seen if seen is not None else set()
    if depth > MEMORY_IMPORT_DEPTH:
        return text

    def _sub(m: re.Match) -> str:
        spec = m.group(1)
        if depth + 1 > MEMORY_IMPORT_DEPTH:
            return f"<!-- @import 超深度未展开：{spec} -->"
        p = Path(spec).expanduser()
        if not p.is_absolute():
            p = base_dir / p
        p = p.resolve()
        if p in seen or not p.is_file():
            return f"<!-- @import 未解析：{spec} -->"
        seen.add(p)
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return f"<!-- @import 读取失败：{spec} -->"
        return _clip(_expand_imports(body, p.parent, depth + 1, seen),
                     MEMORY_IMPORT_MAX_CHARS)

    return _IMPORT_RE.sub(_sub, text)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…[截断]"


def _git_info(cwd: Path) -> tuple[str, str]:
    try:
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                cwd=str(cwd), capture_output=True, text=True,
                                timeout=5).stdout.strip()
        if not branch:
            return "", ""
        st = subprocess.run(["git", "status", "--porcelain"], cwd=str(cwd),
                            capture_output=True, text=True, timeout=5).stdout
        n = len([ln for ln in st.splitlines() if ln.strip()])
        return branch, (f"{n} 处未提交改动" if n else "clean")
    except (OSError, subprocess.SubprocessError):
        return "", ""


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "chrome-profile",
              ".cache", ".npm", ".claude", ".agent", ".loadn", ".opencode", ".fake"}


def _dir_tree(cwd: Path) -> str:
    """2 层目录树（≤ENV_TREE_MAX_ENTRIES 项；隐藏/重目录跳过）。"""
    out: list[str] = []
    try:
        for p in sorted(cwd.iterdir(), key=lambda x: x.name):
            if len(out) >= ENV_TREE_MAX_ENTRIES:
                out.append("…（更多略）")
                break
            name = p.name
            if name in _SKIP_DIRS or name.startswith("."):
                continue
            if p.is_dir():
                out.append(f"{name}/")
                try:
                    for q in sorted(p.iterdir(), key=lambda x: x.name)[:ENV_TREE_DEPTH * 8]:
                        if len(out) >= ENV_TREE_MAX_ENTRIES:
                            break
                        if q.name in _SKIP_DIRS or q.name.startswith("."):
                            continue
                        out.append(f"  {q.name}{'' if q.is_file() else '/'}")
                except OSError:
                    continue
            else:
                out.append(name)
    except OSError:
        pass
    return "\n".join(out)

