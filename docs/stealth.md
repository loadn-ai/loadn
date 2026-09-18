# CC-Fingerprint 校准与运维（GLM 通道伪装层）

## 现状

**2026-09-17 已完成真机校准**（本机 CC 2.1.183 → 本地捕获网关，零 token），
并做了逐字段 diff 验证（headers 11 项 + body 15 项全对齐）。校准方法沉淀在
下方（CC 升级后重跑一遍即可）。

### 校准快照（CC 2.1.183，2026-09-17）

- UA：`claude-cli/2.1.183 (external, sdk-ts, agent-sdk/0.3.263)`
- beta：6 个 flag（claude-code-20250219 / interleaved-thinking /
  context-management / prompt-caching-scope / mid-conversation-system / effort）
- x-stainless：package-version 0.94.0、runtime-version **v24.3.0**（CC 自带
  node，非系统 node）
- URL：`/v1/messages?beta=true`；只发 Bearer 无 x-api-key；`x-app: cli`
- body：`metadata.user_id`=JSON(device_id 64hex/account_uuid ""/session_id
  uuid)、`thinking:{type:adaptive}`、`output_config:{effort}`、
  `context_management.edits=[clear_thinking_20251015 keep all]`、
  max_tokens 32000、无 temperature
- system 三块：billing 头块（无缓存标记）+ "You are a Claude agent, built on
  Anthropic's Claude Agent SDK."（ephemeral）+ 主 prompt（ephemeral）
- 工具面：真 CC 无 Grep/Glob/MultiEdit/TodoWrite（Agent 替代搜索）——
  hahaness 保留这些工具（已知形状差异：砍掉会瘫痪检索能力；工具集合是
  弱信号，headers/metadata/system 才是硬指纹）

## 校准流程（复刻 2026-09-17 的做法，零 token）

**不要**对 GLM 抓包——本地起捕获网关，真 CC 打本地（30 行 http.server，
记 headers+body、回最小 SSE），两头 diff：

1. 起 `python3 server.py 18923`（捕获网关，脚本形态见本节末注）
2. 真 CC 指向本地（`--settings` 优先级最高，不会被 settings.json 带偏）：
   ```bash
   echo '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:18923","ANTHROPIC_AUTH_TOKEN":"sk-fake"}}' > /tmp/ovr.json
   claude -p --settings /tmp/ovr.json --model glm-5.3 "回复一个字"
   ```
3. 回填 `CC_PROFILE`（对照捕获 JSON）：
   - `CC_VERSION`/`CC_BUILD` ← UA 与 billing 块；`AGENT_SDK_VERSION` ← UA 尾段
   - `SDK_VERSION` ← `x-stainless-package-version`；`NODE_VERSION` ← runtime-version
   - `BETA_FLAGS` ← `anthropic-beta` 全串；metadata/thinking/output_config/
     context_management/system 块结构变化 → 同步 `cc_request_body` 与
     anthropic `_body` 三块组装
4. **diff 验证**：hahaness（`HAHANESS_STEALTH=cc-all` + `HAHANESS_BASE_URL`
   指捕获服务）跑一 turn，与真 CC 捕获逐字段对比
5. 跑 `pytest tests/test_fingerprint.py` 全绿

> 注：捕获网关脚本在校准时位于 /tmp/cc_capture/server.py（未入库，按上方
> 描述 30 行可重写：ThreadingHTTPServer 记 POST 的 path/headers/body，
> 对 stream 请求回最小合法 SSE）。

## 版本跟追（月级）

- [cchistory](https://cchistory.mariozechner.at) 出新版本记录时 diff 一次特征
- UA 大版本跳变（2.x→3.x）**必须跟**，小版本差 1-2 个可接受

## 上线检查单

- [ ] 独立 GLM 账号已购 plan（不挂主力资产）
- [x] 校准完成（2026-09-17，CC 2.1.183，逐字段 diff 全对齐）
- [ ] `HAHANESS_STEALTH=cc` 仅 GLM 通道启用（`cc-all` 只用于测试）
- [ ] 并发 ≤3 会话；工作日 15:00-18:00 高峰错峰
- [ ] API fallback 通道演练过一次切换
- [ ] workdaddy 侧：systemd unit `Environment=HAHANESS_STEALTH=cc`（spawn env
      继承）或 engines 配置注入

## 已知边界（行为纪律 > 伪装）

- 摘要/planner 辅助请求：headers/UA/身份前缀同过伪装层 ✓；metadata 后缀用裸
  user_id（无 session 段）——真 CC 不发这类请求，形态差异无法完全消除
- 子代理 provider 实例未注入 `_stealth_sid`（裸 user_id，稳定）
- 请求节奏/并发/时序是伪装不了的——纪律约束（并发 ≤3、429 退避拉长）才是
  生存主力
- ToS 灰色地带，封号风险由独立账号承担；本层只降低客户端指纹差异
