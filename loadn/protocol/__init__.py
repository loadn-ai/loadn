"""协议清单（P2-3，opencode event-manifest 同构）：v1/v2 事件三维声明。

- **增量原则**：v2 事件叠加在 v1 之上，默认（无 `--protocol v2`）引擎
  只发 v1 事件流——旧宿主（现有 webui 不改）在 v2 引擎下行为不变。
- 三维：`since`（引入版本）/ `durable`（P2-2 选择性落盘标记：事件终态
  后回放是否必须）/ `latest`（现行版本——将来 v3 弃用位）。
- v1 桥（opencode event-v2-bridge 同构）：v2 事件名 → v1 形态映射，
  `--protocol v2` 时**双发**（v2 原生 + v1 兼容行），宿主按版本挑流。
"""
from __future__ import annotations

import json
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"

# v1 事件（PROTOCOL.md §2 固化，全部 since=v1）
# v2 增量事件：
#   permission_request  引擎→宿主：ask 语义上抛（带 params_hash，接 P0-4
#                       审批回写——宿主批准可规则化落 policy.json）
#   permission_result   宿主→引擎：allow/deny + rule化标志（stdin 注入）
#   tool_use_failure    工具执行失败终态（PostToolUseFailure 语义）
MANIFEST = {
    "version": 2,
    "events": {
        # ---- v1（durable=result 是判死/记账依据；其余软事件不强制落盘）
        "system": {"since": 1, "durable": False, "latest": 2},
        "assistant": {"since": 1, "durable": False, "latest": 2},
        "user": {"since": 1, "durable": False, "latest": 2},
        "stream_event": {"since": 1, "durable": False, "latest": 2},
        "steer": {"since": 1, "durable": False, "latest": 2},
        "plan": {"since": 1, "durable": False, "latest": 2},
        "todos": {"since": 1, "durable": False, "latest": 2},
        "result": {"since": 1, "durable": True, "latest": 2},
        # ---- v2 增量（PROTOCOL v2 节）
        "permission_request": {"since": 2, "durable": True, "latest": 2},
        "permission_result": {"since": 2, "durable": False, "latest": 2},
        "tool_use_failure": {"since": 2, "durable": False, "latest": 2},
    },
    # v1 桥：v2 事件在 v1 流中的映射（v2 双发时的兼容行形态）
    "v1_bridge": {
        "permission_request": None,     # v1 无对应——不发（宿主旧版不识，
                                        # 判死兜底只认 result）
        "permission_result": None,
        "tool_use_failure": "user",     # v1 里以 tool_result(is_error) 回填
    },
}


def load_manifest() -> dict:
    """清单（落盘版优先——manifest.json 与代码内声明同步演进）。"""
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return MANIFEST


def event_meta(name: str) -> dict:
    return load_manifest()["events"].get(name) or {}


def is_v1(name: str) -> bool:
    return event_meta(name).get("since", 1) == 1


def v1_bridge(name: str) -> str | None:
    """v2 事件在 v1 流的映射形态；None=不桥（v1 宿主不可见）。"""
    return load_manifest()["v1_bridge"].get(name)
