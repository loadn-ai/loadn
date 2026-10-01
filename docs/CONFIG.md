# 配置参考（config.yaml）

> 数据根：`$LOADN_WEBUI_HOME/config.yaml`（默认 `~/.loadn-data/`）。
> 原则：**不存在的键用代码默认值**——升级不破坏既有配置；完整可运行样例见
> 仓库根 `config.yaml.example`。安全段全部 fail-closed：枚举笔误/非法值
> **拒绝启动**而非静默降级（`loadn_webui/config.py` 加载期校验）。

## 顶层段一览

| 段 | 用途 | 关键键 |
|---|---|---|
| `engines` | 可插拔执行引擎 | `default`（claude/hahaness/opencode）、`no_compact` |
| `claude` | claude 引擎参数 | `effort`/`model`/`claude_bin` |
| `run` | 并发与事件 | `max_concurrent_turns`、`replay_max_events`、`events_retain_days` |
| `server` | 监听与认证 | `host`/`port`/`token`（非 127.0.0.1 强制 token；**`token` 留空=整站免认证（含管理面）**——仅限已有外层防护的部署：仅本机访问，或反代层已有 basic auth 等；此时前端不再弹 token 门） |
| `mcp` | 全局 MCP servers | `servers`（.mcp.json 的 mcpServers 格式） |
| `resources` | 外部资源接入 | OCR/沙箱/CDP/代理/搜索/邮箱等端点与密钥 |
| `notify` | 运维通知 | bark/serverchan/telegram + 事件开关 |
| `share` | 产物分享 | `base_url`（空=未启用） |
| `pricing` | 成本页价目 | `api`/`plan_credits`（覆盖即整表替换）、`usd_cny` |
| `security` | 安全平面（下节详表） | — |

## security 段（安全平面全量键）

> v0.6.5 起**全量显式可调**，追加语义 fail-closed：内置防护表永不因配置
> 清空。**宪法红线不可配置**：审计链（零开关）、审批确认码门本体、信任门、
> SSRF 私网段、skills 供应链锁、bash 解析失败=block。

### 执行沙箱

以上安全运维七键（sandbox/codemode_enabled/lsp_enabled/shared_readonly/
resource_bridges/approval_ttl_s/approval_enforce/egress_proxy_port）另有
WebUI 写入口：安全中心 `PUT /api/admin/security/ops`（局部键语义，与
本表 yaml 写同效——sandbox 档位重启生效，其余热生效）。

| 键 | 默认 | 说明 |
|---|---|---|
| `sandbox` | `off` | 档位枚举（非法值拒启）：`off` 直跑（诚实标注未隔离）/ `bwrap` Linux 原生档（探测 bwrap+user namespace，不可用 fail-closed 降 off+审计）/ `vm-bwrap` 桌面 VM 执行域（执行语义=bwrap）/ `seatbelt`·`appcontainer`·`remote` 占位档（降 off+审计） |
| `shared_readonly` | `[]` | 跨项目只读共享根（绝对路径列表）：ro-bind 同路径进沙箱 + `$LOADN_SHARED_RO` 指路；写权限永不开放 |
| `resource_bridges` | `[]` | 宿主机资源桥接（显式授权面）：每项 `{path: 绝对路径, mode: ro\|rw\|dev}`——ro/rw=目录或文件 bind，dev=设备节点（GPU render node 等）；rw=任务可写宿主该路径 |

### 出口管控（egress）

| 键 | 默认 | 说明 |
|---|---|---|
| `egress_mode` | `enforce` | 三态：`off`=全局放开（代理仍在路径上直通+审计，凭证仍走网关）/ `warn`=放行记告警 / `enforce`=白名单强制。**会话级覆盖链**：session `params.egress` > 此全局值 |
| `egress_on_deny` | `ask` | enforce 下未列域处理：`deny`=直接 403 / `ask`=自动弹审批卡（SSE 推送+挂起等裁决，批准即热放行） |
| `egress_ask_wait_s` | `120` | ask 挂起等待上限秒（**15-600**；超时/拒绝→403 带理由与 hint） |
| `egress_allow` | 出厂白名单¹ | 后缀匹配（`export.arxiv.org` 类子域一并覆盖）；支持 `config.yaml` 热重载免重启 |
| `egress_proxy_port` | `0` | 出口代理端口（0=随机绑定 lifespan 回写；生产可固定 8793） |
| `egress_grant_ttl_s` | `7200` | 审批式临时放行默认 TTL 秒（**300-86400**；钳制域在 egress_grants 常量） |

