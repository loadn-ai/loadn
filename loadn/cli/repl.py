"""交互 REPL（非 -p 模式）：逐轮 run_turn，text 呈现。

指令：/exit /quit 退出；/resume <sid> 换会话续跑；/fork 从当前会话分叉；
/compact 立即压缩；/todos 看任务清单；/help 帮助。
"""
from __future__ import annotations

from loadn.types import TextBlock, ToolUseBlock


async def run_repl(bundle, emitter, fmt: str, stop) -> int:
    core = bundle.core
    print(f"loadn 会话 {bundle.session.session_id}（/help 看指令，Ctrl-C 中断当前轮）")
    while True:
        try:
            line = input("❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/help":
            print("/exit /resume <sid> /fork /compact /todos /undo")
            continue
        if line == "/undo":
            from loadn.core import autocommit as ac
            print(ac.undo(core.cwd, core.session.session_id))
            continue
        if line == "/todos":
            for t in core.session.state.todos:
                print(f"  [{t.status:>10}] {t.subject}")
            continue
        if line.startswith("/resume "):
            from loadn.core.session import SessionManager
            sid = line.split()[1]
            bundle.session = SessionManager.resume(sid, core.cwd)
            core.session = bundle.session
            print(f"已切到会话 {sid}")
            continue
        if line == "/fork":
            from loadn.core.session import SessionManager
            new = SessionManager.fork(bundle.session.session_id, core.cwd)
            bundle.session = new
            core.session = new
            print(f"已分叉为新会话 {new.session_id}")
            continue
        if line == "/compact" and core.compactor is not None:
            msgs = core.session.messages_for_turn()
            msgs, did = await core.compactor.compact(
                msgs, context_window=core.settings.context_window,
                prev_summary=core.session.last_compact_summary())
            if did:
                core.session.mark_compact(core.compactor.last_summary)
                # 压缩后重置内存上下文：下一轮从 compact 点重放
                print("已压缩")
            continue

        # 普通轮：emit 以文本呈现
        async def _show(ev: dict) -> None:
            if ev.get("type") == "assistant":
                for b in ev["message"].content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        print(b.text)
                    elif isinstance(b, ToolUseBlock):
                        probe = next((str(v) for k, v in b.input.items()
                                      if isinstance(v, (str, int, float)) and v), "")
                        print(f"  ⚙ {b.name} {probe[:100]}")
            elif ev.get("type") == "tool_result":
                blk = ev["block"]
                mark = "✗" if blk.is_error else "·"
                text = blk.content if isinstance(blk.content, str) else "(blocks)"
                print(f"  {mark} {ev.get('name', '')} {text.strip()[:200]}")

        try:
            summary = await core.run_turn(line, emit=_show, stop=stop)
        except Exception as e:  # noqa: BLE001
            print(f"（轮异常：{e!r}）")
            continue
        if summary.subtype != "success":
            print(f"（{summary.subtype}{('｜' + summary.error) if summary.error else ''}）")
        stop.requested = False   # Ctrl-C 只打断当轮，REPL 继续


def trust_preflight(cwd) -> None:
    """P0-2 信任门（交互模式问一次；在 build_agent 之前调用——admit 后
    本会话即按已信任加载。headless -p 不经过这里：gate() fail-closed，
    无人可问=按未信任降级，与 ask→deny 哲学一致）。"""
    from loadn.core import trust
    ok, why = trust.gate(cwd)
    if ok:
        return
    root = trust.project_root(cwd)
    print(f"\n⚠ {why}")
    for f in (trust._resource_files(root)[:8] if root else []):
        print(f"    {f}")
    try:
        ans = input("信任该工作区的项目级 hooks/skills/权限规则？[y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans in ("y", "yes") and root is not None:
        trust.admit(root)
        print("已信任（记录当前内容摘要；资源变更后需重新确认）")
    elif root is not None:
        trust.revoke(root)
        print("已标记不信任：项目级资源降级跳过（全局 hooks/skills 不受影响）")
