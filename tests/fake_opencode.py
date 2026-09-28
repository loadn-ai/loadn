#!/usr/bin/env python3
"""假 opencode CLI（零 token 测试）：遵守真 `opencode run --format json` 接口契约。

与 fake_claude.py 同协议：cwd（workspace）下 .fake/ 控制文件驱动行为，
LOADN_FAKE_LOG 记每次 argv（--session 扫描 / argv[-1]=PROMPT）。差别只在
输出形态——这里逐行吐 opencode NDJSON 事件（session.* / message.part.*），
完成信号是 session.status(idle)，由 OpencodeEventAdapter 归一化消费。

  argv 契约：<bin> run --format json --auto [--model p/m] [--variant effort]
            [--session ses_…] PROMPT
  控制文件（语义同 fake_claude.py）：
      reply       自定义回复文本
      tools       工具调用场景：reasoning + Bash + Write（tool part pending→completed）
      todos       TodoWrite tool part（input.todos 数组）
      artifacts   额外写 artifacts/report.md + citations-audit.json（产物扫描）
      hang        吐完 text part 后睡 120s（测 stop；无 idle）
      fail        session.created 后 session.error + exit 1
      fastfail    stderr 秒退 exit 1，无任何事件（无 result 可合成）
      bigusage    step-finish tokens.input=600000（测轮换启发式）
      giantline   200k 字符的 text part（测 reader 单行上限）
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ts() -> int:
    return int(time.time() * 1000)


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:                       # health() 探测面
        sys.stdout.write("opencode version 1.18.0 (fake)\n")
        return 0

    log = os.environ.get("LOADN_FAKE_LOG")
    if log:
        session_flag = None
        for i, a in enumerate(argv):
            if a == "--session" and i + 1 < len(argv):
                session_flag = {"flag": a, "value": argv[i + 1]}
                break
        prompt = argv[-1] if argv else ""
        with open(log, "a") as f:
            f.write(json.dumps({"argv": argv, "session": session_flag,
                                "prompt": prompt[:200], "cwd": os.getcwd()},
                               ensure_ascii=False) + "\n")

    prompt = argv[-1] if argv else ""
    session_id = ""
    for i, a in enumerate(argv):
        if a == "--session" and i + 1 < len(argv):
            session_id = argv[i + 1]              # resume：沿用 engine 侧登记的 id
            break
    if not session_id:
        session_id = f"ses_{uuid.uuid4().hex[:24]}"   # fresh：引擎自建 ses_… id

    ctrl = Path.cwd() / ".fake"

    def has(name: str) -> bool:
        return (ctrl / name).exists()

    def created() -> dict:
        return {"type": "session.created", "timestamp": _ts(), "sessionID": session_id,
                "info": {"id": session_id, "title": "fake", "directory": os.getcwd(),
                         "time": {"created": _ts(), "updated": _ts()}}}

    msg = f"msg_{uuid.uuid4().hex[:12]}"

    def part(pid: str, ptype: str, **payload) -> dict:
        return {"type": "message.part.updated", "timestamp": _ts(),
                "sessionID": session_id,
                "part": {"id": pid, "sessionID": session_id, "messageID": msg,
                         "type": ptype, **payload}}

    def tool_part(pid: str, name: str, state: str, **payload) -> dict:
        return part(pid, "tool", tool=name, state=state, **payload)

    if has("fastfail"):
        sys.stderr.write("fake internal failure\n")
        return 1
    if has("fail"):
        _emit(created())
        _emit({"type": "session.error", "timestamp": _ts(), "sessionID": session_id,
               "error": "fake session failure"})
        return 1
    if has("hang"):
        _emit(created())
        _emit(part("prt_hang", "text", text="开始长任务…"))
        time.sleep(float(os.environ.get("LOADN_FAKE_HANG_S") or 120))  # 无 idle：等外部 stop
        return 0

    # ---- 正常场景
    _emit(created())
    if has("pause"):
        # 收养测试道具：created 后睡一段（daemon 在此窗口死），醒后写完全流程
        time.sleep(float(os.environ.get("LOADN_FAKE_PAUSE_S") or 3))

    if has("todos"):
        todos = [{"content": "检索来源", "status": "pending"},
                 {"content": "核验引用", "status": "pending"}]
        _emit(tool_part("prt_todo", "TodoWrite", "pending", input={"todos": todos}))
        _emit(tool_part("prt_todo", "TodoWrite", "completed",
                        input={"todos": todos}, output="todos written"))

    if has("tools") or has("artifacts"):
        _emit(part("prt_rs1", "reasoning", text="需要先验证环境，再写产物文件。"))
        _emit(part("prt_tx1", "text", text="我先跑一条命令"))
        _emit(part("prt_tx1", "text", text="我先跑一条命令验证环境。"))   # 全量快照模拟增量
        bash_input = {"command": "echo hello-fake", "description": "测试命令"}
        _emit(tool_part("prt_bash", "Bash", "pending", input=bash_input))
        _emit(tool_part("prt_bash", "Bash", "running", input=bash_input))
        _emit(tool_part("prt_bash", "Bash", "completed", input=bash_input,
                        output="hello-fake"))
        art_dir = Path.cwd() / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "report.md").write_text(
            "# 测试报告\n\n这是一份 fake opencode 产物。\n\n- 关键结论 [1]\n\n"
            "## 参考\n\n- [1] https://example.com\n")
        (art_dir / "citations-audit.json").write_text(json.dumps([
            {"key": "1", "title": "Example", "url": "https://example.com",
             "status": "VERIFIED", "evidence": ["fetch ok"],
             "checked_at": "2026-09-15"}], ensure_ascii=False, indent=2))
        write_input = {"file_path": "artifacts/report.md", "content": "# 测试报告"}
        _emit(tool_part("prt_write", "Write", "pending", input=write_input))
        _emit(tool_part("prt_write", "Write", "completed", input=write_input,
                        output="File written"))

    if has("giantline"):
        big = "x" * 200_000                       # 单行 >64KB：测 reader 上限加固
        _emit(part("prt_big", "text", text=big[:100_000]))
        _emit(part("prt_big", "text", text=big))

    reply_file = ctrl / "reply"
    text = reply_file.read_text() if reply_file.exists() else \
        f"收到：{prompt[:60]}（fake opencode）"
    _emit(part("prt_final", "text", text=text[:max(1, len(text) // 2)]))  # 快照×2 模拟增量
    _emit(part("prt_final", "text", text=text))

    tokens = {"input": 1000, "output": 200, "reasoning": 0,
              "cache": {"read": 10000, "write": 0}}
    if has("bigusage"):
        tokens = {"input": 600000, "output": 2000, "reasoning": 0,
                  "cache": {"read": 500000, "write": 0}}
    _emit(part("prt_step", "step-finish", cost=0.05, tokens=tokens))
    # 模型面：session.updated 的 info 带 modelID/tokens/cost（适配器 model_id 源）
    _emit({"type": "session.updated", "timestamp": _ts(), "sessionID": session_id,
           "info": {"id": session_id, "title": "fake", "directory": os.getcwd(),
                    "modelID": "fake", "tokens": tokens, "cost": 0.05}})
    _emit({"type": "message.part.delta", "timestamp": _ts(), "sessionID": session_id,
           "messageID": msg, "partID": "prt_final", "delta": {"text": "（应被丢弃）"}})
    _emit({"type": "session.status", "timestamp": _ts(), "sessionID": session_id,
           "properties": {"status": {"type": "busy"}}})
    _emit({"type": "session.status", "timestamp": _ts(), "sessionID": session_id,
           "properties": {"status": {"type": "idle"}}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
