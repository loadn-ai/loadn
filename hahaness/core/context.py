"""ContextAssembler（工程详设 §4.3）：system 提示六段注入，预算 ~12k token。

注入顺序（就近优先/常驻优先）：
  1. 核心 system prompt（角色/工作流/工具规范引用/安全规则）
  2. 工具使用规范段（每工具 1-2 句；完整 schema 走 tools 字段不占 system）
  3. 环境块：cwd / os / git branch+status（截断）/ 目录树（2 层 ≤100 项）
  4. 宪法合并：~/.agent/AGENT.md → 项目 CLAUDE.md/AGENTS.md（cwd 存在即读，
     常见 harness 双写同文，天然兼容）
  5. Skills 索引：仅 name+description（正文由 SkillLoader 按需注入——本实现中
     索引即门牌，agent 用 Read 读 .claude/skills/<name>/SKILL.md 取正文）
  6. 记忆块：~/.agent/MEMORY.md（存在时）
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from hahaness import hahaness_home
from hahaness.constants import CONTEXT_BUDGET_TOKENS, ENV_TREE_DEPTH, ENV_TREE_MAX_ENTRIES

CORE_PROMPT = """你是 hahaness——一个在终端里干活的工程 agent。你通过工具调用来完成
任务：读文件、改代码、跑命令、查资料。工作纪律：

- 动手前先看：改任何文件前先 Read 它；不确定目录结构先 Bash ls 或 Glob。
- 小步快跑：一次工具调用做一件事；改动可验证就立刻验证（跑测试/编译/执行）。
- 错误自查：工具报错时先读错误信息自救（这是正常的反馈循环），连续 3 次同
  参数调用无进展就换策略或向用户汇报阻塞。
- 结果导向：任务完成即收束输出；最终回复用简洁中文总结做了什么、改了哪些
  文件、有什么未尽事项。要澄清就把问题写进回复等用户下一条消息，不要空转。
- 安全底线：不删不可恢复的数据；不把密钥写进文件或命令行参数；外部网络
  失败时说明情况而不是编造结果。
"""

TOOL_NOTES = {
    "Bash": "执行 shell 命令（默认 120s 超时，长任务用 run_in_background）。输出超 3 万字符会被截断。",
    "Read": "读文件（带行号输出，默认前 2000 行；图片返回视觉内容）。",
    "Write": "整文件覆盖写（已存在的文件必须先 Read 过）。",
    "Edit": "精确串替换（old_string 必须唯一含缩进；支持 replace_all）。",
    "Grep": "内容搜索（默认 rg；命中超 200 条会截断，建议收窄）。",
    "Glob": "文件名模式匹配（按修改时间倒序，≤100 条）。",
    "Task": "派子代理（独立上下文，只回传最终结果；搜索/调研优先用它防污染主上下文）。",
    "WebFetch": "抓网页正文（≤5 万字符）。",
    "WebSearch": "网页搜索（top10）。",
    "TodoWrite": "任务清单全量覆盖写（复杂任务先列清单，推进即更新）。",
}

MEMORY_NOTE = "以上记忆是长期偏好，适用时遵循；与用户当次指令冲突时以当次为准。"


class ContextAssembler:
    def __init__(self, cwd: Path, tools: list[str] | None = None) -> None:
        self.cwd = Path(cwd)
        self.tools = tools or []

    def build(self, *, with_env: bool = True) -> str:
        """组装完整 system（追加顺序即注入顺序；预算软约束超了截环境块）。"""
        parts: list[str] = [CORE_PROMPT.strip()]
        # 2) 工具规范段
        notes = [f"- {name}：{TOOL_NOTES[name]}" for name in self.tools
                 if name in TOOL_NOTES]
        if notes:
            parts.append("## 工具使用要点\n" + "\n".join(notes))
        # 4) 宪法（先拼重头，环境块是可截的软段）
        parts.append(self.constitution_block())
        # 6) 记忆
        memory = _read_first([hahaness_home() / "MEMORY.md"])
        if memory:
            parts.append("## 长期记忆\n" + memory.strip()[:4000] + "\n\n" + MEMORY_NOTE)
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
        """宪法合并：项目 CLAUDE.md / AGENTS.md（双写同文的工作区取其一）。"""
        texts = []
        for name in ("CLAUDE.md", "AGENTS.md"):
            t = _read_first([self.cwd / name])
            if t:
                texts.append(t)
                break
        if not texts:
            return ""
        return "## 项目宪法（最高优先级的工作区规则）\n" + _clip(texts[0], 60000)

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
        entries: list[str] = []
        for base in (self.cwd / ".claude" / "skills", self.cwd / ".agent" / "skills",
                     hahaness_home() / "skills"):
            entries.extend(_skill_index(base))
        if not entries:
            return ""
        return ("## 可用 Skills（用 Read 读 <skill>/SKILL.md 获取正文后再按其指引干活）\n"
                + "\n".join(entries))


# ---------------------------------------------------------------- 辅助
def _read_first(paths: list[Path]) -> str:
    for p in paths:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


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
              ".cache", ".npm", ".claude", ".agent", ".opencode", ".fake"}


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


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _skill_index(base: Path) -> list[str]:
    """skills 目录索引：name+description（frontmatter 提取）。"""
    entries: list[str] = []
    try:
        for d in sorted(base.iterdir(), key=lambda x: x.name):
            skill_md = d / "SKILL.md"
            if not (d.is_dir() or d.is_symlink()) or not skill_md.exists():
                continue
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _FM_RE.match(text)
            name, desc = d.name, ""
            if m:
                for ln in m.group(1).splitlines():
                    if ln.startswith("name:"):
                        name = ln.split(":", 1)[1].strip()
                    elif ln.startswith("description:"):
                        desc = ln.split(":", 1)[1].strip()
            entries.append(f"- {name}：{_clip(desc, 160)}")
    except OSError:
        pass
    return entries
