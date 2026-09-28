"""引擎注册表：claude | loadn（默认候选）| opencode；hahaness=旧名 alias。

选择序：profile.engine > CONFIG.engines.default；未知名 log 警告回落 claude。
"""
from __future__ import annotations

from ..config import CONFIG
from ..util import get_logger
from .base import EngineSpec
from .base import EventAdapter as EventAdapter
from .claude import ClaudeSpec
from .claude import resolve_claude_bin as resolve_claude_bin
from .loadn import LoadnSpec
from .opencode import OpencodeSpec

log = get_logger(__name__)

ENGINES: dict[str, EngineSpec] = {
    "claude": ClaudeSpec(),
    "loadn": LoadnSpec(),
    "hahaness": LoadnSpec(),   # 旧名 alias（一版）：同一 spec，配置键 fallback
    # opencode 能力位速览：supports_transcript=False（判死只剩 stdout 一路）｜
    # max_turns_flag=False（无 --max-turns，靠 timeout_s 收敛）｜
    # session_id_domain="any"（ses_… 非 UUID，轮换=空串 fresh）｜
    # 有状态适配器（NDJSON→stream-json，result 恒在 finalize 合成）
    "opencode": OpencodeSpec(),
}


# 旧名 alias 集合：resolve() 可用（旧会话 DB 里 engine=hahaness），但 API
# 不吐给前端（用户不需要看到两个同款引擎）
ALIASES = {"hahaness"}

ENTRYPOINT_GROUP = "loadn.webui.engines"


def load_entrypoint_engines(registry: dict | None = None) -> list[str]:
    """第三方引擎挂载：entry point 组 ``loadn.webui.engines``。

    外部包在自身 pyproject 声明::

        [project.entry-points."loadn.webui.engines"]
        myagent = "my_pkg.engine:MySpec"

    值须是 EngineSpec 实例（或返回它的零参 callable）。加载失败只告警
    不炸平台（第三方代码不该能挡住启动）。返回成功挂载的名字。
    """
    import warnings
    from importlib.metadata import entry_points
    target = registry if registry is not None else ENGINES
    loaded: list[str] = []
    try:
        eps = entry_points(group=ENTRYPOINT_GROUP)
    except Exception:                      # noqa: BLE001 — 老 Python 兼容
        return loaded
    for ep in eps:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                obj = ep.load()
            spec = obj() if callable(obj) and not isinstance(obj, EngineSpec) else obj
            if isinstance(spec, EngineSpec):
                target[ep.name] = spec
                loaded.append(ep.name)
                log.info("第三方引擎挂载：%s (%s)", ep.name, ep.value)
            else:
                log.warning("entry point %s 不是 EngineSpec，跳过", ep.name)
        except Exception:                  # noqa: BLE001
            log.warning("第三方引擎 %s 加载失败（忽略）", ep.name, exc_info=True)
    return loaded


load_entrypoint_engines()   # import 即挂载（含测试收集路径）


def default_engine() -> str:
    return CONFIG.engines.default if CONFIG.engines.default in ENGINES else "claude"


def resolve(engine: str | None) -> EngineSpec:
    name = engine or default_engine()
    spec = ENGINES.get(name)
    if spec is None:
        log.warning("未知引擎 %s，回落 claude", name)
        spec = ENGINES["claude"]
    return spec
