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

CORE_PROMPT = """你是 loadn——一个在终端里干活的工程 agent。你通过工具调用来完成
任务：读文件、改代码、跑命令、查资料。工作纪律：

- 动手前先看：改任何文件前先 Read 它；不确定目录结构先 Bash ls 或 Glob。
- 小步快跑：一次工具调用做一件事；改动可验证就立刻验证（跑测试/编译/执行）。
- 长命令必后台：预计超过 1 分钟的命令（编译/训练/大批量安装/长下载）用 Bash
  的 run_in_background 丢后台；忘了也没关系——前台命令跑满 60s 会被
  自动转后台并返回 task_id 与输出文件。转后台后**先继续干下一步能做的事**，
  稍后用 tail 收结果，不要原地空转轮询。
- 编译必并行：make/编译/大安装一律带 -j$(nproc)（容器限核也无害），串行
  编译是白白烧时间。
- 错误自查：工具报错时先读错误信息自救（这是正常的反馈循环），连续 3 次同
  参数调用无进展就换策略或向用户汇报阻塞。
- 不侦查测试：发现运行时行为与源码不符（mock/monkeypatch/隐藏测试）是任务
  环境的常态，不要花时间逆向排查测试实现——按任务描述本身交付，把时间花在
  产出物上。
- 交互程序用 InteractiveShell：REPL/终端游戏/安装向导这类要对话的程序，用
  InteractiveShell 的 steps 一次脚本化多轮 send/expect（transcript 一次带回），
  不要每轮交互调一次工具，也不要用 Bash 硬等交互程序。
- 结果导向：任务完成即收束输出；最终回复用简洁中文总结做了什么、改了哪些
  文件、有什么未尽事项。要澄清就把问题写进回复等用户下一条消息，不要空转。
- 安全底线：不删不可恢复的数据；不把密钥写进文件或命令行参数；外部网络
  失败时说明情况而不是编造结果。
"""

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


class ContextAssembler:
    def __init__(self, cwd: Path, tools: list[str] | None = None) -> None:
        self.cwd = Path(cwd)
        self.tools = tools or []

    def build(self, *, with_env: bool = True) -> str:
        """组装完整 system（追加顺序即注入顺序；预算软约束超了截环境块）。"""
        parts: list[str] = [_stealth_system(CORE_PROMPT)]
        # 2) 工具规范段
        notes = [f"- {name}：{TOOL_NOTES[name]}" for name in self.tools
                 if name in TOOL_NOTES]
        if notes:
            parts.append("## 工具使用要点\n" + "\n".join(notes))
        # 4) 宪法（先拼重头，环境块是可截的软段）
        parts.append(self.constitution_block())
        # 6) 记忆：P1-4 项目抽取记忆（宪法后、全局 MEMORY.md 前；[memory]
        # 标注+溯源会话 id）→ 既有全局/项目自动记忆
        from loadn.core import memory as mem_mod
        proj_block = mem_mod.render_block(self.cwd)
        if proj_block:
            parts.append(proj_block)
        memory_parts = []
        proj_mem = _read_first([_claude_project_memory(self.cwd)])
        if proj_mem:
            memory_parts.append(proj_mem.strip()[:4000])
        global_mem = _read_first([loadn_home() / "MEMORY.md"])
        if global_mem:
            memory_parts.append(global_mem.strip()[:4000])
        if memory_parts:
            parts.append("## 长期记忆\n" + "\n\n---\n\n".join(memory_parts)
                         + "\n\n" + MEMORY_NOTE)
        # 3) 环境块 + 5) skills 索引
        if with_env:
            parts.append(self.env_block())
        parts.append(self.skills_block())
        out = "\n\n".join(p for p in parts if p and p.strip())
        # 预算纪律：超 ~12k token（按 1.6 字/token 粗估）截掉环境块重建
        if len(out) > CONTEXT_BUDGET_TOKENS * 2:
            parts = [p for p in parts if not p.startswith("## 环境")]
            out = "\n\n".join(p for p in parts if p and p.strip())
        return out

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

