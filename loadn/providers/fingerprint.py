"""CC-Fingerprint：GLM 通道的客户端特征对齐层（把请求出口伪装成 Claude Code）。

用途与边界：GLM Coding Plan 按客户端形态提供服务，非 CC 客户端有封号风险。
本层只动请求出口（headers/请求体/system 前缀），不碰引擎逻辑；开关
`LOADN_STEALTH=cc`（或 config.json `extra.stealth`），默认关闭，且
**只在 GLM 通道自动生效**（`cc-all` 强制全通道，仅供测试）。

单一真相：全部特征集中在 CC_PROFILE——版本跟追只改这里（来源：
cchistory + 一次真机抓包校准，流程见 docs/stealth.md）。实例级特征
（user_id）生成一次落盘复用——随机漂移本身就是异常特征。

红线：行为纪律 > 伪装（并发 ≤3 会话、高峰错峰、独立账号承担风险）。
"""
from __future__ import annotations

import json
import platform
import uuid
from dataclasses import dataclass
from pathlib import Path

# GLM 通道的 base_url 主机匹配（stealth=cc 只对这些通道生效）
GLM_HOSTS = ("bigmodel.cn", "zhipuai.cn", "zhipu.ai")

# CC 身份句（事实性身份句=最强 prompt 指纹；provider 层统一前置，
# 覆盖主循环与摘要/planner 等全部辅助请求）
CC_IDENTITY_PREFIX = "You are a Claude agent, built on Anthropic's Claude Agent SDK."


@dataclass(frozen=True)
class CCProfile:
    """Claude Code 客户端特征快照。

    2026-09-17 真机校准（本机 CC 2.1.183 打本地捕获网关，零 token——
    流程与再校准见 docs/stealth.md）。版本跟追：cchistory 出新版时重跑校准。
    """

    CC_VERSION: str = "2.1.183"
    CC_BUILD: str = "ee8"              # billing 头的构建后缀
    AGENT_SDK_VERSION: str = "0.3.263" # UA 的 agent-sdk 段
    SDK_VERSION: str = "0.94.0"        # x-stainless-package-version（内嵌 TS SDK）
    BETA_FLAGS: str = ("claude-code-20250219,interleaved-thinking-2025-05-14,"
                       "context-management-2025-06-27,prompt-caching-scope-2026-01-05,"
                       "mid-conversation-system-2026-04-07,effort-2025-11-24")
    NODE_VERSION: str = "v24.3.0"      # CC 自带 node 运行时（x-stainless-runtime-version）
    # CC 按模型档位的习惯输出上限；抓包实测 glm-5.3=32000
    MAX_TOKENS: dict = None            # type: ignore[assignment]

    def max_tokens_for(self, model: str) -> int:
        table = self.MAX_TOKENS or {}
        m = (model or "").lower()
        for k, v in table.items():
            if k in m:
                return int(v)
        return 32000

    @property
    def user_agent(self) -> str:
        return (f"claude-cli/{self.CC_VERSION} (external, sdk-ts, "
                f"agent-sdk/{self.AGENT_SDK_VERSION})")

    @property
    def billing_block(self) -> str:
        return (f"x-anthropic-billing-header: cc_version={self.CC_VERSION}.{self.CC_BUILD};"
                f" cc_entrypoint=sdk-ts;")


CC_PROFILE = CCProfile()


# ---------------------------------------------------------------- 开关与门控
def stealth_mode() -> str:
    """伪装档位：""（关）| cc（仅 GLM 通道）| cc-all（全通道，测试用）。

    来源：env LOADN_STEALTH > config.json extra.stealth。
    """
    import os

    mode = (os.environ.get("LOADN_STEALTH")
            or os.environ.get("HAHANESS_STEALTH", "")).strip().lower()
    if mode in ("", "off", "none", "0", "false"):
        return ""
    if mode not in ("cc", "cc-all"):
        return ""
    return mode


def active(base_url: str) -> bool:
    """伪装是否对该通道生效：cc → 仅 GLM 主机；cc-all → 全部。"""
    mode = stealth_mode()
    if not mode:
        return False
    if mode == "cc-all":
        return True
    host = (base_url or "").lower()
    return any(h in host for h in GLM_HOSTS)


# ---------------------------------------------------------------- 实例身份
def identity(home: Path) -> dict:
    """稳定的实例身份（生成一次落盘复用；随机漂移=异常特征）。

    真机形状（2026-09-17 抓包）：metadata.user_id 是 JSON 字符串
    {"device_id":<64hex>, "account_uuid":"", "session_id":<uuid>}——
    device_id 实例级恒定；account_uuid 空（AUTH_TOKEN 计费无 OAuth 账号）；
    session_id 每会话一个 uuid（辅助请求无会话时用落盘的固定 aux id）。
    """
    f = Path(home) / "stealth_identity.json"
    try:
        if f.exists():
            data = json.loads(f.read_text())
            if isinstance(data, dict) and data.get("device_id"):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    ident = {"device_id": uuid.uuid4().hex + uuid.uuid4().hex,
             "aux_session_id": str(uuid.uuid4())}
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(ident))
    except OSError:
        pass                  # 落盘失败：本次内存态兜底（下次重新生成）
    return ident


# ---------------------------------------------------------------- 出口变换
def cc_headers(api_key: str, *, retry_count: int = 0) -> dict[str, str]:
    """CC 形态的请求头（含 x-stainless 全家——Python 客户端必不带、真 CC 必带，
    是比 UA 更硬的指纹）。os/arch 与宿主真实一致（CC 也跑在 Linux 上，
    撒谎没必要）；retry_count 随重试递增（SDK 真实行为）。

    注意：不含 x-api-key——CC 用 ANTHROPIC_AUTH_TOKEN 时只发 Bearer 形
    （GLM Coding Plan 教程即此接法），双头反而是异常。
    """
    p = CC_PROFILE
    machine = platform.machine() or "x86_64"
    return {
        "User-Agent": p.user_agent,
        "authorization": f"Bearer {api_key}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": p.BETA_FLAGS,
        "x-app": "cli",
        "x-stainless-lang": "js",
        "x-stainless-package-version": p.SDK_VERSION,
        "x-stainless-os": platform.system() or "Linux",
        "x-stainless-arch": {"x86_64": "x64", "aarch64": "arm64"}.get(machine, "x64"),
        "x-stainless-runtime": "node",
        "x-stainless-runtime-version": p.NODE_VERSION,
        "x-stainless-retry-count": str(retry_count),
    }


def cc_request_body(body: dict, sid: str, home: Path,
                    effort: str = "high") -> dict:
    """请求体对齐真机形状（2026-09-17 抓包）：

    metadata.user_id=JSON(device_id/account_uuid/session_id)；stream 恒真；
    thinking=adaptive；output_config.effort；context_management 编辑项；
    去 temperature。sid 缺省（辅助请求）用落盘的固定 aux session id。
    """
    ident = identity(home)
    user_id = json.dumps({
        "device_id": ident["device_id"],
        "account_uuid": "",
        "session_id": sid or ident["aux_session_id"],
    }, ensure_ascii=False)
    out = dict(body)
    out["metadata"] = {"user_id": user_id}
    out["stream"] = True
    out["thinking"] = {"type": "adaptive"}
    out["output_config"] = {"effort": effort}
    out["context_management"] = {
        "edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}
    if "max_tokens" not in out:
        out["max_tokens"] = CC_PROFILE.max_tokens_for(out.get("model", ""))
    out.pop("temperature", None)
    return out
