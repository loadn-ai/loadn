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

## 2. 事件流（stdout，NDJSON 每行一 JSON）

| 事件 | 硬/软 | 契约 |
|---|---|---|
| `system` (subtype=init/heartbeat) | 软 | init 携 session_id/model/tools |
| `assistant` | 软 | message.content blocks：text / tool_use / thinking |
| `user` | 软 | tool_result 回填 |
| `stream_event` | 软 | 仅 `--verbose` 下逐 delta |
| `steer` | 软 | 插话消费回执（§5） |
| `plan` / `todos` | 软 | loadn 特有 |
| **`result`** | **硬** | 必含 `usage` / `modelUsage`（每模型 costUSD）/ `num_turns` / `total_cost_usd` / `subtype` / `session_id` |

判死兜底信号之一：15s 内 exit 1 且无 usage → webui 判 fresh 会话秒拒。

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
