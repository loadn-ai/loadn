# PROTOCOL v1 — loadn 引擎 ↔ loadn webui 唯一接口

本文档是引擎（`loadn` 包）与平台（`loadn_webui` 包）之间**唯一**的接口契约。
两个包同仓不同进程：webui 永远以子进程 + stream-json 方式驱动引擎，
`loadn_webui` 代码禁止 `import loadn.core`（CI 架构测试强制，tests/test_architecture.py）。

版本：**v1**（2026-09-22，如实固化现行行为，无破坏性变更）。
改契约 = 独立 PR + 本文件 bump + `tests/contract/` 双侧测试同步。

---

## 1. argv 方言

```
loadn -p --verbose --output-format stream-json \
  [--session-id <uuid4> | --resume <uuid4>] \
  [--model M] [--effort E] [--max-turns N] [--no-compact] \
  [--disallowedTools T]... \
  PROMPT
```

- `PROMPT` 恒为 `argv[-1]`——**webui 侧调用纪律**（`engines/loadn.py build_argv` 强制）；
  引擎侧 argparse 现状允许 flag 后置，契约测试锁调用侧形态。
- stream-json 输出必须伴 `-p`（无头模式），否则引擎 exit 2。
- `--dangerously-skip-permissions --permission-mode bypassPermissions`：执行面
  权限由平台策略层（W1 policy，模型之外）接管，引擎侧全放行。
- `--no-compact`：双重压缩协调——webui 侧外层轮换（profile
  `rotate_input_tokens`）已设时传，禁引擎内压；`engines.no_compact` 显式
  覆盖两头。**loadn 特有**，claude CLI 无此旗标。
- `--disallowedTools <name>`（可重复，claude CLI 同名同义）：工具黑名单
  四层同拒——内建/MCP 工具面不注入、ToolSearch 延迟索引不广告、物化即拒
  （带改道路标）、调用时权限 deny 兜底（bypass 不豁免）。webui 侧真源
  `TurnCall.disallowed_tools`（profile 禁用 + off 档执行域门合流）；缺省
  不传（argv 面零噪声）。

## 2. 事件流（stdout，NDJSON 每行一 JSON）

| 事件 | 硬/软 | 契约 |
|---|---|---|
| `system` (subtype=init/heartbeat) | 软 | init 携 session_id/model/tools（tools=**spawn 时点快照**：MCP 懒加载（§8 env）运行中物化的工具不补录进该列表） |
| `assistant` | 软 | message.content blocks：text / tool_use / thinking |
| `user` | 软 | tool_result 回填 |
| `stream_event` | 软 | 仅 `--verbose` 下逐 delta |
| `steer` | 软 | 插话消费回执（§5） |
| `plan` / `todos` | 软 | loadn 特有 |
| **`result`** | **硬** | 必含 `usage` / `modelUsage`（每模型 costUSD）/ `num_turns` / `total_cost_usd` / `subtype` / `session_id` |

判死兜底信号之一：15s 内 exit 1 且无 usage → webui 判 fresh 会话秒拒。

### 2.1 Task 工具卡与子代理归属（v1.1 增补，纯增量）

Task 工具（SubagentManager）在 `assistant`/`user` 事件里以三段形状外发：

| 阶段 | 事件/块 | 形状 |
|---|---|---|
| 开始卡 | `assistant` → `tool_use` | `id="sub_N"`（N=回合内序号）、`name="Task"`、`input={prompt≤300, subagent_type, description, agent_name}` |
| 子活动转发 | `assistant` → `tool_use` / `user` → `tool_result` | 块名改 `子N·<原工具名>`，id 保持子代理内部 id（结果回填按原 id 配对） |
| 结束卡 | `user` → `tool_result` | `tool_use_id="sub_N"`、`content=text≤2000`、`is_error=(subtype≠success)` |

- `agent_name`（v1.1 起）：引擎从 `AGENT_NAME_POOL`（小说人物人名池，
  `loadn/constants.py`）确定性轮转取名，回合内撞名追加 `·N` 后缀——供宿主
  UI 拟人展示。缺该键的旧流按 `子N` 回退展示。
- `子N·` 前缀与 `sub_N` id 体系是归属配对的唯一约定，**不随版本变更**。
- 宿主（webui）派生的 `agent_id`/`agent_n`/`agent_role` 等结构化字段不属
  本契约——宿主内部消费面，随宿主版本演进。

