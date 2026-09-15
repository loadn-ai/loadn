# Changelog

本项目的全部显著变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-09-16

首个公开版本。

### Added
- **核心循环**：AgentCore/LoopController（工具批执行、LoopGuard 同指纹打断、
  max-turns 收敛闸、错误分类——工具错误回填自救/provider 错误 turn error/
  stop 优雅收尾）
- **Provider 层**：Anthropic 原生 SSE（含 `glm-5.3[1m]` 变体剥壳）、OpenAI
  兼容三向转换（tool_calls id 对齐、reasoning_content↔thinking）、429/529/5xx
  指数退避重试（流中断不重试）、usage 双波记账
- **工具层**：Bash（进程组/30k 截断保首尾/后台任务表）、Read（cat -n/图片
  base64/25MB 拒读）、Write/Edit（唯一匹配/mtime 读后写守卫/fuzzy 提示）、
  Grep/Glob（命中上限）、WebFetch/WebSearch、TodoWrite 全量覆盖
- **进程治理**：登记式 ProcessSupervisor（双信号判死：stdout 静默+产物 mtime；
  SIGTERM→SIGKILL 收割；绝不扫全局进程表）
- **上下文工程**：ContextAssembler 六段注入（核心提示/工具要点/环境块/宪法
  CLAUDE.md+AGENTS.md/skills 索引/长期记忆）、Compactor（92% 窗口触发、K=20
  轮保留、tool_use/result 配对完整、摘要模型降级硬摘要）
- **权限与钩子**：四模式 PermissionEngine（deny 最严优先，bypass 也拦）；
  HookRunner 5 事件（exit 2 = block，stdout 可改写输入/输出）
- **子代理**：Task 工具（general/explore/plan 用途型，独立 transcript，信号量
  并发 4，默认禁递归，final text 30k 截断）
- **MCP**：stdio JSON-RPC 客户端（`mcp__<server>__<tool>` 注册，失败降级警告）
- **持久化**：transcript append-only JSONL（compact 点截断重放、半行容忍）、
  session.db SQLite 索引
- **CLI**：与 Claude Code headless 契约同构（`-p --verbose --output-format
  stream-json`、PROMPT=argv[-1]、--session-id/--resume/--fork、`Session ID
  already in use` 同文案、30s heartbeat）；REPL（/resume /fork /compact /todos）
- **测试**：150+ 用例零 token 全分支（fake provider 控制文件协议 + 内联脚本
  provider + CLI 子进程 e2e）

[0.1.0]: https://github.com/placeholder/hahaness/releases/tag/v0.1.0
