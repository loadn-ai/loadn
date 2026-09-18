# Changelog

本项目的全部显著变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [0.6.0] - 2026-09-17

### Added
- **CC-Fingerprint 伪装层**（GLM Coding Plan 通道把请求出口对齐 Claude Code
  客户端形态）：`HAHANESS_STEALTH=cc`（或 config.json `extra.stealth`）开启，
  仅对 GLM 主机自动生效（`cc-all` 全通道，测试用）。五层：
  ① headers 全家——`claude-cli/{VER} (external, cli)` UA + x-stainless 家族
  （lang=js/os/arch/runtime/runtime-version/retry-count 随重试递增）+ beta 位
  + 只发 Bearer 不发 x-api-key；② 请求体——metadata.user_id 实例级稳定
  （stealth_identity.json 落盘复用）+ 会话后缀 sha8 稳定派生、stream 恒真
  （辅助请求强制流式）、max_tokens CC 档位值、去 temperature；③ system 前置
  CC 官方身份句 + 剥 hahaness 自报身份句、全量清扫身份字样（provider 层统一
  出口，摘要/planner 辅助请求同过伪装层）；④ 工具面整形——隐藏
  InteractiveShell、注册 CC 名单 stub（AskUserQuestion/EnterPlanMode/
  ExitPlanMode 无害回执；BashOutput/KillShell 映射真实后台任务治理）；
  ⑤ 行为纪律见 docs/stealth.md（校准流程 + 上线检查单 + 版本跟追）。
  **CC_PROFILE 当前为占位值，上线前须按 docs/stealth.md 真机抓包校准。**

## [0.5.0] - 2026-09-17

七大 Harness（pi/dsh/codex-rs/hermes/openclaw/opencode/openhands）深读清单
的 P0 六项集成：成本、截断、压缩、防循环、错误恢复、编辑匹配。

### Added
- **prompt caching 三断点**（pi 布局）：tools 末项 / system 块数组 /
  末消息末块打 `cache_control:ephemeral`（只加在 to_dict 顶层 dict——嵌套
  是共享引用，不污染 session/transcript）；摘要/planner/grace 走
  `use_cache=False` no-cache 通道；`extra.disable_prompt_cache` 总闸 +
  网关 400 点名 system/cache_control 时自动降级重发双保险。修两个现存
  bug：**usage 累计口径导致实际上下文 20-40% 即过度压缩**（改 last-call
  口径）与 **resume 后 compact 摘要丢失**（replay 回注摘要头）
- **截断即行动**（pi+codex）：clip_middle 加 Warning 头（原尺寸可见）；
  Read 截断 footer 给续读 offset、超长单行给 `sed -n 'Xp'` 命令；Bash
  超限全文落 session scratch 目录（不落 cwd——保 system 缓存断点）
- **压缩四件套**（dsh/codex/pi）：渲染层 prune（旧 tool_result 骨架化，
  不 mutate Message）；交接文档五段模板 + 文件账本（readFiles/
  modifiedFiles 自动提取）+ 有旧摘要时 UPDATE 增量模式；token 预算切点
  （COMPACT_KEEP_TOKENS=20k，至少 2 轮）；摘要 clip 随窗口缩放 + 失败
  重试一次再降级
- **grace call**（hermes）：轮次耗尽给一次无工具收尾调用写结论（usage
  并入、num_turns 不增、自包异常防 subtype 改写、stop 时跳过）
- **两段式防循环**（dsh）：同指纹同结果第 2 次轻提醒、第 3 次才硬打断
- **残缺 toolCall 拒绝**（pi）：max_tokens 截断产出的半截 JSON 参数不
  执行，回填 is_error 让模型重发（非截断坏 JSON 仍走 `_raw` 自救老路）
- **FailoverReason 溢出自救**（hermes）：`classify_error` 错误→恢复动作
  查表；context_overflow → 强制压缩后重发（每 turn 至多一次、不计流重试
  额度）；无 compactor/no_compact 如实报错
- **Edit 归一化 fuzzy**（pi）：精确 0 命中后 NFKC+智能引号+行尾空白归一
  的行序列匹配（未动行保留原始字节、CRLF 跟随、空窗守卫、多命中拒绝、
  .ipynb 排除）
- **Claude 资源共享**（双引擎对等）：用户宪法回退链 `~/.agent/AGENT.md` →
  `~/.claude/CLAUDE.md`；skill 发现根加 `~/.claude/skills`（用户全局 skill，
  项目级同名覆盖全局）；agent 发现根加 `~/.claude/agents`；长期记忆块读
  Claude Code 的 per-project 自动记忆 `~/.claude/projects/<slug>/memory/
  MEMORY.md`（slug = cwd 路径 `/`→`-`，与 claude 目录名规则一致——记忆跨
  引擎共享演化）+ 全局 `~/.agent/MEMORY.md` 兜底

## [0.4.0] - 2026-09-17

Terminal-Bench 超时死法画像驱动的四项引擎加固（31/32 临终任务死在 Bash
等待里：单条长命令同步等死 / 交互轮次爆炸 / 物理编译墙）。

### Added
- **Bash 前台 60s 自动转后台**（死法①根治）：前台命令跑满
  `BASH_AUTO_BG_S`（60s）仍未结束 → `ProcessSupervisor.adopt` 收养进程
  （已捕获输出作为 prelude 落盘、reader 续读、shutdown 收割梯子同构），
  立即返回 task_id/pid/pgid/输出文件 + tail 轮询示例；显式
  `timeout_s ≤ 60` 保持快速失败语义；无 supervisor（子代理）降级原行为；
  阈值线上恰好退出走 `_drain_and_finish` 排空收尾
