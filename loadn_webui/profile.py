"""agent 角色注册表（承袭 papergo/profile.py 的 registry.yaml + md 两级注入）。

接入一个新角色 = profiles/registry.yaml 一条 + profiles/<name>.md 一份，零代码。
角色正文渲染进会话工作区 CLAUDE.md（rotate 后仍生效），不用
--append-system-prompt 双通道。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .config import PATHS
from .util import get_logger

log = get_logger(__name__)


@dataclass
class Profile:
    name: str
    description: str = ""
    profile_md: str = ""
    match_keywords: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    effort: str = "high"
    model: str | None = None
    timeout_s: int = 3600
    # 双信号判死阈值：stdout 事件行 + transcript mtime 双静默超过该值才杀。
    stall_timeout_s: int = 30 * 60
    # 工具调用轮次上限（None=不限制）→ claude --max-turns，收敛闸。
    max_turns: int | None = None
    # 上下文膨胀轮换阈值（input tokens；None=禁用轮换）。
    rotate_input_tokens: int | None = 400_000
    # 内建工具黑名单（WebSearch/WebFetch/Task 等）→ 会话 .claude/settings.json
    # permissions.disallow 注入；管理面 Tools tab 可视化编辑。
    disallowed_tools: list[str] = field(default_factory=list)
    # 执行引擎（engines/ 注册名：claude|hahaness|opencode）；None=继承
    # config.yaml engines.default（灰度默认翻一处即全量切换）。
    engine: str | None = None

    def render_context(self) -> str:
        return self.profile_md


_REGISTRY: dict[str, Profile] | None = None


def load_registry() -> dict[str, Profile]:
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY
    reg: dict[str, Profile] = {}
    path = PATHS["profiles"] / "registry.yaml"
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    for name, spec in (data.get("profiles") or {}).items():
        if not isinstance(spec, dict):
            continue
        md_path = PATHS["profiles"] / spec.get("profile", f"{name}.md")
        try:
            md = md_path.read_text()
        except OSError:
            md = ""
            log.warning("profile %s 的正文缺失：%s", name, md_path)
        rotate = spec.get("rotate") or {}
        reg[name] = Profile(
            name=name,
            description=spec.get("description", ""),
            profile_md=md,
            match_keywords=[str(k) for k in spec.get("match_keywords") or []],
            skills=[str(s) for s in spec.get("skills") or []],
            effort=spec.get("effort", "high"),
            model=spec.get("model"),
            timeout_s=int(spec.get("timeout_s", 3600)),
            stall_timeout_s=int(spec.get("stall_timeout_s", 1800)),
            max_turns=(int(spec["max_turns"]) if spec.get("max_turns") else None),
            rotate_input_tokens=(int(rotate["input_tokens"])
                                 if isinstance(rotate, dict) and rotate.get("input_tokens")
                                 else 400_000),
            disallowed_tools=[str(t) for t in spec.get("disallowed_tools") or []],
            engine=(str(spec["engine"]) if spec.get("engine") else None),
        )
    if not reg:
        reg["assistant"] = Profile(name="assistant", description="通用助手（内置兜底）")
    _REGISTRY = reg
    return reg


def reset_cache() -> None:
    """registry.yaml 被管理面改写后调用，下轮 load_registry 重读。"""
    global _REGISTRY
    _REGISTRY = None


def get(name: str) -> Profile:
    reg = load_registry()
    if name in reg:
        return reg[name]
    log.warning("未知 profile %s，回落 assistant", name)
    return reg.get("assistant", Profile(name="assistant"))


def auto_match(text: str) -> Profile:
    """关键词命中数选角色，全零落 assistant（承袭 papergo auto_match 语义）。"""
    reg = load_registry()
    best, best_hits = None, 0
    for p in reg.values():
        hits = sum(1 for kw in p.match_keywords if kw and kw.lower() in text.lower())
        if hits > best_hits:
            best, best_hits = p, hits
    return best if best else reg.get("assistant", Profile(name="assistant"))