## 3. 会话语义

- session id 域：**UUIDv4**（webui 侧非法值换新 UUID 再传）。
- 首 turn `--session-id`，续 turn `--resume`。
- 冲突（同 id 活锁或 transcript 已存在）：错误文案**包含子串
  `"Session ID already in use"`**（实际为 `Session ID already in use: <sid>`，
  打 stderr + exit 1）——webui 按子串匹配（engines/base.py），
  据此触发 fresh→resume 翻转重试。**锁子串不锁全句。**
- 跨引擎绝不 resume：claude=uuid 域、opencode=`ses_…` 域，
  webui `_align_engine` 存档旧 id。

## 4. 能力位（webui 侧 EngineSpec，非引擎自报）

`supports_transcript / session_id_domain("uuid"|"any") / max_turns_flag /
effort_flag` 是 webui `engines/base.py` 的适配器知识。PROTOCOL 附表：

| 旗标 | claude | loadn | opencode |
|---|---|---|---|
| `-p --verbose` | ✓ | ✓ | ✗（`run --format json --auto`） |
| `--session-id/--resume` | ✓ | ✓ | `--session <id>`（resume 专用） |
| `--effort` | ✓ | ✓（别名 `--variant`） | `--variant`（白名单档位） |
| `--max-turns` | ✓ | ✓ | ✗（降级省略+警告） |
| `--no-compact` | ✗ | ✓ | ✗ |
| `--disallowedTools` | ✓ | ✓ | ✗（env 通道注入 OPENCODE 配置） |
| transcript 判死 | `~/.claude/projects/*/<uuid>.jsonl` glob | `~/.loadn/sessions/<id>/transcript.jsonl`（`LOADN_HOME` 可覆盖） | ✗（仅 stdout+硬超时） |

opencode 配置经 `OPENCODE_CONFIG_CONTENT` env 注入（方言差异详见
engines/opencode.py 适配器；NDJSON→stream-json 状态适配，result 恒在 finalize 合成）。

## 5. 插话 steer（v1 = env 通道，如实固化）

- env `LOADN_STEER_FILE`（旧 `HAHANESS_STEER_FILE` fallback 一版）显式指定
  NDJSON 文件路径——**不进 argv，引擎不猜 workspace 约定**。
- webui（engine.py）向该文件逐行 append `{"ts","text"}`；每个会话独立文件
  `<ws>/.steer.<sid>.jsonl`（共享工作区多任务不串台）。
- 引擎每轮 LLM 调用前增量轮询：构造时刻文件大小为 offset（启动前内容不
  重放）；尾部半行留待下轮。
- 每消费一条回发 `steer` 事件——webui 据此把该条从待回队列摘除。
- turn 结束仍未消费的插话：webui 按原话回队列为新 turn（不丢话）。
- **一致性铁律（契约测试锁定）**：spawn env 里的 steer 路径必须与 webui
  写入路径逐字节一致。已知历史风险：写侧查 DB workspace 列、读侧用
  spawn cwd 拼——沙箱/容器路径映射改变会静默断链。
- **v1.1 演进计划**：升格 `--steer-file` argv（env 兼容一版），随本文件 bump。

## 6. 判死兜底（v1 = 家目录约定探测）

- claude：`~/.claude/projects/*/<uuid>.jsonl` 的 mtime。
- loadn：`$LOADN_HOME/sessions/<uuid>/transcript.jsonl` 的 mtime。
- `--transcript-path` argv **现状不存在**（v1.1 演进候选：显式传出协商，
  摆脱家目录耦合）。
- 通用兜底：stdout 静默 + 硬超时（timeout_s）。

## 7. hooks 声明路径（W1 执行点 A）

| 引擎 | 声明位置 | 语义 |
|---|---|---|
| claude | `<ws>/.claude/settings.json` 的 `hooks` 键 | PreToolUse exit 2 = block（stderr 回模型）；**bypassPermissions 下仍生效**（W1-0 POC 实证，claude 2.1.183） |
| loadn | `<ws>/.loadn/settings.json`（旧 `.agent/settings.json` 读兜底一版） | 同上；执行序 permissions.check → PreToolUse → execute → PostToolUse |
| opencode | 无 hooks 面 | 执行点 B（CLI 网关）+ permission map deny 兜底 |

loadn 引擎 SessionStart/SessionEnd hooks **无触发点**（声明不支持直到补齐）。

## 8. 附：数据根与 env 速查

