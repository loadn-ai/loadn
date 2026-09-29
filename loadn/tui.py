"""全屏 TUI（textual，Claude Code 风格）：`loadn[tui]` 可选 extra。

布局：滚动流水（assistant 文本 / 工具行 / 结果摘录 / todos）+ 状态条
（模型·会话·累计 tokens）+ 单行输入框 + 快捷键 Footer。通道与 REPL 同源：
run_turn(emit=, stop=) 逐事件回调 + StopHandle 中断——引擎零改动。

依赖红线：textual 为可选依赖（pyproject extras `tui`），未安装时 CLI
回落到 REPL（main.py ImportError 兜底），引擎核心仍 httpx 单依赖。
"""
from __future__ import annotations

import asyncio

from loadn.types import TextBlock, ToolUseBlock

# ---- 纯渲染助手（无 textual 依赖，单测直接打这里） ------------------------


def tool_line(name: str, input_: dict) -> str:
    """工具调用行：⏺ Name 首个字符串参数（截 100）。Claude Code 的视觉词法。"""
    probe = next((str(v) for v in input_.values()
                  if isinstance(v, (str, int, float)) and v), "")
    return f"⏺ {name}({probe[:100]})" if probe else f"⏺ {name}()"


def result_excerpt(text: str, is_error: bool, limit: int = 300) -> str:
    """工具结果摘录：错误带 ✗；多行压空格、截断带省略号。"""
    flat = " ".join(str(text).split())
    body = flat if len(flat) <= limit else flat[:limit] + " …"
    return (f"  ⠿ {body}" if not is_error else f"  ✗ {body}") if body else "  ⠿（空）"


def usage_line(usage: dict | None, total_tokens: int) -> str:
    """状态条用量段：本轮 in/out/cache + 累计。usage 缺席时只报累计。"""
    if not usage:
        return f"累计 {total_tokens} tok"
    cache = usage.get("cache_read_input_tokens") or 0
    return (f"in {usage.get('input_tokens', 0)} · out {usage.get('output_tokens', 0)}"
            f" · cache {cache} ｜ 累计 {total_tokens} tok")


# ---- TUI 本体 --------------------------------------------------------------


def run_tui(bundle, stop) -> int:
    """入口（main.py 调）：委托 textual App.run()。"""
    return LoadnTUI(bundle, stop).run()


try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import Footer, Input, RichLog, Static
except ImportError:  # pragma: no cover —— extras 缺席由 main.py 兜底，不可达
    raise


