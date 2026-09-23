#!/usr/bin/env python3
"""假 claude CLI（零 token 测试）：遵守与真 CLI 相同的接口契约，输出逐行 stream-json。

用法（conftest 通过 WORKDADDY_CLAUDE_BIN 指向本脚本）：
  - argv 契约：-p --verbose --output-format stream-json ... (--session-id|--resume UUID) PROMPT
  - 每次 argv 追加一行 JSON 到 $WORKDADDY_FAKE_LOG（测试断言 --resume 用）
  - 行为由 cwd（workspace）下的 .fake/ 控制文件驱动：
      reply       自定义回复文本
      tools       工具调用场景：Bash echo + Write artifacts/report.md
      todos       TaskCreate/TaskUpdate 事件
      artifacts   额外写 artifacts/report.md + citations-audit.json（产物扫描）
      hang        睡 120s（测 stop）
      fail        exit 1 + stderr（测 turn_error）
      fastfail    秒退 exit 1 无 result（测 resume 簿记/轮换）
      bigusage    result usage input_tokens=600000（测轮换启发式）
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


def _assistant_event(blocks: list) -> dict:
    return {"type": "assistant",
            "message": {"id": f"msg_{uuid.uuid4().hex[:8]}", "role": "assistant",
                        "content": blocks}}


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("WORKDADDY_FAKE_LOG")
    if log:
        session_flag = None
        for i, a in enumerate(argv):
            if a in ("--session-id", "--resume") and i + 1 < len(argv):
                session_flag = {"flag": a, "value": argv[i + 1]}
                break
        prompt = argv[-1] if argv else ""
        with open(log, "a") as f:
            f.write(json.dumps({"argv": argv, "session": session_flag,
                                "prompt": prompt[:4000], "cwd": os.getcwd()},
                               ensure_ascii=False) + "\n")

    prompt = argv[-1] if argv else ""
    session_id = ""
    for i, a in enumerate(argv):
        if a in ("--session-id", "--resume") and i + 1 < len(argv):
            try:
                import uuid as _u
                session_id = str(_u.UUID(argv[i + 1]))
            except ValueError:
                session_id = argv[i + 1]
            break
    if not session_id:
        session_id = str(uuid.uuid4())

    ctrl = Path.cwd() / ".fake"

    def has(name: str) -> bool:
        return (ctrl / name).exists()

    if has("fastfail"):
        sys.stderr.write("Error: Session not found\n")
        return 1
    if has("fail"):
        _emit({"type": "system", "subtype": "init", "session_id": session_id,
               "model": "fake", "tools": [], "mcp_servers": []})
        sys.stderr.write("fake internal failure\n")
        return 1
    if has("hang"):
        _emit({"type": "system", "subtype": "init", "session_id": session_id,
               "model": "fake", "tools": [], "mcp_servers": []})
        _emit(_assistant_event([{"type": "text", "text": "开始长任务…"}]))
        time.sleep(float(os.environ.get("WORKDADDY_FAKE_HANG_S") or 120))
        return 0

    # ---- 正常场景
    _emit({"type": "system", "subtype": "init", "session_id": session_id,
           "model": "fake", "tools": ["Bash", "WebSearch"], "mcp_servers": []})
    if has("pause"):
        # 收养测试道具：init 后睡一段（daemon 在此窗口死掉），醒来继续写完
        # assistant + result——模拟「daemon 重启而 CLI 照跑」
        time.sleep(float(os.environ.get("WORKDADDY_FAKE_PAUSE_S") or 3))

    if has("todos"):
        _emit(_assistant_event([{"type": "tool_use", "id": "tu_1", "name": "TaskCreate",
                                 "input": {"subject": "检索来源", "description": "扇出搜索"}}]))
        _emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "task created", "is_error": False}]}})
        _emit(_assistant_event([{"type": "tool_use", "id": "tu_2", "name": "TaskUpdate",
                                 "input": {"taskId": "1", "status": "in_progress"}}]))
        _emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_2", "content": "updated", "is_error": False}]}})

    if has("tools") or has("artifacts"):
        _emit(_assistant_event([{"type": "thinking", "thinking": "需要跑一条命令验证环境，然后再写产物文件。"}]))
        _emit(_assistant_event([{"type": "tool_use", "id": "tu_3", "name": "Bash",
                                 "input": {"command": "echo hello-fake", "description": "测试命令"}}]))
        _emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_3", "content": "hello-fake", "is_error": False}]}})
        art_dir = Path.cwd() / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "report.md").write_text(
            "# 测试报告\n\n这是一份 fake 产物。\n\n- 关键结论 [1]\n\n## 参考\n\n- [1] https://example.com\n")
        (art_dir / "citations-audit.json").write_text(json.dumps([
            {"key": "1", "title": "Example", "url": "https://example.com",
             "status": "VERIFIED", "evidence": ["fetch ok"], "checked_at": "2026-09-02"}],
            ensure_ascii=False, indent=2))
        _emit(_assistant_event([{"type": "tool_use", "id": "tu_4", "name": "Write",
                                 "input": {"file_path": "artifacts/report.md",
                                           "content": "# 测试报告"}}]))
        _emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_4", "content": "File written", "is_error": False}]}})

    if has("giantline"):
        # 单行 >64KB（Read 图片的 base64 tool_result 即如此）——测 reader 上限加固
        big = "x" * 200_000
        _emit(_assistant_event([{"type": "tool_use", "id": "tu_5", "name": "Read",
                                 "input": {"file_path": "work/screen.png"}}]))
        _emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_5",
             "content": [{"type": "image", "source": {"type": "base64", "data": big}}],
             "is_error": False}]}})
        _emit(_assistant_event([{"type": "text", "text": "图片已读完"}]))

    reply_file = ctrl / "reply"
    text = reply_file.read_text() if reply_file.exists() else \
        f"收到：{prompt[:60]}（fake claude）"
    _emit(_assistant_event([{"type": "text", "text": text}]))

    usage = {"input_tokens": 1000, "output_tokens": 200,
             "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 0}
    if has("bigusage"):
        usage = {"input_tokens": 600000, "output_tokens": 2000,
                 "cache_read_input_tokens": 500000, "cache_creation_input_tokens": 0}
    model_usage = {"glm-5.3[1m]": {
        "inputTokens": usage["input_tokens"], "outputTokens": usage["output_tokens"],
        "cacheReadInputTokens": usage["cache_read_input_tokens"],
        "cacheCreationInputTokens": 0, "webSearchRequests": 2,
        "costUSD": 0.05, "contextWindow": 1000000, "maxOutputTokens": 32000}}
    _emit({"type": "result", "subtype": "success", "result": text,
           "session_id": session_id, "total_cost_usd": 0.05,
           "usage": usage, "modelUsage": model_usage, "num_turns": 2, "duration_ms": 800})
    return 0


if __name__ == "__main__":
    sys.exit(main())