| env | 语义 | 默认 | 旧名 fallback |
|---|---|---|---|
| `LOADN_HOME` | 引擎数据根（sessions/transcript/session.db） | `~/.loadn`（首启从 `~/.agent` 自动搬迁） | `HAHANESS_HOME` |
| `LOADN_STEER_FILE` | 插话文件（§5） | —（缺省即功能关闭） | `HAHANESS_STEER_FILE` |
| `LOADN_PROVIDER` | provider 覆盖（fake/anthropic/…） | — | `HAHANESS_PROVIDER` |
| `LOADN_STEALTH` | GLM 通道 CC 伪装 | — | `HAHANESS_STEALTH` |
| `LOADN_WEBUI_HOME` | **平台**数据根（DB/workspace/config） | monorepo 根 | `WORKDADDY_HOME` |
| `LOADN_USER_MEMORY` | 用户级跨项目记忆域（P4：`_user/` 全局域，注入 `[user-memory]` 段；归属=第一人称偏好词表∧无项目指称） | `on` | — |
| `LOADN_MCP_LAZY_TOOL_THRESHOLD` | MCP 工具懒加载阈值：单 server 工具数超此值只注入 ToolSearch 索引（enum=name+首句@server），经其按需物化；`0`=关 | `15` | — |

两类 HOME 语义不同（引擎 vs 平台），spawn 传递时互不污染。

---

## 7. PROTOCOL v2（P2-3，增量事件 + 版本协商）

**启用**：`loadn --protocol v2`（默认 v1——旧宿主零感知，v2 是纯增量）。

### 7.1 事件清单（`loadn/protocol/manifest.json`，代码侧 `loadn.protocol`）

每事件三维声明（opencode event-manifest 同构）：
- `since`：引入版本（1 或 2）
- `durable`：终态后回放是否必须（对接 P2-2 选择性落盘——v1 仅 `result`
  durable；v2 的 `permission_request` durable=审计要求）
- `latest`：现行版本（v3 演进的弃用位）

### 7.2 v2 增量事件

| 事件 | 方向 | 契约 |
|---|---|---|
| `permission_request` | 引擎→宿主 | `{tool, input, reason, params_hash}`——ask/deny 语义上抛；`params_hash`=入参 sha256 前 16 位（审批规则化回写 P0-4 关联键）；**durable** |
| `permission_result` | 宿主→引擎 | stdin 注入 `{allow, rule_id?}`（rule_id 非空=已规则化，引擎后续同形调用不再上抛） |
| `tool_use_failure` | 引擎→宿主 | 工具执行失败终态（PostToolUseFailure 语义；v1 桥=user 的 tool_result is_error 形态照发） |

### 7.3 v1 桥（`manifest.v1_bridge`）

v2 事件在 v1 流的映射：`tool_use_failure`→`user`（既有回填行为不变）；
`permission_request/result`→`null`（v1 宿主不识，不双发——判死兜底
只认 `result`，不受影响）。

### 7.4 能力位演进

能力协商仍以 webui 侧 EngineSpec 为准（§4）；v2 的 `system.init` 追加
`protocol: 2` 字段（v1 无此字段——宿主按存在性探测）。

## 8. 传输：stdio 直连 / UDS daemon（P2-1）

**方言不动**：本节只换管道不改事件流——§2/§7 的事件字节流在两种传输下
逐字节一致（桥零改动的根基）。

- 直连（现状）：宿主 spawn `loadn -p --output-format stream-json`，事件流
  走 stdout。
- daemon：`loadn daemon` 监听 `$LOADN_HOME/var/engine.sock`（0600）；
  连接级 token 写 `var/engine.token`（0600）。attach 首行
  `{"auth": token, "session_id": sid}`；其后每行 `{"type":"run","prompt":…}`
  为一个 turn 请求，事件流回写该连接并镜像同会话其他连接。
- 鉴权：错/缺 token 立即断（`{"type":"error","error":"auth failed"}`）。
- 断连语义：连接断开→引擎收尾当前 turn 后挂起（AgentCore 存活，cache
  warmer 全量生效——P1-6 的长命进程前提）；重连同 sid 续作。
- 桥：`loadn daemon-bridge`（env `LOADN_ENGINE_SOCK`）——宿主 spawn 桥
  替代直接 spawn 引擎，视角仍是 stdin/stdout NDJSON 子进程（codex
  stdio-to-uds 同构）。
