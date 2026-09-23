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
           or os.environ.get("WORKDADDY_HOME")      # 旧名兼容（迁移期）
           or Path.home() / ".loadn-data")          # 开源默认：用户家目录

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
    # 行为类资产：代码根给默认值，数据根可覆盖（同 skills overlay 模式——
    # 代码版本走 monorepo，用户定制走数据根；frontend_dist 教训：行为
    # 定义拴数据根则代码更新到不了生产，宪法安全章节实际丢了）
    # 注意这里是**默认路径**，运行时用 behavior_dirs() 搜索（下文）
    "skills": ROOT / "skills",
    # 前端是代码工件（随 monorepo 走），不是数据——用代码根而非数据根
    "frontend_dist": CODE_ROOT / "ui" / "dist",
}


@dataclass
class ClaudeConfig:
    # model=None 继承 CLI/订阅默认。测试用 LOADN_CLAUDE_BIN 指向假实现。
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
    # 可插拔无头引擎（loadn_webui/engines/ 注册表）。default 为全局默认，灰度切换
    # 只翻这一处；profile 可用 engine 字段按角色覆盖。opencode_provider 是
    # opencode 模型前缀默认（zai=Z.AI GLM）。no_compact 控制 loadn 内压：
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


# W2 沙箱档位统一枚举（跨平台三方案 A/B/C 的归一）：
#   off           降级档：直跑（诚实标注「本机文件未隔离」，W1/W3/W6 仍生效）
#   bwrap         Linux 原生 bwrap 档（服务器/生产形态）
#   vm-bwrap      桌面 VM 执行域（loadn desktop 置备的 Linux VM 内跑平台；
#                 执行语义=bwrap，requested 值本身即「运行于 VM」的报告标记）
#   seatbelt      mac 原生轻量档（长期可选，未实现 → 降档 off+审计）
#   appcontainer  win 原生轻量档（长期可选，未实现 → 降档 off+审计）
#   remote        远程执行器档（企业场景，未实现 → 降档 off+审计）
# 解析/探测/降档见 sandbox.py resolve_tier()；枚举校验在 load_config()。
SANDBOX_TIERS = ("off", "bwrap", "vm-bwrap", "seatbelt", "appcontainer", "remote")


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
        "registry.npmjs.org", "pypi.org",
        "ark.cn-beijing.volces.com", "2captcha.com", "capsolver.com",
        "opencode.ai", "llm-gw.internal",
        "github.com", "githubusercontent.com",
        "arxiv.org"])   # 后缀匹配：export.arxiv.org（API/PDF）一并覆盖
    egress_mode: str = "enforce"          # enforce | warn
    # SSRF 内网敏感域（fetch_page 等宿主中介抓取的禁入后缀清单）——私网/回环/
    # 链路本地 IP 段无条件拦截，这里只补「解析得到公网 IP 但属于平台侧通道」的域
    ssrf_deny_hosts: list = field(default_factory=lambda: [
        "llm-gw.internal", "sms.woldy.net"])
    approval_ttl_s: int = 600
    approval_cooldown_after: int = 5
    # 不可逆动作确认码门（M0 关门最终形态=enforce：skill 文档已审批化）；
    # warn 仅作迁移期显式配置
    approval_enforce: str = "enforce"
    # W2-a 执行沙箱档位（SANDBOX_TIERS；默认 off 双轨——doctor 通过+真机验证后切 bwrap）
    sandbox: str = "off"
    # W5.1 出口代理端口（0=随机绑定，lifespan 回写实际值；生产可固定 8793）
    egress_proxy_port: int = 0


@dataclass
class ShareConfig:
    # 产物分享：/share/<token> 公开只读路由（VPS 反代 your-domain.com/share
    # → 本机 nginx :80 → 8792）；base_url 空 = 未启用（mint API 报错）。
    base_url: str = ""            # 如 https://your-domain.com/share


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
    # 成本分析页价目（$/M tokens、积分/M tokens）。留空 = 用 loadn_webui/pricing.py
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
    proxy: str = ""        # 出网代理（如 http://127.0.0.1:7890），空=不可用
    sms_url: str = ""       # 短信查询服务端点，空=未部署
    sms_token: str = ""
    sms_phone: str = ""
    mail_imap: str = "imap.126.com:993"             # 平台邮箱（注册/登录收验证码）
    mail_smtp: str = "smtp.126.com:465"
    mail_user: str = ""      # 平台邮箱账号（如 you@example.com）
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
    adb_addr: str = ""       # android 真机（如 192.168.x.x:5555）
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
    # W2 档位枚举 fail-closed：安全关键配置的笔误不允许静默降级为直跑——
    # 启动即报错（旧部署仅用过 off|bwrap，均在枚举内，无迁移面）。
    # yaml 1.1 坑：裸 off/on 解析为布尔——False 归一化回 "off"（手写 yaml 的
    # `sandbox: off` 是存量合法写法；True 无对应档，走枚举报错）
    if isinstance(cfg.security.sandbox, bool):
        cfg.security.sandbox = "off" if cfg.security.sandbox is False else "on"
    if cfg.security.sandbox not in SANDBOX_TIERS:
        raise ValueError(
            f"security.sandbox={cfg.security.sandbox!r} 不在档位枚举 "
            f"{SANDBOX_TIERS} 内（config.yaml）——拒绝启动，请修正后重试")
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
                "pages_cache", "backups", "skills"):   # skills：数据根 overlay 首启即建
        PATHS[key].mkdir(parents=True, exist_ok=True)

def behavior_file(name: str, rel: str) -> Path:
    """在 behavior_dirs(name) 搜索路径中找 rel 文件（先到先得=数据根覆盖）。"""
    for d in behavior_dirs(name):
        f = d / rel
        if f.exists():
            return f
    return behavior_dirs(name)[0] / rel      # 不存在时返回默认位置（调用方报错）


def behavior_dirs(name: str) -> list[Path]:
    """行为类资产搜索路径（profiles/prompts）：代码根在前（默认值），
    数据根在后（用户覆盖）。同名文件先到先得——数据根的覆盖生效。

    skills 用 skills_dirs()（overlay 语义同构但合并方式不同——skill 是
    目录级合并，这里按文件级覆盖）。
    """
    dirs = [ROOT / name, CODE_ROOT / name]     # 数据根优先（用户覆盖）
    seen, out = set(), []
    for d in dirs:                              # 去重（测试 HOME=CODE_ROOT 时同路径）
        if d.exists() and str(d) not in seen:
            seen.add(str(d)); out.append(d)
    return out


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
