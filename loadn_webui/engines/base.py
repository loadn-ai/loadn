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

import json
import os
import subprocess
import uuid
from pathlib import Path
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

    def session_tail(self, session_id: str, *, max_chars: int = 1800) -> str:
        """旧会话 transcript 的尾部交接摘要（用户最后指令/todos/最后输出）；
        '' = 无 transcript 或引擎不支持——调用方回落纯磁盘台账指引。"""
        return ""

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


# ------------------------------------------------------------ transcript 尾部摘要
# 双信封兼容：claude CLI transcript（message.content）与 loadn transcript
# （payload.content——payload 即 Message dict）。尾读上限：长会话不全量解析。
_TAIL_READ_BYTES = 512 * 1024


def summarize_transcript_tail(path: Path, *, max_chars: int = 1800) -> str:
    """transcript JSONL 尾部 → 交接摘要（轮换 anchor 注入用）。

    提取：最后一条用户文本（意图）、todos 终态（TodoWrite 快照 +
    TaskCreate/TaskUpdate 状态机）、最后一段 assistant 文本（结论/进展）。
    文件不存在/坏行/空尾 → ''（调用方回落纯磁盘台账指引）。
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _TAIL_READ_BYTES))
            tail_txt = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    if size > _TAIL_READ_BYTES:
        tail_txt = tail_txt.split("\n", 1)[-1]        # 丢掉被截断的首行

    last_user = ""
    last_asst = ""
    todos: list[dict] = []
    for ln in tail_txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue                                 # 被杀时的半行
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        msg = ev.get("message") if isinstance(ev.get("message"), dict) \
            else (ev.get("payload") if isinstance(ev.get("payload"), dict) else {})
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if t == "user":
            # tool_result 的 content 是块列表（无 text 块）→ 天然跳过
            txt = "\n".join(b.get("text") or "" for b in content
                            if isinstance(b, dict) and b.get("type") == "text").strip()
            if txt:
                last_user = txt
        elif t == "assistant":
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    last_asst = b["text"]
                elif b.get("type") == "tool_use":
                    name, inp = b.get("name"), b.get("input") or {}
                    if name == "TodoWrite" and isinstance(inp.get("todos"), list):
                        todos = [{"subject": x.get("content") or "任务",
                                  "status": x.get("status", "pending")}
                                 for x in inp["todos"] if isinstance(x, dict)]
                    elif name == "TaskCreate":
                        todos.append({"subject": inp.get("subject") or "任务",
                                      "status": "pending"})
                    elif name == "TaskUpdate":
                        try:
                            tno = int(inp.get("taskId"))
                            if 1 <= tno <= len(todos) and inp.get("status"):
                                todos[tno - 1]["status"] = inp["status"]
                        except (TypeError, ValueError):
                            pass

    parts: list[str] = []
    if last_user:
        parts.append(f"【用户最后指令】{last_user[:300]}")
    if todos:
        marks = {"completed": "✓", "in_progress": "▶"}
        lines = [f"- [{marks.get(td.get('status'), ' ')}] {td.get('subject', '')}"
                 for td in todos[:12]]
        parts.append("【任务清单】\n" + "\n".join(lines))
    if last_asst:
        parts.append(f"【最后输出】{last_asst[:1200]}")
    return "\n\n".join(parts)[:max_chars]