- **InteractiveShell 工具**（死法②）：纯 pty 会话（零依赖，tmux 不保证
  在容器里）——一次调用携带 steps 脚本化多轮 send/expect，transcript 一次
  带回（play-zork 类任务 LLM 轮次砍 5-10x）。master O_NONBLOCK +
  TIOCSWINSZ + TERM=xterm；父进程关 slave 保 EIO；轮询读（取消安全，
  不用 add_reader）；expect 超时非错误（会话保留）；EOF 三路判定；
  会话上限 4、steps ≤40、单调用预算 900s（先于 loop 层超时返回）
- **编译并行死规矩**（死法③）：CORE_PROMPT 加"make/编译/大安装必带
  `-j$(nproc)`"；auto-bg 语义与"转后台后先干别的"同步进提示
- **thinking 预算旋钮**（P2）：`HAHANESS_THINKING_BUDGET` env 或
  config.json `extra.thinking_budget` → Anthropic 形请求体
  `thinking.budget_tokens`（clamp ≥1024 且 < max_tokens-1024，默认关）

### Changed
- `ProcessSupervisor` 新增 `adopt`（收养在跑进程）/ `track`（只登记不启
  reader，`_SyncProcAdapter` 包同步 Popen 防 shutdown 阻塞事件循环）/
  `mark_exited`（track 无 reader 的落态出口）
- Bash 输出格式化抽 `_format_output`（前台/排空收尾共用）

## [0.3.0] - 2026-09-17

吸收 Claude Code CLI 的能力补齐：流式契约、工具面、上下文工程三线升级。

### Added
- **stream_event 逐 delta**：`--verbose` 下 stream-json 逐事件外发
  `{"type":"stream_event","event":{Anthropic SSE 形事件}}`（对位 claude CLI
  同位语义；宿主可用于打字机渲染）。`StreamEventSynthesizer` provider 无关
  合成（message_start/content_block_*/message_delta/message_stop，块 index
  连续、delta 拼接与整块 assistant 守恒）；ChunkAssembler 改按块到达序输出
  （修真实流交错轮的块序失真）
- **MultiEdit**：单文件多处原子编辑（按序应用——后一编辑匹配前一编辑作用后
  的文本；任一步失败零写入并报 1-based 序号；守卫复用 Edit 内核并新增写前
  mtime 复查）
- **Bash cwd/env 每调用参数**：cwd 相对解析限工作区子树内（符号链解开后
  判）、env 三层合并（os.environ < env_extra < 调用级）；前台/后台两路径均生效
- **NotebookEdit**：.ipynb cell 级编辑（replace/insert/delete，cell_id 定位，
  insert=插到目标 cell 前或缺省尾部追加）
- **Skill 工具**：按需加载 SKILL.md 正文（剥 frontmatter、$ARGUMENTS 替换、
  16k clip）；发现逻辑抽 `core/skills.py` 单一真相（索引与工具共用、同名
  .claude > .agent > home 先到先得）；发现非空才注册（AUTO_REGISTER=False）
- **自定义 subagent**：`.claude/agents/*.md`（frontmatter name/description/
  tools/model + 正文为 system_add）；项目级覆盖 home 与同名内建；TaskTool
  enum 动态化；frontmatter 的 model 经 model_provider_factory 生效
- **宪法祖先链 + @import**：CLAUDE.md/AGENTS.md 从边界根（git 根；repo 外
  home 下到 home；再外只读 cwd）到 cwd 远→近收集，每层 CLAUDE.md 优先；
  `@path` 行级 import（相对所在文件、深度 3、seen 防环、缺失留注释）；
  用户级 `$HAHANESS_HOME/AGENT.md` 置顶；总量 120k 截最远端
- **small_model 接线**：provider `chat(model=...)` per-call 覆盖（过
  api_model_name 剥变体后缀），Compactor 摘要优先用小模型
- loop 外发 todos 事件（TodoWrite 成功后）与 plan 事件透传

### Fixed
- **StreamInterrupted 假 success**：断流原穿透 run_turn（finally 落
  subtype=success 的 result 事件 + traceback 崩溃 + `_kill_my_children` 被跳
  过、子进程泄漏）→ 现在 loop 层退避重试（STREAM_RETRY_MAX=2；半成品
  assistant 从未入 messages，整轮重放语义安全），耗尽转 error_during_execution
- **retriable error chunk 从未重试**：provider 声称"交给 loop 决策"而 loop
  无决策分支 → 现与 StreamInterrupted 同一重试循环
- **意外异常落假 success**：run_turn 加 catch-all → error_during_execution
  落盘收尾（`_kill_my_children` 恢复可达）
- StreamJsonEmitter 静默丢弃 loop 的 plan 事件（现透传）

## [0.2.1] - 2026-09-16

### Fixed
- plan 事件 emit 未 await（coroutine 泄漏）；planner 判定可观测：拆与不拆
  都落 transcript（system/plan 事件）+ 对外事件

## [0.2.0] - 2026-09-16

### Added
- **并行拆分调度（TaskPlanner）**：任务前置评估——可拆（2-3 个独立子任务）
  → SubagentManager.gather 并行扇出，子结果注入主循环收敛（主 agent 保留
  整合/验证/补做）；`--no-plan` 直跑
- **长命令后台纪律**：Bash run_in_background 经 ProcessSupervisor 托管
  （输出 tee、双信号判死、CLI 退出扫 /proc 兜底清杀）

## [0.1.1] - 2026-09-16

### Fixed
- **max_tokens 截断空转**（Terminal-Bench aimo 归因）：16k 输出上限被长
  thinking 吃满、text 零产出空转——MODEL_MAX_OUTPUT_TOKENS 提到 32768，loop
  识别「stop_reason=max_tokens 且零文本零工具」注入一次收敛续轮
- 工具错误文案与 LoopGuard 细节修复（批 1 深度归因产物）

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