class LoadnTUI(App):  # type: ignore[misc]
    TITLE = "loadn"
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "中断当前轮"),
        Binding("ctrl+l", "clear_log", "清屏"),
        Binding("ctrl+d", "quit", "退出"),
    ]

    def __init__(self, bundle, stop) -> None:
        super().__init__()
        self.bundle = bundle
        self.core = bundle.core
        self.stop = stop
        self._turn: asyncio.Task | None = None
        self._total_tokens = 0

    # ---- 布局

    def compose(self) -> ComposeResult:
        yield RichLog(markup=True, wrap=True, highlight=False, id="log")
        yield Static("", id="status")
        yield Input(placeholder="发消息（/help 指令，Ctrl-C 中断，Ctrl-D 退出）",
                    id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        model = getattr(self.core.settings, "model", "") or ""
        log.write(f"[dim]会话 {self.core.session.session_id} · {model} · "
                  f"{self.core.cwd}[/dim]")
        log.write("[dim]/help 看指令；Ctrl-C 中断当前轮（再按一次如无轮在跑则不退出）[/dim]")
        self._refresh_status()

    # ---- 事件路由

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        self.query_one("#prompt", Input).value = ""
        if not line:
            return
        if line.startswith("/"):
            await self._slash(line)
            return
        log = self.query_one("#log", RichLog)
        log.write(f"[bold cyan]❯ {line}[/bold cyan]")
        await self._run_turn(line)

    async def _run_turn(self, prompt: str) -> None:
        if self._turn and not self._turn.done():
            self.query_one("#log", RichLog).write(
                "[yellow]（上一轮还在跑——Ctrl-C 中断后再发）[/yellow]")
            return
        self.stop.requested = False

        async def _show(ev: dict) -> None:
            self._render_event(ev)

        self._turn = asyncio.create_task(self.core.run_turn(prompt, emit=_show,
                                                             stop=self.stop))
        try:
            summary = await self._turn
        except Exception as e:  # noqa: BLE001 —— 轮异常不杀 TUI（REPL 同语义）
            self.query_one("#log", RichLog).write(f"[red]（轮异常：{e!r}）[/red]")
            return
        usage = getattr(summary, "usage", None) or {}
        self._total_tokens += int(usage.get("input_tokens", 0)
                                  + usage.get("output_tokens", 0)
                                  + int(usage.get("cache_read_input_tokens") or 0))
        log = self.query_one("#log", RichLog)
        if summary.subtype != "success":
            log.write(f"[red]（{summary.subtype}"
                      f"{('｜' + summary.error) if summary.error else ''}）[/red]")
        else:
            log.write(f"[dim]—— 轮完成 · {usage_line(usage, self._total_tokens)} ——[/dim]")
        self._render_todos()
        self._refresh_status()

    def _render_event(self, ev: dict) -> None:
        """emit 回调 → 流水渲染。与 REPL _show 同事件面，视觉升级。"""
        log = self.query_one("#log", RichLog)
        et = ev.get("type")
        if et == "assistant":
            for b in ev["message"].content:
                if isinstance(b, TextBlock) and b.text.strip():
                    log.write(b.text)
                elif isinstance(b, ToolUseBlock):
                    log.write(f"[magenta]{tool_line(b.name, dict(b.input))}[/magenta]")
        elif et == "tool_result":
            blk = ev["block"]
            text = blk.content if isinstance(blk.content, str) else "(blocks)"
            line = result_excerpt(text, bool(blk.is_error))
            color = "red" if blk.is_error else "dim"
            log.write(f"[{color}]{line}[/{color}]")

    def _render_todos(self) -> None:
        todos = getattr(self.core.session.state, "todos", None) or []
        if not todos:
            return
        log = self.query_one("#log", RichLog)
        log.write("[dim]  todos:[/dim]")
        for t in todos:
            mark = {"completed": "[green]✔[/green]",
                    "in_progress": "[yellow]◐[/yellow]"}.get(t.status, "○")
            log.write(f"  {mark} {t.subject}")

    def _refresh_status(self) -> None:
        st = self.query_one("#status", Static)
        model = getattr(self.core.settings, "model", "") or "default"
        st.update(f"[dim]{model} · {self.core.session.session_id[:8]} · "
                  f"{usage_line(None, self._total_tokens)}[/dim]")

    # ---- 指令与快捷键

    async def _slash(self, line: str) -> None:
        log = self.query_one("#log", RichLog)
        cmd = line.split()[0]
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/help":
            log.write("[dim]/exit /resume <sid> /fork /compact /todos /undo /clear[/dim]")
        elif cmd == "/clear":
            log.clear()
        elif cmd == "/todos":
            self._render_todos()
        elif cmd == "/undo":
            from loadn.core import autocommit as ac
            log.write(f"[dim]{ac.undo(self.core.cwd, self.core.session.session_id)}[/dim]")
        elif cmd.startswith("/resume ") and len(line.split()) > 1:
            from loadn.core.session import SessionManager
            sid = line.split()[1]
            self.bundle.session = SessionManager.resume(sid, self.core.cwd)
            self.core.session = self.bundle.session
            log.write(f"[dim]已切到会话 {sid}[/dim]")
            self._refresh_status()
        elif cmd == "/fork":
            from loadn.core.session import SessionManager
            new = SessionManager.fork(self.bundle.session.session_id, self.core.cwd)
            self.bundle.session = new
            self.core.session = new
            log.write(f"[dim]已分叉为新会话 {new.session_id}[/dim]")
        elif cmd == "/compact":
            if self.core.compactor is None:
                log.write("[yellow]（compactor 未配置——/compact 不可用）[/yellow]")
                return
            msgs = self.core.session.messages_for_turn()
            msgs, did = await self.core.compactor.compact(
                msgs, context_window=self.core.settings.context_window,
                prev_summary=self.core.session.last_compact_summary())
            if did:
                self.core.session.mark_compact(
                    self.core.compactor.last_summary,
                    tokens_cropped=getattr(
                        self.core.compactor, "last_dropped_tokens", None))
                log.write("[dim]已压缩[/dim]")
        else:
            log.write(f"[yellow]未知指令 {cmd}（/help 查看）[/yellow]")

    def action_interrupt(self) -> None:
        if self._turn and not self._turn.done():
            self.stop.requested = True
            self.query_one("#log", RichLog).write(
                "[yellow]⏸ 已请求中断（当前工具步完成后停）[/yellow]")
        else:
            self.query_one("#log", RichLog).write("[dim]（无轮在跑）[/dim]")

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()


def tui_available() -> bool:
    """textual 是否可用（main.py 据此选 TUI/REPL）。"""
    try:
        import textual  # noqa: F401
        return True
    except ImportError:
        return False
