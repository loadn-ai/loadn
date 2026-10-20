"""首跑引导（onboarding）：裸 ``loadn`` 交互模式第一步。

对标 Claude Code 首跑体验——新用户第一次跑 loadn，不该面对一个空输入框
和一个"发消息才炸"的未配置引擎。本模块在 TUI/REPL 之前（provider_config
解析之前）跑终端问答：

  1. 欢迎屏（版本/数据根/一句话定位）
  2. 现状检测：已配置（env/claude settings/auth.json/config.json 任一
     有端点+密钥）→ 只提示不问询；未配置 → 引导三问
  3. 选预设端点（GLM 开放平台/DeepSeek/Z.AI/Anthropic/Kimi/自定义），
     base_url/model 预填默认、回车接受；api_key 走 getpass 不回显
  4. 合并写 ``$LOADN_HOME/config.json``（0600 原子写，auth.json 同款；
     只更新已答键，既有 small_model/extra 等不碰）
  5. 幂等 marker（``.onsite_done``）——已引导过不再骚扰；手动重跑
     ``loadn setup`` 随时可进

headless（-p）不经过这里（无人可问，fail-closed 哲学与信任门一致）；
非 tty（管道）同样跳过。
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

from loadn import __version__, loadn_home

MARKER_NAME = ".onsite_done"

# 预设端点表（引导选单）：name 唯一键；provider 与 provider_config 契约对齐
# （anthropic=原生 SSE 形态；openai_compat=chat-completions 三向转换）。
PRESETS: list[dict] = [
    {"key": "1", "name": "GLM 开放平台（智谱 BigModel）",
     "provider": "openai_compat",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "model": "glm-4.6"},
    {"key": "2", "name": "DeepSeek",
     "provider": "openai_compat",
     "base_url": "https://api.deepseek.com",
     "model": "deepseek-chat"},
    {"key": "3", "name": "Z.AI（GLM Coding Plan，Anthropic 形态）",
     "provider": "anthropic",
     "base_url": "https://api.z.ai/api/anthropic",
     "model": "glm-4.6"},
    {"key": "4", "name": "Anthropic 官方",
     "provider": "anthropic",
     "base_url": "https://api.anthropic.com",
     "model": "claude-sonnet-4-5"},
    {"key": "5", "name": "Kimi（Moonshot）",
     "provider": "openai_compat",
     "base_url": "https://api.moonshot.cn/v1",
     "model": "kimi-k2-turbo-preview"},
    {"key": "6", "name": "自定义 OpenAI 兼容端点（vLLM/OneAPI/其他）",
     "provider": "openai_compat", "base_url": "", "model": ""},
]
JUMP_KEY = "s"   # 跳过（保持现状，marker 照写——不再每次启动骚扰）


# ---------------------------------------------------------------- 纯函数

def marker_path() -> Path:
    return loadn_home() / MARKER_NAME


def mask_key(key: str) -> str:
    """密钥脱敏（提示行用）：前 4 后 4；过短全打码。"""
    if not key:
        return "（未设）"
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "****"


def needs_onboarding(cfg: dict, marker_exists: bool) -> bool:
    """是否需要走完整引导：未引导过 且 端点/密钥有缺口。

    已配置（env/claude settings 已带端点+密钥）即使无 marker 也不进
    问询——检测行提示后补写 marker 即可（每启动都问是骚扰不是引导）。
    fake provider（测试/回放通道）无需端点，同视为已配置。
    """
    if marker_exists:
        return False
    if str(cfg.get("provider") or "").lower() == "fake":
        return False
    return not (cfg.get("base_url") and cfg.get("api_key"))


def merge_write_config(home: Path, updates: dict) -> Path:
    """合并写 config.json：只落 updates 非空键，既有键不碰；0600 原子写
    （tmp→chmod→rename，auth.json write_auth 同款——半写不落世界可读态）。"""
    p = home / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, json.JSONDecodeError, ValueError):
        pass   # 坏文件当不存在（引导修复它的路本身就是重写）
    for k in ("provider", "base_url", "api_key", "model"):
        if updates.get(k):
            data[k] = updates[k]
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, p)
    return p


def write_marker() -> None:
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("ok\n", encoding="utf-8")


# ---------------------------------------------------------------- 交互流程

def _ask(prompt: str, default: str = "") -> str:
    """带默认值的问询行：回车/EOF=接受默认（空默认则真空）。"""
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        ans = ""
    return ans or default


def run_onboarding(*, force: bool = False) -> int:
    """引导主流程（loadn setup 子命令同入口；force=已配置也重问）。

    返回 exit code（0=正常结束——含用户跳过；引导本身无失败态，
    写盘异常兜底为提示不炸）。"""
    from loadn.providers import provider_config

    cfg = provider_config()
    home = loadn_home()
    marker = marker_path().exists()

    print()
    print("╭──────────────────────────────────────────────────╮")
    print("│  loadn —— 自研 Claude Code 内涵的原生 agent 引擎  │")
    print("╰──────────────────────────────────────────────────╯")
    print(f"  版本 {__version__} · 数据根 {home}")
    print()

    if not force and not needs_onboarding(cfg, marker):
        # 已可用（配置齐 or 引导过）：检测行 + 补 marker，零问询
        if cfg.get("base_url") or cfg.get("api_key"):
            print(f"✓ 已配置：{cfg.get('provider') or 'anthropic'}"
                  f" · {cfg.get('model') or '（默认模型）'}"
                  f" · 密钥 {mask_key(str(cfg.get('api_key') or ''))}")
        if not marker:
            write_marker()
        print("  （改配置：loadn setup，或手编 config.json）")
        return 0

    print("首次使用需要配置一个模型端点（之后随时 loadn setup 修改）。")
    print("预设端点：")
    for p in PRESETS:
        print(f"  {p['key']}. {p['name']}")
    print(f"  {JUMP_KEY}. 跳过（沿用环境变量/claude settings 现状）")
    print()

    preset: dict = {}
    while True:
        choice = _ask("选择端点").lower()
        if choice == JUMP_KEY:
            break
        preset = next((p for p in PRESETS if p["key"] == choice), None)  # type: ignore[assignment]
        if preset is not None:
            break
        print("  （无效选择——输入数字或 s）")

    updates: dict = {}
    if preset:
        print(f"\n→ {preset['name']}（provider: {preset['provider']}）")
        base_url = _ask("  base_url", preset["base_url"])
        if base_url:
            updates["base_url"] = base_url
            updates["provider"] = preset["provider"]
        try:
            key = getpass.getpass("  api_key（输入不回显，回车=跳过）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            key = ""
        if key:
            updates["api_key"] = key
        model = _ask("  model", preset["model"])
        if model:
            updates["model"] = model

    if updates:
        p = merge_write_config(home, updates)
        print(f"\n✓ 已写入 {p}（0600）"
              f"——{'·'.join(sorted(updates))} 共 {len(updates)} 键")
    else:
        print("\n（未写任何配置——沿用现状）")
    write_marker()
    print("✓ 引导完成。修改：loadn setup ｜ 帮助：TUI 里 /help")
    return 0


def maybe_onboard() -> None:
    """交互模式自动入口（main.py 在 provider_config 之前调用）：

    只在 tty + 未引导过时静默进入；已引导/已配置零输出（不打扰脚本
    与老用户）。异常全吞——引导失败不挡正常启动（零配置也能跑 fake）。"""
    try:
        if not sys.stdin.isatty():
            return
        from loadn.providers import provider_config
        cfg = provider_config()
        if not needs_onboarding(cfg, marker_path().exists()):
            if not marker_path().exists():
                write_marker()
            return
        run_onboarding()
    except Exception:   # noqa: BLE001 — 引导是增强，不成为启动路径的单点
        pass
