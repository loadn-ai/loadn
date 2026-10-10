# 扩展与定制指南

> loadn 的定制面全部是**数据/声明式优先**：能用配置和内容文件解决的不写代码；
> 要写代码的每一条缝都有稳定契约。按「想改什么」查表。

| 想定制什么 | 途径 | 章节 |
|---|---|---|
| 换/加编码引擎 | entry point 包 | §1 |
| 换 LLM 供应商 | 引擎 config.json / env | §2 |
| 给 AI 加技能 | SKILL.md | §3 |
| 接外部工具服务 | MCP server | §4 |
| 拦截/审计工具调用 | hooks | §5 |
| 角色档案 | profiles/*.yaml | §6 |
| 新会话的工作区模板 | prompts/ | §7 |
| 端口/沙箱/白名单/通知 | config.yaml | §8 |
| **代码级扩展（工具/事件/provider）** | **loadn.ext 协议** | **§9** |

## 1. 加一个引擎（平台侧最大的扩展缝）

平台的引擎层是插件化的：任何「吃 argv、吐 Claude Code stream-json 方言
NDJSON」的可执行文件都能注册为引擎。两种挂法：

**a) 代码内建**（本仓库）：在 `loadn_webui/engines/` 写一个 `EngineSpec`
子类（见 `base.py` 的三件套：`build_argv` / `adapter()` / 能力位），在
`engines/__init__.py` 的 `ENGINES` 注册。

**b) 外部包挂载（推荐第三方用）**——entry point，零侵入：

```toml
# 你的包 myagent/pyproject.toml
[project.entry-points."loadn.webui.engines"]
myagent = "myagent.loadn_spec:SPEC"     # EngineSpec 实例或零参 callable
```

`pip install myagent` 后平台启动即自动挂载（加载失败只告警不炸平台）。
接口要点：

- `resolve_bin() -> str`：可执行文件定位（做健康探测）
- `build_argv(call) -> (cmd, env_extra)`：把一次 turn（prompt/session/
  resume/model…）翻成 argv 与注入环境
- `adapter()`：事件归一化器。原生 stream-json 用恒等基类；异构输出
  子类化 `EventAdapter` 累积状态转换（参考 opencode.py）
- 能力位：`supports_transcript` / `max_turns_flag` / `session_id_domain`
  等声明式差异，平台按位适配而非 if-else 引擎名
- 沙箱：新引擎默认直跑；要进 bwrap 需在 `sandbox.py wrap_engine` 补
  挂载矩阵（该引擎运行时依赖哪些目录），安全语义见 ARCHITECTURE §安全栈

契约细节（事件形状/退出码/心跳/会话锁语义）以 docs/PROTOCOL.md v1 为准，
tests/contract/ 有对赌用例——第三方引擎建议在自己仓库里跑这套契约。

## 2. 换 LLM 供应商（引擎侧）

不动代码。解析序（`loadn` 引擎，P3-5b 起含 auth.json 层）：

1. `$LOADN_HOME/config.json`（部署显式真源，最高优）
2. `$LOADN_HOME/auth.json`（`loadn auth login` 的凭据库——0600；
   同名 provider 条目生效，支持 `--base-url` 订阅型网关）
3. env：`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `LOADN_MODEL`
   / `LOADN_PROVIDER`（`anthropic` | `openai` | `fake` | loadn.ext 注册名）
4. `~/.claude/settings.json` 的 env 段（已有网关配置原样复用）

凭据管理子命令：`loadn auth login|refresh|logout|status|check
[--credentials]`——token 经 getpass 输入（不进 shell 历史），只落
auth.json（0600，挂载面零命中）+ 平台 vault 加密同步（装了 loadn-web
即生效）；`check --credentials` 导出 JSON 供脚本/CI。

Anthropic 形网关（任何兼容端点）与 OpenAI 兼容端点（vLLM/LiteLLM/…）
都原生支持；`fake` provider 用于零 token 测试。加全新协议族则在
`loadn/providers/` 加一个文件（参考 `openai.py` 的适配层写法）。

## 3. 写一个 Skill（最常用的扩展）

Skill = 目录 + `SKILL.md`（frontmatter `name` + `description`，正文是
给 AI 的操作手册；缺字段自动回落——name→目录名、description→空串，
兼容 [agentskills.io](https://agentskills.io) 开放标准的 frontmatter
形状，`allowed-tools`/`metadata` 等额外字段忽略不炸）。放置位置任一：

- 项目内：`.agents/skills/`（agentskills.io 标准目录，`npx skills add`
  等工具的落点，同名优先级最高）> `.claude/skills/` > `.loadn/skills/`
  / `.agent/skills/`（随仓库走；项目级四根都过 P0-2 信任门）
- 用户级：`~/.claude/skills`（与 Claude CLI 共享）/ `$LOADN_HOME/skills`
  （引擎侧全局，不受信任门）
- 平台级：数据根 `skills/`（webui 管理中心可视化管理、可安装/上传）

私有 skill 不进仓库：设 `LOADN_SKILLS_EXTRA=/path/to/dir` 即整目录
overlay 进平台（开源部署与私有资产分离的设计）。

安装第三方 skill 走管理中心（三来源：GitHub repo/tree URL 或
`owner/repo/path` 简写、任意 `https://….zip` / `.tar.gz` 归档 URL、
zip 上传）——先过供应链扫描（W4 八类检查），能力声明会展示给用户。
GitHub/URL 两类远程来源装完即 pin：`SKILL.md` 盖 `source` 戳并写入
`$LOADN_HOME/skills.lock.json` 供应链锁，此后文件被 out-of-band 篡改
→ 引擎拒索引（fail-closed）；经管理面编辑/重装则自动刷新锁。上传的
zip 视作用户自备，不 pin。管理中心可把任一 skill 导出为 agentskills.io
兼容 zip（frontmatter 规范化、剥内部元数据），供其他 agent 使用。

### 用户教学自动建议（P12：经验→技能固化闭环）

会话中用户说出显式教学（「以后都/记住要…」）或否定纠错（「不对/重来…」）
句式时，引擎 turn 结束后写 `.loadn/skill-suggest.json`（单槽 pending），
前端聊天页顶部弹出建议卡。三向决策：

- **固化为技能**：预填技能名/简介/正文（用户原话+上轮行为摘要，可编辑），
  写入 `.agents/skills/<name>/SKILL.md` 并同步平台技能库——先过供应链
  扫描，红线拒写；此后该工作区每个新会话自动加载。
- **拒绝**：记负样本（同类指纹 7 天内不再提示）。
- **这不是教导**：误判通道（如系统拼接文本被词表误命中）——仅清除本次
  建议，不记抑制。

防自激：每会话最多触发 2 次；会话轮换恢复协议段（context_inflation 交接
文本）在检测前剥离；压缩后反思（`LOADN_REFLECT_AFTER_COMPACT=on`）产物
进记忆域 draft 待人工转正，与本通道互不触发。

## 4. 接外部工具服务（MCP）

项目根 `.mcp.json` 声明 stdio server，工具以 `mcp__<server>__<tool>`
出现在引擎工具面。首次使用按哈希锁定（防替换）。平台侧的外部资源
（OCR/浏览器沙箱/短信/邮箱…）在 config.yaml `resources:` 配端点与密钥
——密钥只进 config.yaml，永不入库。

**工具懒加载（P2）**：单 server 工具数超过阈值（`MCP_LAZY_TOOL_THRESHOLD`
=15，env `LOADN_MCP_LAZY_TOOL_THRESHOLD` 覆盖，0=关）时不全量注入工具面
——大 server 单家可吃十几万 token。改为只注册 `ToolSearch` 工具：其参数
enum 即索引（name + description 首句 + 来源 server），模型点名后当场物化
并返回完整 inputSchema（一次往返），同轮即可正确构造调用；直接调用索引
内的工具也会被 loop 当场物化执行。延迟索引持有全量 spec（连接与会话同
寿命 = 会话内 schema 缓存）；transcript 的 result 事件带可选
`mcp_deferred` 字段（延迟工具数与估算 token）供观测。引擎内部直用的
工具（如 `mcp__lsp__diagnostics`）永不延迟。

## 4b. Webhook 事件触发（P3）

外部事件（PR / 支付回调 / 表单提交）→ agent 会话：管理中心「Webhooks」
tab 建钩（name / profile / prompt 模板 / 限流 / IP 白名单），得到
`POST /hooks/{token}` 触发地址——token 即凭证（20 hex 高熵，删行即吊销）。
payload 是**不可信事件数据**：仅 `{{payload}}` 字面替换（禁求值）、≤64KB
超限截断标注、整体作为用户消息投递（带来源标注包装，不解析为系统操作）。
响应 `202 + {session_id, run_id}`，`GET /hooks/{token}/runs/{run_id}` 轮询
结果。命中/拒绝全部入审计账本（哈希链）。默认限流 6/min（per-hook）。

部署注意：公开触发端点在 `/api` 外（W0 认证不护，token 即凭证），但
**host_guard 照守全部路径**——外网源须走已配域名（`share.base_url`）或在
`server.extra_hosts` 加白；IP 白名单匹配 `client.host`，不信任
X-Forwarded-For（fail-closed）。签名校验（HMAC）为后续卡。

## 5. Hooks：拦截与审计工具调用

项目 `.agent/settings.json` 的 `hooks` 段挂外部命令：stdin 收
`{tool_name, tool_input}` JSON，**exit 2 = 阻断**。平台内置
`policy-check --hook` 执行体（L0 红线/AST 出口域），可叠加自己的。

## 6. 角色档案（profiles/*.yaml）

每个档案 = 一类用户的预设：`engine`（用哪个引擎）、`skills`（默认挂载
哪些技能）、模型与参数。平台按档案渲染工作区与侧栏；会话可随时切
引擎（不绑死在档案上）。

## 7. 会话工作区模板（prompts/）

新会话的工作区骨架（CLAUDE.md/AGENTS.md/…）。平台的「宪法链」= 从
git 根到 cwd 的 CLAUDE.md/AGENTS.md 就近叠加 + 用户级 AGENT.md。

## 8. config.yaml 参考入口

数据根 `config.yaml` 分节：`server`（端口/token/域名）、`engines`
（默认引擎）、`security`（sandbox / approval_enforce / egress_mode /
egress_allow 白名单 / shared_readonly）、`resources`（外部资源端点与
密钥）、`notify`（bark/Server酱/Telegram 运维通知）、`pricing`（成本页
价目覆盖）。字段语义见 `loadn_webui/config.py` 各 dataclass 的注释
（默认值均为通用值；个人部署的私有端点写在自己的 config.yaml 里，
不回传上游）。

**放行策略速查**（两道门已同面——命令级 hook 与网络级 proxy 消费同一
策略；改完全部热生效，活跃会话快照自动刷新）：

| 想放什么 | 操作 | 作用域 |
|---|---|---|
| 单个域名 | 安全中心「数据流向」一键放行（或 yaml `egress_allow` 追加） | 全局 |
| 整档放开 | `PUT /api/admin/egress/policy` `mode: warn|off`（warn=记事件放行；off=全放） | 全局 |
| 只放开一个任务 | 会话属性面板 params `egress: off|warn` | 单会话 |
| 临时授权 | agent 发起审批（限时+审计+到期自动收回） | 单会话限时 |

白名单条目手编容错：`https://x.com:8443/api`、`*.cdn.x.com`、大写、
尾点都会归一化后再匹配（原文保留在 yaml，不回写）。

**跨项目数据共享与宿主机资源桥接**（`security.shared_readonly` +
`security.resource_bridges`）：

```yaml
security:
  shared_readonly:            # 只读数据共享（ro 特例，兼容面）
    - /data/papers
  resource_bridges:           # 宿主资源桥（显式授权面，默认空）
    - path: /mnt/datasets/common
      mode: ro                # ro=只读 bind | rw=可写授权 | dev=设备节点
    - path: /dev/dri          # GPU render nodes（--dev-bind）
      mode: dev
```

- bwrap 档：ro/rw 同路径 bind、dev 设备节点 `--dev-bind` 挂进沙箱；
  同路径 bridge 语义优先于 shared_readonly
- env 指路：`$LOADN_SHARED_RO`（ro 子集）+ `$LOADN_HOST_BRIDGES`
  （全量 `path:mode`）——agent 知道哪些宿主资源被显式授权可用
- 同项目内共享继续用项目根 `inputs/`（rw，`$LOADN_INPUTS` 指路）

**安全机制显式配置面**（v0.6.5 起全部可在 config.yaml `security:` 调节；
**追加语义 fail-closed**——内置防护表永不因配置清空，笔误拒绝启动）：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `hard_blocklist_l0` | `[]` | L0 红线**追加**正则（坏正则拒启动） |
| `l0_extra_cmd_names` | `[]` | L0 红线追加命令名 |
| `net_cmds` | `[curl, wget]` | 命令级网络门管哪些下载器（收紧自担漏拦） |
| `glob_warn_patterns` | `[]` | glob 兜底 warn 形态追加 |
| `sensitive_path_patterns` / `sensitive_abs_paths` | `[]` | 敏感路径黑名单追加 |
| `canary_enabled` | `true` | 蜜罐布放+外渗检测开关 |
| `approval_enforce` | `enforce` | enforce \| warn（枚举校验拒笔误） |
| `approval_ttl_s` | `600` | 审批卡 TTL（60-86400） |
| `codemode_enabled` / `lsp_enabled` | `false` | 受限执行域 / LSP 诊断 |
| `sandbox` / `shared_readonly` / `resource_bridges` | `off` / `[]` / `[]` | 沙箱档位 / 共享 / 桥接 |

**不可配置项**（宪法红线）：审计链（零开关）、审批确认码门本体
（码/hash/single-use/TTL 校验）、项目信任门、SSRF 私网段、skills 供应链
锁、bash 解析失败=block——这些机制的强度不因配置降低。

## 9. 代码级扩展：`loadn.ext` 协议（P3-5）

上面八条缝都是数据/声明式；要写代码的扩展收敛为**一个入口**——
扩展 = 一个 Python 模块，暴露 `load(ext)`，`ext` 面三个方法：

```python
# my_ext.py —— 三个方法按需用，全可选
def load(ext):
    # ① 事件订阅（closed 事件集：PreToolUse/PostToolUse/Stop/
    #    SessionStart/SessionEnd/TurnEnd——与 hooks 同面）
    ext.on("PreToolUse", lambda p: (
        {"decision": "block", "reason": "…"}     # 阻断（回填给模型）
        if p["tool"] == "Bash" and "rm -rf /" in p["input"].get("command", "")
        else None))                              # None=不干预
    # handler 也可返回 {"input": {...}} / {"output": "…"} 改写
    #（PreToolUse 改入参 / PostToolUse 改输出——与外部命令钩子的
    # stdout JSON 完全同语义）；async handler 原生支持。

    # ② 注册工具（Tool 实例：name/description/input_schema/execute）
    ext.register_tool(MyTool())
    # 与内置工具**同名 = 整体替换**（不改编擎代码改内置行为，
    # 替换有 log 留痕）——见 examples/replace_grep.py

    # ③ 注册 provider 工厂（factory(cfg) -> Provider）
    ext.register_provider("mine", lambda cfg: MyProvider(cfg))
    # 生效：env LOADN_PROVIDER=mine 或 config.json 的 provider 字段
```

**放置位置**（信任面同 hooks——这是任意代码执行面）：
- 全局：`$LOADN_HOME/extensions/*.py`
- 项目级：`.loadn/extensions/*.py`（P0-2 信任门——clone 的仓库不得自带；
  未过门整目录跳过并告警）
- overlay：env `LOADN_EXT_EXTRA`（os.pathsep 分隔多目录；私有扩展与
  开源部署分离，同 `LOADN_SKILLS_EXTRA` 哲学）

加载顺序 = 全局 < 项目级 < overlay（后加载同名工具覆盖先加载）。
单个扩展加载失败只告警不炸会话（同 MCP discover 纪律）。

**首批示例 10 个**（`examples/`，全部可运行、CI 冒烟
`tests/test_ext.py::test_all_examples_load_and_smoke`）：
hello_tool（新工具）、replace_grep（同名替换内置）、permission_gate
（PreToolUse block）、audit_log（PostToolUse JSONL 留痕）、
block_secret_write（私钥落盘门）、uppercase_read（输出改写）、
turn_notifier（Stop 观察）、todo_guard（入参校验）、custom_provider
（provider 工厂）、git_checkpoint（写路径检查点）。

## 贡献回上游

改动协议/安全面的 PR 请先读 docs/PROTOCOL.md 与 docs/ATTACK_SURFACE.md
并过 tests/contract + tests/security。流程见 CONTRIBUTING.md。
