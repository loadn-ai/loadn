"""可插拔无头引擎层基座：EngineSpec（argv 组装 + 能力位）+ EventAdapter。

范式承袭 papergo/providers.py 的 ProviderSpec / build_call / supports_transcript
三件套，推广为三个正交面：

  · build_argv(call) -> (cmd, env_extra)   引擎方言的 argv 与注入环境
  · adapter()                              事件归一化（恒等 / 有状态转换）
  · 能力位                                  transcript 判死 / session id 域 / flag 面

事件契约锚点（全引擎统一到 claude CLI stream-json 形状，engine._consume 零改动）：
webui 只消费三类事件——assistant（message.content blocks）、user（tool_result
blocks）、result（usage/modelUsage/num_turns 记账，硬契约）。claude 与 loadn
原生输出该格式（恒等适配器）；opencode 由有状态适配器把 NDJSON 归一化进来。
"""
from __future__ import annotations

import os
import subprocess
import uuid
from typing import TYPE_CHECKING

from ..util import get_logger

if TYPE_CHECKING:
    from ..claude_runner import TurnCall

log = get_logger(__name__)


class EventAdapter:
    """事件归一化器：feed 喂一行原始事件 → 0..n 个 stream-json 事件。

    流结束后 finalize 合成收尾事件（result 等）。恒等引擎（claude/loadn）
    用基类行为即可；有状态引擎（opencode）子类化并维护累积状态。
    """

    def feed(self, ev: dict) -> list[dict]:
        return [ev]

    def finalize(self, rc: int | None, duration_s: float) -> list[dict]:
        return []


class EngineSpec:
    """一个无头引擎的完整方言描述。子类覆写各面；基类给 claude 同构默认。"""

    name: str = "claude"
    # 判死 transcript 兜底：False = 只有 stdout 静默 + 硬超时两路信号
    supports_transcript: bool = True
    # session id 域：uuid（claude/loadn）| any（opencode ses_… 等）
    session_id_domain: str = "uuid"
    # flag 能力（管理面提示用；False 的在 build_argv 里降级省略）
    max_turns_flag: bool = True
    effort_flag: bool = True

    # ------------------------------------------------------------ argv / env
    def resolve_bin(self) -> str | list[str]:
        """二进制路径（str）或 argv 前缀（list，如 [python, -m, loadn]）。"""
        raise NotImplementedError

    def build_env(self) -> dict:
        """spawn 时注入的环境变量（与 argv 一起返回给 runner 合并）。"""
        return {}

    def build_argv(self, call: TurnCall) -> tuple[list[str], dict]:
        """组完整 argv。约定：PROMPT 恒为末位（fake 契约 argv[-1]）。"""
        raise NotImplementedError

    def adapter(self) -> EventAdapter:
        return EventAdapter()

    # ------------------------------------------------------------ 能力位
    def transcript_age(self, session_id: str) -> float | None:
        """引擎自有 transcript 的年龄（秒）；None = 无此信号（只剩 stdout 一路）。"""
        return None

    def new_session_id(self) -> str:
        """轮换用新会话 id；'' = 下次 fresh 不传 id（由引擎自建）。"""
        return str(uuid.uuid4())

    def is_in_use_error(self, err: str | None) -> bool:
        """fresh 秒拒特例：transcript 已存在 → 转 resume 重试（claude 文案）。"""
        return bool(err and "already in use" in err)

    def health(self) -> dict:
        """`<bin> --version` 探测（15s 超时；失败不抛，ok=False）。"""
        binp = self.resolve_bin()
        cmd = binp if isinstance(binp, list) else [binp]
        label = " ".join(cmd)
        try:
            r = subprocess.run([*cmd, "--version"], capture_output=True, text=True,
                               timeout=15, env={**os.environ, **self.build_env()})
            version = (r.stdout or r.stderr).strip()
            return {"bin": label, "version": version, "ok": r.returncode == 0 and bool(version)}
        except Exception as e:  # noqa: BLE001 — 健康检查永不抛
            return {"bin": label, "version": "unavailable", "ok": False, "error": str(e)[:120]}
