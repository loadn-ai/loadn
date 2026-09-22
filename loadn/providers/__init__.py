"""Provider 层契约：chat() 异步流式接口 + chunk 类型 + 配置解析。

实现：anthropic.py（原生协议，兼容 Z.AI 等 Anthropic 形态网关）、
openai_compat.py（DeepSeek/GLM chat-completions 三向转换）、fake.py
（LOADN_PROVIDER=fake，回放 .fake 控制文件，测试零 token）。
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from loadn.types import Message, ToolDef


# ---------------------------------------------------------------- chunk
@dataclass
class Chunk:
    """provider 流式增量（对齐 Anthropic SSE delta 分类）。

    kind ∈ text_delta | thinking_delta | input_json_delta | usage | stop | error
    """
    kind: str
    text: str = ""                # text_delta / thinking_delta 载荷
    tool_use_id: str = ""         # input_json_delta：所属 tool_use
    tool_name: str = ""           # input_json_delta 首片：工具名
    partial_json: str = ""        # input_json_delta：参数增量
    message_id: str = ""
    model: str = ""
    # usage：message_start（input/cache 侧）与 message_stop（全量）两波
    usage: dict | None = None
    stop_reason: str = ""
    error: str = ""               # kind=error 载荷
    retriable: bool = False       # error 是否可重试（retry.py 判定依据）


@runtime_checkable
class Provider(Protocol):
    """一次 chat = 一轮 assistant 响应的完整 chunk 流（以 stop/error 结尾）。

    model：per-call 覆盖实例默认模型（摘要/planner 用小模型）；None = 默认。
    use_cache：False = 辅助请求（摘要/planner/grace）不打缓存断点——不污染
    主会话的缓存路由（pi 的 cacheRetention:"none" 同义）。
    """

    def chat(self, messages: list[Message], tools: list[ToolDef], system: str,
             *, stream: bool = True, model: str | None = None,
             use_cache: bool = True) -> AsyncIterator[Chunk]: ...

    @property
    def model_name(self) -> str: ...


# ---------------------------------------------------------------- 配置解析
def api_model_name(model: str) -> str:
    """API 侧模型名：剥掉 "[...]" 变体后缀（glm-5.3[1m] 是 claude CLI 的
    客户端标记，Z.AI 等 Anthropic 形态网关只认裸名 glm-5.3——2026-09-15
    实测 [1m] 直发 400 模型不存在）。窗口语义由 core/build._window_of
    从原始名推断，不经过本函数。"""
    n = (model or "").split("[", 1)[0].strip()
    return n or model


def provider_config() -> dict:
    """端点/密钥解析序（高 → 低）：

    1. $LOADN_HOME/config.json（自有覆盖：provider/base_url/api_key/model）
    2. 环境变量 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY /
       ANTHROPIC_DEFAULT_*_MODEL / LOADN_PROVIDER=fake
    3. ~/.claude/settings.json 的 env 块（claude CLI 权威来源——本机即 Z.AI
       Anthropic 形态网关，同一端点/密钥复用，零新配置）

    返回 {"provider": anthropic|openai|fake, "base_url", "api_key",
          "model", "small_model", "extra": {…}}。
    """
    from loadn import loadn_home

    cfg: dict = {"provider": "anthropic", "base_url": "", "api_key": "",
                 "model": None, "small_model": None, "extra": {}}

    # -- 3) claude CLI 的 env 块（兜底基线）
    try:
        p = Path.home() / ".claude" / "settings.json"
        envs = (json.loads(p.read_text()).get("env") or {})
        if isinstance(envs, dict):
            cfg["base_url"] = envs.get("ANTHROPIC_BASE_URL") or ""
            cfg["api_key"] = envs.get("ANTHROPIC_AUTH_TOKEN") \
                or envs.get("ANTHROPIC_API_KEY") or ""
            cfg["model"] = envs.get("ANTHROPIC_DEFAULT_SONNET_MODEL") \
                or envs.get("ANTHROPIC_MODEL") or cfg["model"]
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    # -- 2) 环境变量
    if os.environ.get("ANTHROPIC_BASE_URL"):
        cfg["base_url"] = os.environ["ANTHROPIC_BASE_URL"]
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        cfg["api_key"] = os.environ["ANTHROPIC_AUTH_TOKEN"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        cfg["api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("ANTHROPIC_MODEL"):
        cfg["model"] = os.environ["ANTHROPIC_MODEL"]
    if os.environ.get("LOADN_PROVIDER") or os.environ.get("HAHANESS_PROVIDER"):
        cfg["provider"] = (os.environ.get("LOADN_PROVIDER")
                           or os.environ["HAHANESS_PROVIDER"]).strip().lower()
    if os.environ.get("LOADN_BASE_URL"):
        cfg["base_url"] = os.environ["LOADN_BASE_URL"]
    if os.environ.get("LOADN_API_KEY"):
        cfg["api_key"] = os.environ["LOADN_API_KEY"]
    if os.environ.get("LOADN_MODEL"):
        cfg["model"] = os.environ["LOADN_MODEL"]
    if os.environ.get("LOADN_THINKING_BUDGET"):
        # thinking 预算旋钮（P2：竞赛类任务 thinking 无节制的闸门）；
        # int 解析失败静默弃（保持零配置可用）
        try:
            cfg["extra"]["thinking_budget"] = int(os.environ["LOADN_THINKING_BUDGET"])
        except (TypeError, ValueError):
            pass

    # -- 1) 自有 config.json（最高优先）
    try:
        own = json.loads((loadn_home() / "config.json").read_text())
        if isinstance(own, dict):
            for k in ("provider", "base_url", "api_key", "model", "small_model"):
                if own.get(k):
                    cfg[k] = own[k]
            if isinstance(own.get("extra"), dict):
                cfg["extra"].update(own["extra"])
    except (OSError, json.JSONDecodeError):
        pass

    if not cfg["provider"]:
        cfg["provider"] = "anthropic"
    return cfg


def build_provider(cfg: dict | None = None):
    """按配置实例化 provider（fake 走 env 控制文件；anthropic/openai 惰性导入）。"""
    cfg = cfg or provider_config()
    kind = cfg.get("provider") or "anthropic"
    if kind == "fake":
        from loadn.providers.fake import FakeProvider
        return FakeProvider()
    if kind in ("openai", "openai_compat", "openai-compatible"):
        from loadn.providers.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(cfg)
    from loadn.providers.anthropic import AnthropicProvider
    return AnthropicProvider(cfg)
