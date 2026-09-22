"""stream-json 输出层：AgentCore 事件 → claude CLI 兼容 NDJSON（stdout）。

契约与 宿主平台 tests/fake_claude.py 逐字段对齐（宿主平台 消费层零改动）：
  首行   {"type":"system","subtype":"init","session_id","model","tools","mcp_servers"}
  增量   {"type":"assistant","message":{"id":"msg_<hex>","role":"assistant","content":[blocks]}}
  流式   {"type":"stream_event","event":{Anthropic SSE 形事件},"parent_tool_use_id":null}
         —— --verbose 时逐 delta 外发（claude CLI 同位语义；宿主打字机渲染可用）
  回填   {"type":"user","message":{"role":"user","content":[{"type":"tool_result",…}]}}
  观测   {"type":"plan",…} / {"type":"todos","todos":[…]}（planner 判定/清单变更）
  心跳   {"type":"system","subtype":"heartbeat","ts"}   —— 工具长执行时 stdout 判活续命
  终止   {"type":"result","subtype","result","session_id","total_cost_usd","usage",
          "modelUsage","num_turns","duration_ms"}
"""
from __future__ import annotations

import json
import sys
import time
import uuid

from loadn import constants


class StreamJsonEmitter:
    """AgentCore 的 emit 回调 → stdout NDJSON（flush 每行，供父进程逐行读）。"""

    def __init__(self, model: str = "", tools: list[str] | None = None) -> None:
        self.model = model
        self.tools = tools or []
        self.session_id = ""        # CLI 在 run 前注入（result 事件字段）
        self._init_sent = False
        self._msg_n = 0

    def send_init(self, session_id: str) -> None:
        self._emit({"type": "system", "subtype": "init", "session_id": session_id,
                    "model": self.model or "unknown",
                    "tools": self.tools, "mcp_servers": []})
        self._init_sent = True

    def send_heartbeat(self) -> None:
        self._emit({"type": "system", "subtype": "heartbeat",
                    "ts": round(time.time(), 3)})

    async def __call__(self, event: dict) -> None:
        t = event.get("type")
        if t == "assistant":
            msg = event["message"]
            self._msg_n += 1
            self._emit({"type": "assistant", "message": {
                "id": event.get("message_id") or f"msg_{uuid.uuid4().hex[:12]}",
                "role": "assistant",
                "content": [b.to_dict() for b in msg.content]}})
        elif t == "tool_result":
            blk = event["block"]
            self._emit({"type": "user", "message": {
                "role": "user", "content": [blk.to_dict()]}})
        elif t == "plan":
            self._emit({"type": "plan", "session_id": self.session_id,
                        **(event.get("plan") or {})})
        elif t == "todos":
            self._emit({"type": "todos", "session_id": self.session_id,
                        "todos": event.get("todos") or []})
        elif t == "stream_event":
            self._emit({"type": "stream_event", "event": event["event"],
                        "session_id": self.session_id,
                        "parent_tool_use_id": None})
        elif t == "steer":
            # 用户插话被注入（消费回执）：宿主据此把该条从待回队列摘除
            self._emit({"type": "steer", "session_id": self.session_id,
                        "text": event.get("text") or ""})
        elif t == "turn":
            self.send_result(event["summary"])

    def send_result(self, summary) -> None:
        cost = 0.0
        mu_out = {}
        for m, mu in (summary.model_usage or {}).items():
            c = constants.cost_usd(
                m, input_t=mu.get("inputTokens") or 0,
                cache_read_t=mu.get("cacheReadInputTokens") or 0,
                cache_write_t=mu.get("cacheCreationInputTokens") or 0,
                output_t=mu.get("outputTokens") or 0)
            mu_out[m] = {**mu, "costUSD": round(c, 6)}
            cost += c
        self._emit({
            "type": "result", "subtype": summary.subtype,
            "result": summary.text,
            "session_id": self.session_id
            or getattr(summary, "session_id", "") or "",
            "total_cost_usd": round(cost, 6),
            "usage": summary.usage or {},
            "modelUsage": mu_out,
            "num_turns": summary.num_turns,
            "duration_ms": int(summary.duration_s * 1000)})

    @staticmethod
    def _emit(ev: dict) -> None:
        sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
        sys.stdout.flush()
