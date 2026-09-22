"""配置：dataclass 默认值 + 仓库根 config.yaml 覆盖 + PATHS 集中管理。

设计承袭 papergo/config.py：默认值全在代码里，config.yaml 可选、按
dataclass 名分节覆盖，不存在的键忽略——升级不破坏用户配置。
LOADN_WEBUI_HOME 环境变量整体迁移（测试/多实例；旧 WORKDADDY_HOME 兼容一版）。
注意与引擎数据根 LOADN_HOME（=~/.loadn）语义不同：这是 webui 平台自身的根。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("LOADN_WEBUI_HOME")
           or os.environ.get("WORKDADDY_HOME")
           or Path(__file__).resolve().parent.parent)

# 代码真实所在仓（HOME 重定位后仍指向源码）——历史 hack：曾作为 PYTHONPATH
# 注入引擎子进程（W0 前的安全债），R1 起引擎经 pip console_script 解析，不再注入。
CODE_ROOT = Path(__file__).resolve().parent.parent

PATHS = {
    "root": ROOT,
    "workspace": ROOT / "workspace",
    "var": ROOT / "var",
    "db": ROOT / "var" / "loadn.db",
    "logs": ROOT / "var" / "logs",
    "call_logs": ROOT / "var" / "logs" / "calls",
    "run": ROOT / "var" / "run",
    "pid_dir": ROOT / "var" / "run" / "live_claude_pids",
    "stop_file": ROOT / "var" / "run" / "STOP",
    "pages_cache": ROOT / "var" / "pages_cache",
    "backups": ROOT / "var" / "backups",
    "profiles": ROOT / "profiles",
    "prompts": ROOT / "prompts",
    "skills": ROOT / "skills",
    "frontend_dist": ROOT / "ui" / "dist",
}


@dataclass
class ClaudeConfig:
    # model=None 继承 CLI/订阅默认。测试用 WORKDADDY_CLAUDE_BIN 指向假实现。
    effort: str = "high"
    model: str | None = None
    claude_bin: str | None = None


@dataclass
class PerEngineConfig:
    # 单引擎覆盖：bin=二进制路径（留空即自动探测 loadn console_script/venv）；
    # model/provider 为该引擎默认模型；extra_args 追加 CLI 参数。
    # 旧配置 engines.hahaness 段一版兼容：loadn 段未显式配置时回落读旧段。
    bin: str | None = None
    model: str | None = None
    provider: str | None = None      # opencode -m <provider>/<model> 的 provider 前缀
    enabled: bool = True
    extra_args: list[str] = field(default_factory=list)


@dataclass
class EnginesConfig:
    # 可插拔无头引擎（workdaddy/engines/ 注册表）。default 为全局默认，灰度切换
    # 只翻这一处；profile 可用 engine 字段按角色覆盖。opencode_provider 是
    # opencode 模型前缀默认（zai=Z.AI GLM）。no_compact 控制 hahaness 内压：
    # None=自动（profile 设了 rotate_input_tokens 则禁内压），True/False 恒定。
    default: str = "claude"
    opencode_provider: str = "zai"
    no_compact: bool | None = None
    claude: PerEngineConfig = field(default_factory=PerEngineConfig)
    opencode: PerEngineConfig = field(default_factory=PerEngineConfig)
    loadn: PerEngineConfig = field(default_factory=PerEngineConfig)
    hahaness: PerEngineConfig = field(default_factory=PerEngineConfig)   # 旧键 alias


@dataclass
class RunConfig:
    # 同时在跑的 claude 进程上限（订阅并发保护）；超出排队。
    max_concurrent_turns: int = 3
    # 精准回放：fresh connect 只回放活跃 turn 的尾部条数（前端 live 尾窗 60，
    # 200 有富余）；无活跃 turn 零回放。断线补发（Last-Event-ID）不受此限。
    replay_max_events: int = 200
    # 终态 turn 的 session_events 保留天数（超期清理；活跃 turn 永不清）。
    events_retain_days: float = 7
    # 精准回放：fresh connect 只回放活跃 turn 的尾部条数（前端 live 尾窗 60，
    # 200 有富余）；无活跃 turn 零回放。断线补发（Last-Event-ID）不受此限。
    replay_max_events: int = 200
    # 终态 turn 的 session_events 保留天数（超期清理；活跃 turn 永不清）。
    events_retain_days: float = 7


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"      # 非 127.0.0.1 时强制要求 token
    port: int = 8792
    token: str = ""
    # W0：管理面（skills/tools/settings/schedules/skillhub 的非 GET）独立令牌；
    # 空 = 复用 token。额外放行的 Host（反代域名等，Host 白名单用）。
    admin_token: str = ""
    extra_hosts: list = field(default_factory=list)
    # 运行时填充（非 yaml 键）：token 宽限期截止 epoch 秒。0 = 无宽限立即 enforce。
    # 机生 token（var/server_token）自动生成/启用起 14 天宽限——宽限内无凭证
    # 请求放行+显著告警，给用户时间把 token 配进前端；人配 server.token 无宽限。
    token_grace_until: float = 0.0


@dataclass
class McpConfig:
    # 全局 MCP servers（标准项目级 .mcp.json 的 mcpServers 格式），注入每个新会话。
    servers: dict = field(default_factory=dict)


@dataclass
class TitleGenConfig:
    # 自动标题：首条消息 → 外部小模型生成任务标题（火山方舟 OpenAI 兼容接口）。
    # api_key 为空 = 未配置，静默跳过。手动改名/显式标题优先，不会被覆盖。
    enabled: bool = True
    api_base: str = "https://ark.cn-beijing.volces.com/api/v3"
    api_key: str = ""
    model: str = "doubao-seed-2-0-mini-260428"


@dataclass
class SecurityConfig:
    """W1 确定性权限平面（v1.1 §6.2）。策略在模型之外；出厂即生效。"""
    # L0 红线正则（覆盖=追加；文本+AST 双拦）
    hard_blocklist_l0: list = field(default_factory=list)
    # 不可逆工具（W1-2 approvals 确认码门；先网关告警）
    irreversible_tools: list = field(default_factory=lambda: [
        "mail", "sms", "wechat", "pay", "account_write", "browser_export"])
    # 出口白名单（v1.1：出厂默认 enforce；存量部署可先 mode=warn 灰度两周）
    egress_allow: list = field(default_factory=lambda: [
        "api.anthropic.com", "api.bochaai.com", "open.bigmodel.cn",
        "sms.example.test", "registry.npmjs.org", "pypi.org",
        "ark.cn-beijing.volces.com", "2captcha.com", "capsolver.com"])
    egress_mode: str = "enforce"          # enforce | warn
    approval_ttl_s: int = 600
    approval_cooldown_after: int = 5
    # 不可逆动作确认码门（M0 关门最终形态=enforce：skill 文档已审批化）；
    # warn 仅作迁移期显式配置
    approval_enforce: str = "enforce"
    # W2-a 执行沙箱：off（默认，双轨——doctor 通过+真机验证后切 bwrap）| bwrap
    sandbox: str = "off"
    # W5.1 出口代理端口（0=随机绑定，lifespan 回写实际值；生产可固定 8793）
    egress_proxy_port: int = 0


@dataclass
class ShareConfig:
    # 产物分享：/share/<token> 公开只读路由挂在主服务上（VPS workdaddy.cc/share
    # → 本机 nginx :80 → 8792）；base_url 空 = 未启用（mint API 报错）。
    base_url: str = ""            # 如 https://workdaddy.cc/share


@dataclass
class NotifyConfig:
    # 运维通知（发给用户自己的手机，与会话内 wechat-send 语义不同）：
    # 调度唤醒 / turn 报错 / 完成 三类事件推 bark/Server酱/Telegram。
    # provider 空 = 未启用。telegram 走 resources.proxy（墙内必需）。
    provider: str = ""            # bark | serverchan | telegram
    bark_url: str = ""            # 如 https://api.day.app/<yourkey>
    serverchan_key: str = ""      # SendKey（sct…）
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # 事件开关（三类；turn_done 默认关防刷屏）
    events: dict = field(default_factory=lambda: {"on_error": True,
                                                  "on_scheduled": True,
                                                  "on_turn_done": False})


@dataclass
class PricingConfig:
    # 成本分析页价目（$/M tokens、积分/M tokens）。留空 = 用 workdaddy/pricing.py
    # 内置 z.ai 官方价目；覆盖即**整表替换**（非深合并），键结构见 pricing.py。
    api: dict = field(default_factory=dict)
    plan_credits: dict = field(default_factory=dict)
    usd_cny: float = 0.0          # 0 = 内置 7.1


@dataclass
class ResourcesConfig:
    # 外部资源接入（agent 动手能力）：端点默认即本机部署地址，密钥默认空、
    # 经设置 API 灌入 config.yaml。vlm 复用 titlegen 的 ark 接口，key 留空=继承。
    ocr_url: str = "http://127.0.0.1:8686"          # 多格式 OCR（图片/pdf/office）
    sandbox_url: str = "http://127.0.0.1:21111"     # AIO 沙箱（浏览器/命令行/文件）
    sandbox_api_key: str = ""
    cdp_url: str = "http://127.0.0.1:21111/cdp"     # 沙箱内 Chrome 的 CDP 端点
    proxy: str = "http://192.0.2.137:7890"        # clash，空=不可用
    sms_url: str = "https://sms.example.test:30443"    # 短信查询服务
    sms_token: str = ""
    sms_phone: str = "+86 10000000000"
    mail_imap: str = "imap.126.com:993"             # 平台邮箱（注册/登录收验证码）
    mail_smtp: str = "smtp.126.com:465"
    mail_user: str = "user@example.com"
    mail_auth_code: str = ""                        # 126 授权码（非登录密码）
    # 追加邮箱（收验证码用）：[{user, imap, smtp, auth_code}]；上面的单邮箱字段
    # 永远是 1 号（设置页编辑的就是它），这里放 2 号起。密钥只进 config.yaml。
    mailboxes: list = field(default_factory=list)
    vlm_api_base: str = ""                          # 空 = 继承 titlegen.api_base
    vlm_api_key: str = ""                           # 空 = 继承 titlegen.api_key
    vlm_model: str = "doubao-seed-2-1-turbo-260628"
    twocaptcha_key: str = ""
    bocha_key: str = ""                             # 博查中文网页搜索
    zhipu_key: str = ""                             # 智谱 Web Search API
    zhipu_engine: str = "search_pro"                # std 0.01 / pro 0.03 / pro_sogou|quark 0.05 元/次
    adb_addr: str = "192.0.2.78:5555"             # android 真机
    textr_email: str = ""                           # Textr Go 美国虚拟号码（收验证码）
    textr_password: str = ""


@dataclass
class Config:
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    engines: EnginesConfig = field(default_factory=EnginesConfig)
    run: RunConfig = field(default_factory=RunConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    titlegen: TitleGenConfig = field(default_factory=TitleGenConfig)
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    share: ShareConfig = field(default_factory=ShareConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)


def _apply_section(cfg_obj: object, section: dict) -> None:
    valid = {f.name: f for f in fields(cfg_obj)}
    for k, v in section.items():
        if k not in valid:
            continue
        cur = getattr(cfg_obj, k)
        if is_dataclass(cur) and isinstance(v, dict):
            _apply_section(cur, v)   # 嵌套分节（engines.claude 等）递归覆盖
        else:
            setattr(cfg_obj, k, v)


def load_config() -> Config:
    cfg = Config()
    path = ROOT / "config.yaml"
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            data = {}
        if isinstance(data, dict):
            sections = {"claude": cfg.claude, "engines": cfg.engines, "run": cfg.run,
                        "server": cfg.server, "mcp": cfg.mcp, "titlegen": cfg.titlegen,
                        "resources": cfg.resources, "share": cfg.share,
                        "notify": cfg.notify, "pricing": cfg.pricing, "security": cfg.security}
            for name, obj in sections.items():
                sec = data.get(name)
                if isinstance(sec, dict):
                    _apply_section(obj, sec)
    env_bin = os.environ.get("WORKDADDY_CLAUDE_BIN")
    if env_bin:
        cfg.claude.claude_bin = env_bin
    return cfg


CONFIG = load_config()


TOKEN_GRACE_DAYS = 14.0


def resolve_runtime_token() -> str:
    """serve 启动时解析/生成运行时 token（W0.1）。

    优先级：人配 server.token（无宽限，立即 enforce）> var/server_token 文件
    （机生 0600，14 天宽限）> 首次生成（打印一次 + 宽限开始）。写回
    CONFIG.server.token / token_grace_until 并返回生效 token。
    """
    import secrets
    import time

    if CONFIG.server.token:
        CONFIG.server.token_grace_until = 0.0
        return CONFIG.server.token
    ensure_dirs()
    p = PATHS["var"] / "server_token"
    if p.exists():
        token = p.read_text().strip()
        if token:
            CONFIG.server.token = token
            CONFIG.server.token_grace_until = p.stat().st_mtime + TOKEN_GRACE_DAYS * 86400
            return token
    token = secrets.token_urlsafe(32)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(token + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    CONFIG.server.token = token
    CONFIG.server.token_grace_until = time.time() + TOKEN_GRACE_DAYS * 86400
    print("=" * 62
          + "\n[W0] 已自动生成 API token（var/server_token，0600）：\n  " + token
          + f"\n浏览器端首次访问会要求输入；{TOKEN_GRACE_DAYS:.0f} 天宽限期后强制认证。"
          + "\n查看/轮换：loadn-web token show | loadn-web token rotate（wd alias 同效）\n" + "=" * 62)
    return token


def admin_token_value() -> str:
    """管理面令牌：显式配置优先，默认复用 API token。"""
    return CONFIG.server.admin_token or CONFIG.server.token


def ensure_dirs() -> None:
    for key in ("workspace", "var", "logs", "call_logs", "run", "pid_dir",
                "pages_cache", "backups"):
        PATHS[key].mkdir(parents=True, exist_ok=True)

def skills_dirs() -> list[Path]:
    """skill 搜索路径（overlay 双轨，v1.1 §4.3 R2）：repo skills/（公开/脱敏）
    在前，$LOADN_HOME/skills（部署机私有 overlay）在后；同名以先者为准。
    旧仓 skills 目录经 LOADN_SKILLS_EXTRA 追加（迁移过渡）。"""
    dirs = [PATHS["skills"]]
    extra = os.environ.get("LOADN_SKILLS_EXTRA")
    if extra:
        dirs.append(Path(extra))
    home_skills = Path(os.environ.get("LOADN_HOME") or Path.home() / ".loadn") / "skills"
    if home_skills not in dirs:
        dirs.append(home_skills)
    return [d for d in dirs if d.exists()]


def migrate_legacy_db() -> Path | None:
    """旧 var/workdaddy.db → var/loadn.db 首启迁移（copy 不动旧；返回迁移结果）。

    仅在目标不存在且旧库存在时执行；WAL 场景 copy .db + -wal + -shm 三件。
    生产切换（R2.5）前旧服务可能仍在写旧库——切换窗口内执行才终一致。
    """
    old = PATHS["var"] / "workdaddy.db"
    new = PATHS["db"]
    if new.exists() or not old.exists():
        return None
    import shutil
    new.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(old) + suffix)
        if src.exists():
            shutil.copy2(src, str(new) + suffix)
    return new
