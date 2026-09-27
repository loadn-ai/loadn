# Anthropic 形网关线格式兼容层（wire-compat profile）

> 原名「伪装层/stealth」——2026-09-28 开源改写为中性文档：本层的技术事实
> 是**客户端请求指纹兼容**（部分 Anthropic 形网关按请求头/metadata 校验
> 客户端形态，不匹配的请求可能被拒或降级）。文档只描述兼容性工程，
> 不含任何绕过服务方限制的运营建议——使用本层时遵守你与网关服务方
> 的协议是使用者的责任。

## 这是什么

loadn 是独立实现的客户端，不是官方 SDK。部分 Anthropic 形网关（尤其
聚合型网关）会校验请求的客户端指纹（UA/`anthropic-beta` flags/
`x-stainless-*`/metadata 结构）——非官方形态的请求可能被拒绝或进入
降级路径。本层让 loadn 的请求在线格式上与官方 Claude Code CLI 对齐，
属于**协议兼容工程**，与 egress 网关的凭证注入（docs/ARCHITECTURE W5）
配合使用。

开关：`LOADN_STEALTH=cc`（env）或 config.json `extra.stealth`——
`fingerprint.py` 的 `active()` 解析；`cc` 对齐请求头与 metadata，
`cc-all` 仅用于测试（含 system 块身份行）。不开启则发 loadn 原生形态。

## 指纹档案（CC_PROFILE，2026-09-17 真机校准快照）

对齐目标：Claude Code CLI 2.1.183。字段以官方客户端公开可见的请求
形态为准（UA 版本串、beta flag 集、SDK 版本号、metadata JSON 结构、
thinking/output_config/context_management 块）。

**已知形状差异**（刻意保留）：loadn 的工具集含 Grep/Glob/MultiEdit/
TodoWrite（官方 Agent 形态用内部搜索替代）——砍掉会瘫痪引擎检索能力；
工具集合是弱信号，请求头/metadata 才是校验重点。

## 校准方法（零 token，本地捕获对比）

不对任何网关抓包——本地起捕获服务，官方 CC 打本地，两头 diff：

1. 30 行 `http.server` 捕获服务（记 POST path/headers/body，对 stream
   回最小合法 SSE）
2. 官方 CC 指向本地（`--settings` 优先级最高）：
   ```bash
   echo '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:18923","ANTHROPIC_AUTH_TOKEN":"sk-fake"}}' > /tmp/ovr.json
   claude -p --settings /tmp/ovr.json --model glm-5.3 "回复一个字"
   ```
3. 回填 `loadn/providers/fingerprint.py` 的 `CC_PROFILE`（版本串←UA、
   SDK 版本←x-stainless、beta flags 全串、metadata/body 块结构）
4. loadn 指向同一捕获服务跑一 turn，与官方捕获**逐字段 diff**
5. `pytest tests/test_fingerprint.py` 全绿

## 版本跟追

官方客户端大版本跳变（2.x→3.x）时重跑一次校准；小版本差 1-2 可接受。
beta flags 与 metadata 结构是最常变的字段。

## 已知边界

- 摘要/planner 辅助请求走同兼容层；metadata 后缀为裸 user_id（官方
  不发这类请求，形态差异无法完全消除）
- 子代理 provider 实例未注入会话后缀（裸 user_id，稳定）
- 请求节奏/并发是行为层，不在本层范围——对网关的并发与退避纪律由
  egress 层与 `run.max_concurrent_turns` 承担
