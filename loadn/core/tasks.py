"""Runtime Task 注册表（P2-4，ZCode Z4 registry/notification-policy 同构）。

三类长任务统一登记、统一观测/取消：
- **bg**：后台命令（ProcessSupervisor.spawn_bg 的进程组）
- **subagent**：并行子代理（SubagentManager 活跃扇出）
- **timer**：定时/延迟回调（asyncio task 句柄）

- 注册表挂在 ToolContext.extras["task_registry"]（同 shells/state 惯例）
  ——AgentCore 构建时创建，工具与宿主（daemon）都可查。
- `TaskList/TaskCancel` 引擎工具：枚举（id/类型/描述/存活）/取消（bg=
  进程组收割 kill_process_group；subagent=取消 asyncio task；timer=
  task.cancel）。权限面照常（Tool 基类走 permissions+hooks——不绕过）。
- 通知策略（平台侧语义，引擎侧只产事件）：三档 immediate/summary/silent
  + 免打扰窗——notifications.py 事件标记（on_confirm/on_done/on_error），
  webui 消费；文案层独立文件 i18n zh/en（OSS 国际化打底）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from loadn.util import get_logger

log = get_logger(__name__)


@dataclass
class RuntimeTask:
    id: str
    kind: str                       # bg | subagent | timer
    desc: str
    started_at: float = field(default_factory=time.time)
    proc: object | None = None          # asyncio.subprocess.Process（bg）
    cancel_handle: object | None = None  # asyncio.Task（subagent/timer）


class TaskRegistry:
    """登记表（事件循环内使用）。生命周期：进程死/task 完即注销（惰性）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, RuntimeTask] = {}
        self._seq = 0

    # ------------------------------------------------------------ 登记/注销
    def register(self, kind: str, desc: str, *, proc=None,
                 cancel_handle=None) -> str:
        self._seq += 1
        tid = {"bg": "bg", "subagent": "sa", "timer": "tm"}.get(kind, "tk") \
            + str(self._seq)
        self._tasks[tid] = RuntimeTask(tid, kind, desc, proc=proc,
                                       cancel_handle=cancel_handle)
        return tid

    def unregister(self, tid: str) -> None:
        self._tasks.pop(tid, None)

    # ------------------------------------------------------------ 枚举
    def list_alive(self) -> list[dict]:
        out = []
        for t in list(self._tasks.values()):
            alive = True
            if t.proc is not None:
                alive = t.proc.returncode is None
            elif t.cancel_handle is not None:
                alive = not t.cancel_handle.done()
            if not alive:
                self.unregister(t.id)             # 惰性收割
                continue
            out.append({"id": t.id, "kind": t.kind, "desc": t.desc,
                        "age_s": round(time.time() - t.started_at)})
        return out

    # ------------------------------------------------------------ 取消
    async def cancel(self, tid: str) -> dict:
        t = self._tasks.get(tid)
        if t is None:
            return {"ok": False, "error": f"任务 {tid} 不存在或已结束"}
        if t.kind == "bg" and t.proc is not None:
            from loadn.supervisor.process import kill_process_group
            await kill_process_group(t.proc)
            self.unregister(tid)
            return {"ok": True, "cancelled": tid, "way": "process-group"}
        if t.cancel_handle is not None and not t.cancel_handle.done():
            t.cancel_handle.cancel()
            self.unregister(tid)
            return {"ok": True, "cancelled": tid, "way": "task-cancel"}
        self.unregister(tid)
        return {"ok": True, "cancelled": tid, "way": "already-done"}


def registry_of(ctx) -> TaskRegistry | None:
    """ToolContext → 注册表（extras 惯例位）。"""
    reg = ctx.extras.get("task_registry")
    return reg if isinstance(reg, TaskRegistry) else None


# ---------------------------------------------------------------- 通知策略（P2-4 平台语义）
NOTIFY_DEFAULTS = {"on_confirm": "immediate", "on_done": "summary",
                   "on_error": "immediate"}
DND_WINDOW_S = 0                      # 免打扰窗（0=关；平台可配）


def notify_policy(settings: dict | None = None) -> dict:
    """三档策略（immediate/summary/silent）+ 免打扰窗——webui settings 覆盖。"""
    cfg = dict(NOTIFY_DEFAULTS)
    if isinstance(settings, dict):
        for k in cfg:
            if settings.get(k) in ("immediate", "summary", "silent"):
                cfg[k] = settings[k]
    return cfg


def should_notify(event: str, policy: dict, *, dnd_until: float = 0.0,
                  now: float | None = None) -> bool:
    """事件是否即时通知（平台侧消费的纯函数）。summary 档由平台汇总批
    发（此处 False=不即时）；dnd 窗内降级 silent（on_confirm 除外——
    需确认永不静默，人审阻塞）。"""
    import time as _t
    t = now if now is not None else _t.time()
    tier = policy.get(event, "immediate")
    if tier == "silent":
        return False
    if tier == "summary":
        return False                      # 平台汇总，不逐条即时
    if dnd_until and t < dnd_until and event != "on_confirm":
        return False                      # 免打扰窗内降级
    return True


def render_notice(event: str, **kw) -> str:
    """文案层（i18n 表按 env 选）。"""
    from .i18n import messages
    return messages()[event].format(**kw)
