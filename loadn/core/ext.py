"""loadn.ext 扩展协议（P3-5a，pi ExtensionRunner 同构——loadn 取其神）。

代码级扩展收敛为**一个入口**：扩展 = 一个 Python 模块，暴露
`load(ext)` 函数；`ext` 面三个方法（closed surface，全部有类型契约）：

- ``ext.on(event, handler)``：事件订阅。event 限于 :data:`EVENTS`
  （与 hooks/P2-3 事件清单同面——总线化后外部命令钩子与进程内
  handler 同语义：handler 返回 dict 可 block / 改写输入输出）：
  ``{"decision": "block", "reason": "…"}`` 阻断；
  ``{"input": {...}}`` / ``{"output": "…"}`` 覆盖（PreToolUse/PostToolUse）
- ``ext.register_tool(tool)``：注册工具；**与内置工具同名 = 整体替换**
  （pi 语义——replace 内置行为不用改编擎代码；替换有 log 留痕）
- ``ext.register_provider(name, factory)``：注册 provider 工厂
  （``factory(cfg) -> Provider``；env ``LOADN_PROVIDER=<name>`` 生效）

放置位置（信任面同 hooks——任意代码执行面）：
- 全局：``$LOADN_HOME/extensions/*.py``
- 项目级：``.loadn/extensions/*.py``（P0-2 信任门——未过门整目录跳过）
- overlay：env ``LOADN_EXT_EXTRA``（os.pathsep 分隔——私有扩展与
  开源部署分离，同 LOADN_SKILLS_EXTRA 哲学）

单个扩展加载失败只告警不炸会话（同 MCP discover 纪律）。
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from loadn import loadn_home
from loadn.util import get_logger

log = get_logger(__name__)

# closed 事件集（hooks 面 + TurnEnd——扩展可观察 turn 收尾）
EVENTS = ("PreToolUse", "PostToolUse", "Stop", "SessionStart",
          "SessionEnd", "TurnEnd")


@dataclass
class ExtResult:
    """一次 load_extensions 的产物（build_agent 消费）。"""
    tools: dict = field(default_factory=dict)          # name → Tool 实例
    handlers: dict[str, list[Callable]] = field(
        default_factory=dict)                          # event → [handler]
    providers: dict[str, Callable] = field(
        default_factory=dict)                          # name → factory
    loaded: list[str] = field(default_factory=list)    # 成功加载的模块名


class ExtensionAPI:
    """传给扩展 load(ext) 的注册面（三个方法，见模块 docstring）。"""

    def __init__(self, name: str, result: ExtResult) -> None:
        self._name = name
        self._result = result

    def on(self, event: str, handler: Callable) -> None:
        if event not in EVENTS:
            raise ValueError(
                f"未知事件 {event!r}（loadn.ext 事件集：{', '.join(EVENTS)}）")
        if not callable(handler):
            raise TypeError("on(event, handler) 需要 callable")
        self._result.handlers.setdefault(event, []).append(handler)

    def register_tool(self, tool) -> None:
        name = getattr(tool, "name", "")
        if not name or not isinstance(name, str):
            raise ValueError("register_tool 需要 name 属性的工具对象")
        if not inspect.iscoroutinefunction(getattr(tool, "execute", None)):
            # 允许返回 awaitable 的普通函数（duck 形态）；只挡完全没 execute 的
            if not callable(getattr(tool, "execute", None)):
                raise ValueError(f"工具 {name} 缺少 execute 方法")
        self._result.tools[name] = tool

    def register_provider(self, name: str, factory: Callable) -> None:
        if not name or not isinstance(name, str):
            raise ValueError("register_provider 需要非空 name")
        if not callable(factory):
            raise TypeError("register_provider(name, factory) 需要 callable")
        self._result.providers[name] = factory


def ext_dirs(cwd: Path) -> list[Path]:
    """扩展目录发现：全局 < 项目级（信任门内）< overlay env。

    顺序即加载顺序（后加载同名工具覆盖先加载——项目级可覆写全局）。
    """
    import os

    dirs = [loadn_home() / "extensions"]
    from loadn.core import trust
    ok, _why = trust.gate(cwd)
    if ok:
        dirs.append(cwd / ".loadn" / "extensions")
    elif (cwd / ".loadn" / "extensions").exists():
        log.warning("信任门未过：项目级扩展 .loadn/extensions/ 整目录跳过")
    extra = os.environ.get("LOADN_EXT_EXTRA") or ""
    dirs += [Path(p) for p in extra.split(os.pathsep) if p.strip()]
    return dirs


def _load_module(path: Path):
    """按路径加载单个扩展模块（不污染 sys.modules 命名空间——前缀隔离）。"""
    modname = f"_loadn_ext_{path.stem}"
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(modname, None)
    return mod


def load_extensions(cwd: Path) -> ExtResult:
    """扫描全部扩展目录并执行 load(ext)。失败降级告警（同 MCP discover）。"""
    result = ExtResult()
    for d in ext_dirs(cwd):
        try:
            paths = sorted(d.glob("*.py"))
        except OSError:
            continue
        for path in paths:
            try:
                mod = _load_module(path)
                entry = getattr(mod, "load", None)
                if not callable(entry):
                    log.warning("扩展 %s 缺少 load(ext) 入口（跳过）", path.name)
                    continue
                entry(ExtensionAPI(path.stem, result))
                result.loaded.append(path.stem)
            except Exception as e:  # noqa: BLE001 — 单个失败不炸会话
                log.warning("扩展 %s 加载失败（跳过）：%r", path.name, e)
    return result


def apply_handlers(runner, handlers: dict[str, list[Callable]]) -> None:
    """把进程内 handler 挂进 HookRunner（总线化——外部命令钩子同语义）。"""
    runner.handlers.update(handlers)


async def dispatch(handlers: list[Callable], payload: dict):
    """跑一组进程内 handler，取第一个 dict 返回（block/override 语义）。

    返回 None=无决策。handler 抛异常只告警（不炸钩子链）。
    """
    for h in handlers or []:
        try:
            res = h(payload)
            if inspect.isawaitable(res):
                res = await res
        except Exception as e:  # noqa: BLE001
            log.warning("扩展 handler 异常（忽略）：%r", e)
            continue
        if isinstance(res, dict):
            return res
    return None