¹ 出厂含 api.anthropic.com / open.bigmodel.cn / pypi.org / registry.npmjs.org /
github.com / arxiv.org / 2captcha.com / opencode.ai / llm-gw.internal 等

### 权限与审批

| 键 | 默认 | 说明 |
|---|---|---|
| `hard_blocklist_l0` | `[]` | L0 红线追加正则（文本+AST 双拦；坏正则拒启）——**追加**不清空内置表 |
| `l0_extra_cmd_names` | `[]` | L0 追加危险命令名 |
| `net_cmds` | `["curl","wget"]` | 网络类命令白名单（收窄=漏拦其他下载器，放宽自担） |
| `glob_warn_patterns` | `[]` | glob 兜底 warn 形态追加（只 warn 不拦） |
| `sensitive_path_patterns` / `sensitive_abs_paths` | `[]` | 敏感路径黑名单追加（内置 vault/db/audit/.ssh/.aws 之外；Write/Edit/Bash 命中即拦） |
| `irreversible_tools` | 六类² | 不可逆工具确认码门（先网关告警） |
| `approval_enforce` | `enforce` | 确认码门终态（`warn` 仅迁移期） |
| `approval_ttl_s` | `600` | 审批卡 TTL 秒（**60-86400**；码一次性+过期 fail-closed） |

² mail/sms/wechat/pay/account_write/browser_export

### 检测与旁挂能力

| 键 | 默认 | 说明 |
|---|---|---|
| `canary_enabled` | `true` | 蜜罐布放+外渗检测（关=失去检测面，物理层仍在 egress enforce） |
| `ssrf_deny_hosts` | 平台通道域³ | 宿主中介抓取的禁入域（私网/回环/链路本地 IP 段**无条件**拦截，这里只补平台侧通道域） |
| `codemode_enabled` | `false` | P3-4 受限执行域（显式选择开启） |
| `lsp_enabled` | `false` | P3-3 LSP 诊断回注（懒启动语言 server 是显式选择） |

³ llm-gw.internal 等

## 环境变量

| 变量 | 作用 |
|---|---|
| `LOADN_WEBUI_HOME` | 平台数据根（config.yaml/db/vault/audit/workspace） |
| `LOADN_HOME` | 引擎数据根（sessions/transcript/memory） |
| `LOADN_SKILLS_EXTRA` | skill overlay 目录（私有 skill） |
| `LOADN_PROVIDER` | 引擎侧 provider 选择（`fake`=零 token 测试） |
| `LOADN_CLAUDE_BIN` | claude CLI 路径覆盖 |
| `LOADN_STEER_FILE` | 运行中插话队列文件（宿主写入，引擎轮询） |

## 会话级参数覆盖（params）

`params.egress`（off/warn/enforce）、`params.sandbox`（off/bwrap/…）等
八个键支持**会话级覆盖**（PATCH `/api/sessions/{sid}` 的 `params` 段），
优先级高于 `config.yaml` 全局值——用于单任务放开/收紧而不动全局；下一轮
生效，进行中 turn 不受影响。

### 宿主接管配方（运维任务 / 跨项目资源访问）

让某个会话「全面接管宿主机」的三件套（属性面板均可调）：

1. **`params.sandbox: off`**——本会话引擎直跑宿主（文件系统全可见，
   systemctl/journalctl 等运维命令可用）；权限引擎/审计/蜜罐仍然生效，
   只是去掉文件隔离。普通会话保持全局档位不受影响。
2. **`resource_bridges`**（全局，安全运维面可加）——比 off 更克制的
   选择：保持 bwrap 隔离，只把指定宿主路径同路径 bind 进沙箱：
   `{path: /data/code/other-project, mode: rw}`（rw=可写）、ro=只读、
   dev=设备节点。读写面精确到路径。
3. **`params.egress: off` + 白名单**——运维要访问本机服务/内网时放开
   本会话外联（代理仍在路径上：直通+审计+凭证网关不变）。
