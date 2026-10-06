# Changelog

本项目的全部显著变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [未发布]（Unreleased）

- **按目标系统的权限三档（P10，allow/ask/never）**：外部副作用动作的
  目标级持久策略——target_policies 表（match=域名精确/*.suffix 通配或
  动作类名；kind host|action）。决策序在审批门之前：never→建审批+自动
  否决（全链路留痕）；always→建审批+自动批准（一次性码随 create 响应
  给 agent 即用即 consume——用户对该目标的常设决定语义，与逐次审批同一
  条 consume 链）；ask/无记录/**表损坏**→原审批门（fail-closed）；多规则
  命中最严优先（never>always>ask）。host 维度与 action 维度叠加取严。
  挂两点：POST /sessions/{sid}/approvals（bash_allow 本地动作不进此表）
  与 egress 出口 ask 门（never 直接拒 / always 落临时授权同审批批准
  回收语义）。审批卡「Always allow」三档窄化（exact=动作类+参数指纹 /
  domain-action / target，created_from=approval:id）；SKILL.md frontmatter
  targets: 仅建议展示绝不自动生效。管理面 /api/admin/target-policy CRUD
  （改动入审计 policy_change）。手测某域名 never 后浏览器动作被拒——
  UI 手测未走真浏览器（API 层 never/always/ask 全对赌，如实记录）。
- **Telegram 双向对话渠道（P9/P9b）**：ChannelProvider 抽象（WhatsApp/
  Signal 注册位预留）+ Telegram 首实现——长轮询 getUpdates（指数退避
  1s→60s，成功复位）；白名单 chat_id（fail-closed：非白名单忽略+审计
  channel）；bot 消息丢弃（防 loop）；每 chat 限速 10/min；命令
  /new /bind /status /unbind；文本=绑定会话用户消息（ENGINE.submit 经主
  loop 线程安全投递）；turn 终态增量回信（Markdown，>4096 按段分段）。
  管理面 /api/admin/channels（token 只入 vault password 位、白名单/启停
  热生效、getMe 健康探测）+ 资源控制台「渠道」卡。**P9b**：审批请求推
  内联键盘（Approve/Deny），回调走既有 decide 语义、一次性确认码经
  steer 注回会话。SCHEMA_REV 6（channel_bindings）。手测真机问答/审批
  未做（无 bot token 环境；fake API 五件验收全绿）。
- **来源 chips + 动作台账页（P8）**：消息流里带 memory_hits 的 assistant
  消息下方渲染来源 chips（🧠用户域/📁项目域 + id8），点击弹层看记忆全文/
  当前状态（存在/已修改/已删除）/reason/来源会话，「去记忆页编辑」直达
  管理中心记忆 tab。新增 `GET /api/activity` 三源聚合（工具动作
  bash/文件/网络 + 审批全态 + 审计拦截）——过滤（会话/类型/状态）+ 分页
  （limit≤200，工具源 400 行有界窗口，禁大表扫）；卡片三态完成/失败/
  被拦截待审批（带「去审批」直达会话）+ 已否决态；管理中心「台账」tab。
- **记忆来源标注 Sources 数据层（P7）**：记忆条目改**稳定 id**（域|溯源|
  摘要|内容 的 sha1[:8]——同内容重抽幂等原位更新，不再换 id）；注入行首带
  `[memory:<id8>|溯源]`（人类可读）。每 turn 实际注入清单写入 assistant 消息
  JSONL 扩展字段 `memory_hits: [{id,domain,reason,hash}]`（选择器与渲染
  共用单一真相——节关/被预算裁=空清单；旧记录无字段读取不报错）。reason
  枚举 explicit/inferred/user_pref/project_fact（直写=explicit、自动抽取按
  域=user_pref/project_fact，存条目 frontmatter 与 manifest，注入透传）。
  新增 `GET /api/sessions/{sid}/messages/{mid}/sources`：命中记忆全文+
  来源会话 id+当前状态（present/modified/deleted——已删除经 git 史回溯
  内容）。messages 表加 memory_hits_json 列（SCHEMA_REV 5，additive）。
- **webui 记忆管理页（P6，Lindy「不是黑盒」语义）**：管理中心「记忆」tab
  ——域 tab（用户级/项目级）、左条目列表右编辑器（源码/预览双模式，
  react-markdown 复用）、历史侧栏（版本查看+恢复）、删除二次确认。API
  `/api/memory/*`（7 端点，写操作 admin 双头）：新建/编辑**保存即 commit**
  （`memory: manual:webui …`）、编辑与删除**重跑蜜罐/凭证护栏**（命中 400
  返回原因，不落被拒内容）、删除留史、已删条目按原 id 重建恢复（溯源从
  版本 frontmatter 读回）、禁整仓 reset。新建/编辑/删除/恢复入审计账本
  （type=memory；查看不记）。**架构**：存储层抽顶层 `loadn/memorystore.py`
  （进程边界禁 webui 入 loadn.core，而域锁/manifest 协议必须单实现——
  skilllock 同款先例），引擎侧转消费者+再导出（调用方导入路径不变）。
- **记忆 git 版本化（P5，MemFS 语义）**：一次 commit=一次「记住」——域目录
  首写 git init（本地仓，永不触网络，GPG 关），写入=add -A+commit
  （`memory: <摘要> [session:<id>]`，LRU 淘汰的删除同 commit 入史可找回）、
  忘掉/提升各有独立提交、护栏拒绝留 `--allow-empty` reject 提交（被拒内容
  不入树）。**并发安全**：manifest 读改写+淘汰+提交全段入跨进程 flock 域锁
  （用户域全局目录多会话并发是常态——20 并发实测曾互抢 tmp 丢更新，已修）；
  锁超时=文件照写、commit 让下趟补。CLI 扩展 `loadn memory log
  [--domain]` 与 `restore <commit>`（恢复单条重新入库，禁整仓 reset）。
  git 缺席静默降级为无版本记忆（读写不受影响）。
- **用户级跨项目记忆域（P4）**：域键抽象 project/user——新增
  `$LOADN_HOME/memory/_user/` 全局域（换项目不再失忆）。归属判定为确定性
  启发式（禁模型猜）：第一人称偏好词表 ∧ 无路径/文件/包管理指称 → user，
  拿不准 → project 宁保守；词表经 `memory/user-domain-words.txt` 可扩充。
  注入两段：`[user-memory|溯源]` 置于 `[memory|溯源]` 之上（身份先于项目）；
  两域同守蜜罐/凭证护栏与 LRU 上限。"忘掉 X"跨两域生效；新增 CLI
  `loadn memory promote <id>` 手动提升。开关 `LOADN_USER_MEMORY=off`
  （默认 on）= 显式面拒绝+目录零创建+注入面零读，行为与单域现状逐字节
  一致。不迁移存量记忆；boundary 锚点仍只存 project 域 manifest。
- **Webhook 事件触发入口（P3）**：外部事件（PR/支付/表单）→ agent 会话。
  实体 {token(20hex)/name/profile/prompt 模板/enabled/allowed_ips/限流}，
  CRUD 管理面（`/api/hooks`，admin 双头）+ Webhooks 管理页 + 调度页并列卡。
  公开触发 `POST /hooks/{token}`（挂 `/api` 外——W0 只护 /api，token 即凭证，
  share 同构；host_guard 照守）：校验→限流→渲染→建 session 后台执行，
  `202+{session_id, run_id}`；`GET /hooks/{token}/runs/{run_id}` 轮询。
  安全：命中/拒绝全入审计账本（TYPES 扩 `webhook`）；payload 仅 `{{payload}}`
  字面替换（禁求值）、≤64KB 截断标注、整体作用户消息（不可信，不解析为
  指令）；限流 6/min 默认；IP 白名单不信任 X-Forwarded-For（fail-closed）；
  SCHEMA_REV 3→4（webhooks/webhook_runs 两表，additive）。卡面「/api/hooks/
  {token}」路径按 W0 现实修正为 /hooks/{token}。签名校验留 TODO。
- **MCP 工具懒加载 ToolSearch（P2）**：单 server 工具数超阈值（默认 15，
  env `LOADN_MCP_LAZY_TOOL_THRESHOLD` 覆盖，0=关）时不全量注入工具面
  （大 server 单家可吃 12.6 万 token）——只注册 `ToolSearch`：参数 enum 即
  索引（name+description 首句+来源 server），点名即物化并返回完整
  inputSchema（一轮完成，禁止两跳猜参数）；复查补强：直接调用索引内工具
  由 loop 当场物化执行（不吃「未知工具」错误）、disallow 的延迟名在 build
  侧预过滤出索引（enum 不可见，fail-closed）。暗礁处理：引擎内部直用的
  `mcp__lsp__diagnostics` 永不延迟（防 LSP 诊断回注静默失效）、
  tool-call-repair 已知工具集含延迟名、延迟连接与会话同寿命（会话内
  schema 缓存）。验收：50 工具 fake server 载荷降 >60%、≤15 逐字节回归
  一致、loop 级一轮两调用链路绿。result 事件加可选 `mcp_deferred`
  观测字段（transcript 侧，同 diffs 语义）；`init.tools` 保持 spawn 快照
  语义（PROTOCOL.md 注记，无契约变更）。
- **技能目录兼容 agentskills.io（P1）**：发现根新增项目级 `.agents/skills/`
  （开放标准目录、`npx skills add` 落点），同名优先级最高、照过 P0-2 信任门
  ——负路径对赌实测抓出信任门资源枚举不认 `.agents` 的真缺口（只带该目录的
  clone 仓库会被判「无资源」直接放行），已补。frontmatter 解析容错兼容
  agentskills.io 形状（缺 name/description 回落默认值，`allowed-tools`/
  `metadata` 等字段忽略不炸）。安装三来源：GitHub repo/tree URL、
  `owner/repo/path` 简写、任意 https `.zip`/`.tar.gz` 归档 URL（明文与非
  归档扩展名 fail-closed 拒绝）。**安装↔供应链锁打通**：远程来源装完即
  盖 `source` 戳 + 写 `$LOADN_HOME/skills.lock.json`，out-of-band 篡改 →
  引擎拒索引；经管理面编辑/重装自动刷新锁（zip 上传视作用户自备不 pin）。
  新增导出：任一 skill 导出为 agentskills.io 兼容 zip（frontmatter 规范化
  补必填字段、剥 `source` 戳与 `.loadn-*` 内部元数据，可原样装回）。
- **测试质量战役（T + M0-M6）**：行覆盖 78.8%→**83.1%**（CI 门同步收紧）；
  自研突变测试 runner（`scripts/mutate.py`，AST 定位 5 算子+窄测试集映射）
  对 41 个安全与核心文件注入 **2589 个变异**，杀伤率 **76.1%**
  （安全批 483/731=66%、功能批 1488/1858=80%）；补 ~110 例否定路径
  对赌测试（全部注入复验）；挖出并修复产品真 bug 11 个（含 Write 工具
  mtime 守卫缺失、repomap 类方法盲区、backup 同秒重入污染恢复源）。
  逐文件终值与等价变异白名单见 `tests/TEST-PLAN.md` §七。
- **CI 突变回归门**：mutation job 周跑 8 文件子集逐文件下限
  （`tests/TEST-PLAN.md` §七）；pip-audit 供应链周扫。
- **测试同步纪律入宪法**（`CLAUDE.md`）：新文件必进 TARGET_TESTS 映射/
  守卫必配否定路径对赌/合入前跑该文件突变窄集/覆盖率门只升不降。
- evals 场景 5→10；契约 v1/v2 对赌补全（tool_use_failure 终态事件、
  平台 durable 事件转发两个真缺口修复）。

## [0.6.25] - 2026-10-03

- **产物摘要回填可靠性**：单 turn 会话收尾那次若模型输出格式漂移会静默
  失败且无重试时机（实测病例：ebbe 会话 6 产物停留英文文件名）。三层修复：
  - 静默路径全部留痕（格式不中/0 命中告警入日志，不再无声 return 0）
  - **会话打开兜底触发**：GET 会话详情发现缺摘要行即经主循环线程安全
    投递一次回填（幂等；app.state.loop 于 lifespan 登记）
  - 前端产物 tab 激活即拉新，缺摘要时 8s 后补拉一次（回填落地即可见）

## [0.6.24] - 2026-10-02

- **产物中文标题 + 一行摘要（titlegen 回填）**：产物列表此前是英文文件名
  改写、无摘要——多了认不出。现在每个 turn 收尾后台批量回填：简体中文短
  标题（≤12字，概括内容）+ 一行摘要（≤40字），一次 LLM 调用出全批
  （doubao mini 级成本）；扫描重跑不覆盖已回填标题；titlegen 未启用/失败
  静默降级回文件名兜底。
- 前端产物卡：中文标题 + 两行摘要 + 来源徽标（任务生成 / 界面导出）。
- artifacts 表加 `summary` 列（additive 迁移）；测试 4 例含扫描保题对赌，
  突变窄集 55.6%。

## [0.6.23] - 2026-10-02

- **SSE 断点续传（重复输出 4-5 次的根治）**：手动重建的 EventSource 不带
  浏览器内建 Last-Event-ID 状态——每次断线重连（网页后台化/网络抖动/服务
  重启）服务端都精准回放活跃 turn 尾部，叠进已积累流水=重连几次重复几份。
  修复：connectSse 维护已见事件 id 游标，重连 URL 带 `last_event_id=`
  （服务端通道现成，事件 id 全局自增落库、跨重启稳定）；处理层双保险丢弃
  续传窗内重复帧；resync 不再清空已积累 items（续传只补未见事件，清了丢内容）。

## [0.6.22] - 2026-10-02

- **最新活跃置顶（排序修复）**：两个根因——
  - `db.update_session` 无字段调用 `if not fields: return` 提前返回，引擎排队
    路径的置顶触碰（`update_session(c, sid)`）一直是**死代码**：发消息/调度
    启动的任务不跳顶。修为「无字段+touch=纯触碰 updated_at」；
  - 长跑任务期间 updated_at 不动，会被后来完成的旧任务压下去——侧栏排序改
    「在跑任务恒置顶」（含跑着子任务的项目组拉起），其余按 updated_at。
  - 语义钉死测试：无字段+touch=False=完全 no-op（守卫反转可杀）。

## [0.6.21] - 2026-09-29

- **审批策略控件补缺**：后端 `approval_enforce`（enforce|warn）自 v0.6.5 即可
  写，但安全运维面编辑器没有该控件——审批卡提示「在下方明细切换」指向一个
  不存在的开关。补「审批策略」chip 行（强制=确认码拦截 / 仅告警=放行+逐条
  留痕，热生效），读写走既有 PUT /api/admin/security/ops。

## [0.6.20] - 2026-09-29

- **token 门体验**：「稍后再说」本浏览器会话记忆（sessionStorage）——同会话
  不再重复弹；右下角常驻 🔑 浮标可手动重开；弹窗内加「token 置空=完全免认证」
  的指引。无认证模式语义以回归测试钉死（`server.token` 空 → 整站含管理面
  放行，外层防护自负——仅本机/反代 basic auth 部署适用），CONFIG.md 同步。

## [0.6.19] - 2026-09-29

- **全屏 TUI（Claude Code 风格，textual）**：裸 `loadn` 自动进入——滚动流水
  （assistant 文本 / `⏺ Tool(...)` 调用行 / `⠿`·`✗` 结果摘录 / todos 勾选）+
  状态条（模型·会话·本轮 in/out/cache·累计 tokens）+ 单行输入 + Footer 快捷键；
  Ctrl-C 中断当前轮（StopHandle，工具步完成后停）、Ctrl-L 清屏、Ctrl-D 退出；
  斜杠指令集与 REPL 同源（/resume /fork /compact /todos /undo /clear）。
- 依赖红线：textual 走可选 extra `loadn[tui]`；缺席回落基础 REPL（带安装
  提示），`LOADN_NO_TUI=1` 强制 REPL。引擎零改动（复用 run_turn emit/stop）。
- 测试：渲染助手纯函数 3 组 + textual pilot 全链 2 例（fake provider 零 token：
  mount→提交→run_turn→emit 路由→累计；忙时拒新轮守卫对赌）。

## [0.6.18] - 2026-09-29

- **resync 重复返回修复**：v0.6.17 的前台恢复在「live 已有同 turn 流水」时
  保留了旧 items，而 SSE 重连会精准回放同一 turn 尾部——两路叠加流水翻倍
  （违反「回放是 items 单一来源」不变量）。修复：断线重连路径一律空 items
  重播种（回放重铺）；连接健康且 turn 未变才保留已积累流水。另加 5s 冷却
  去重（focus 与 visibilitychange 成对触发导致双拉/双连的抖动源）。

## [0.6.17] - 2026-09-29

- **PWA 前台恢复拉新（「一直运行中」假象修复）**：服务端 SSE/代理链实测健康
  （ping/回放/公网穿透全验证）；病灶在前端——iOS PWA 后台冻结定时器与 SSE
  重连 setTimeout，回前台后界面停在后台前最后一帧，须手动刷新。修复：
  `visibilitychange`/`focus` 唤醒 → `store.resync()`（立即拉会话列表+当前
  会话全量、live 按活跃 turn 重播种/清陈旧、SSE 断线即刻重建不等退避）。
- **刷新恢复上次会话修复**：`openSession` 中 `loadn_sid` 先 setItem 又被
  误删（迁移遗留的 removeItem 多删）——刷新/重开直达功能失效，恢复。

## [0.6.16] - 2026-09-29

- **资源页自定义服务（服务 CRUD 补全）**：原服务卡为前端写死的 13 张固定卡，
  现支持「＋ 新增服务」——`resources.custom_services: [{name, url, note}]`：
  - 新增/编辑端点/删除（删服务连 vault 密钥一并清）；密钥走 vault
    `svc:<name>` 键（AES-GCM，**白名单=仅已配置服务可写**，fail-closed）
  - 消费：任务环境注入 `LOADN_SVC_<NAME>_URL`（`-`→`_`）——密钥不注入，
    凭证不进沙箱的既定边界不变
  - 连通探测 `svc:<name>` 目标（GET 根路径，≥500 判不可达）；独立测试按钮
  - config.yaml 启动 fail-closed 校验（slug/http(s)/重名）；API 面钉子登记
    POST/DELETE `/api/admin/resources/custom`

## [0.6.15] - 2026-09-28

- **设置页三卡 CRUD 化**（增量增强，沿用 setting-card/行内编辑模式）：
  - **成本价目**：裸 JSON textarea → 结构化双表（api $/M 与 plan 积分/M），
    模型行增删改 +「从内置复制」（只调一两个模型的推荐路径）+「恢复内置」
    +「复制 JSON」备份；行级校验（空名/重名/负值）红底行+文案，校验不过禁发。
    后端 additive：`pricing.builtin` 内置表透出。
  - **引擎与模型**：每引擎「清除覆盖」（yaml 段整删 + CONFIG 重置回自动探测，
    `per[name]=null` 语义；区别于逐字段清空）；`override` 标记以 yaml 原始节
    为真源，无覆盖时按钮置灰。
  - **收敛度**：每角色「恢复默认」（registry 条目弹收敛三键回 PROFILE_DEFAULTS，
    条目本体保留——数据根 registry 遮蔽仓库层，删条目=删角色是红线）；
    `reset` 与 `profiles` 可同请求并存。
  - `profile.PROFILE_DEFAULTS` 单一真源导出；否定路径对赌用例 6 则
    （null 不绕白名单/reset 未知角色拒/角色保留回归锁）。

## [0.6.14] - 2026-09-28

- **安全中心可读性修正**：隔离记录/审计事件流/待审清单/蜜罐列表的会话列
  由原始 sid（slug 冻结在创建瞬间的「新任务xxxx」）改为解析当前会话标题
  （悬停见 sid，未命中回落原样）——titlegen 事后命名不再被 sid 遮蔽。
- **沙箱档位文案纠偏**：「重启生效」为过时语义（v0.6.5 设置面出现前仅能
  手改 yaml）。实际链路：设置面写入带 `setattr(CONFIG)` 即热，spawn 期
  惰性读——**下一任务起生效**，在跑任务沙箱已定型不受影响，手改 yaml
  才需重启。UI 五处 + 后端 docstring/log/缓存注释同步改正。

## [0.6.13] - 2026-09-28

- **webui 子包化**：40+ 顶层扁平模块收拢为两个域包——`security/`（sandbox/
  egress_proxy/policy/vault/audit/approve/canary/net_policy/egress_grants/
  skill_scan）与 `integrations/`（resources/notify/pricing/share/titlegen/
  skillhub/skill_zh/mcp_admin/browser_mcp/codemode_mcp/lsp_host）；核心
  运行面（api/engine/engines/db/config…）保持顶层。全仓 ~90 处引用脚本化
  重写（绝对/相对/容器形态、函数内缩进 import、monkeypatch 字符串）。
- **版本单一真源**：仓库版本 0.3.0↔发布 v0.6.x 长期漂移终结——版本随功能
  提交进 git；release build 的「盖章」改为**校验门**（tag≠仓库版本即拒发）。

## [0.6.12] - 2026-09-28

- **routes.py 按域拆包**（架构治理第一刀）：2147 行/108 端点单文件 →
  `api/routes/` 十二域子包（skills/tools/settings/projects/sessions/turns/
  admin/approvals/schedules/files/artifacts/stats + `_common` 横切件），
  `__init__` 聚合 router——app.py 零改动；路由表前后 OpenAPI 快照逐字节
  一致。**API 面契约钉子**：`tests/contract/test_api_surface.py` 钉死全量
  (path, methods)，端点增删显形（文件间挪动不再有天然护栏的补位）。
- **AdminPanel.tsx 拆分**：1105 行 → 47 行页壳 + `components/admin/`
  五文件（Skills/Tools/Settings/Egress/shared）。
- **OSS 标准化**：pyproject 补 `[project.urls]`；CHANGELOG 追记 0.6.6-0.6.11
  并修 placeholder 链接；CONTRIBUTING 补「新 API 端点动哪里」速查行；
  ARCHITECTURE.md 布局同步。突变映射 TARGET_TESTS 同步新包路径（纪律 1）。
- 已知：`test_opencode_adopt` 在干净 HEAD 亦间歇失败（worktree 复现实证），
  与本次重构无关——待办根治。

## [0.6.6] ~ [0.6.11] - 2026-09-27/28

- **[0.6.11] 会话级沙箱档位**：`params.sandbox` 第八键（属性面板 chips）——
  单任务放开隔离（off 直跑宿主，运维/宿主接管场景）或收紧（bwrap），
  全局档位不动；`docs/CONFIG.md` 宿主接管三件套配方
  （params.sandbox off / resource_bridges rw 精确桥 / params.egress off）。
- **[0.6.10] 品牌迁移收口**：WORKDADDY_* 118 处审计 → LOADN_* 主名制；
  localStorage 键活体迁移（读侧双取+迁移删旧）；兼容层（双名认证头/
  spawn env 双注/db 迁移）保留至 v0.8.0 删除。
- **[0.6.9] SPA 缓存投毒断根**：`/assets/` 缺文件一律 404+no-store（原
  SPA fallback 回 index.html 会被代理当 css/js 缓存 → 浏览器 MIME 拒载
  = 全站裸样式）；存在文件 immutable 一年（内容哈希名）。
- **[0.6.8] 管理中心布局治理**：设置页分组导航（基础/引擎与模型/通知与
  分享/外部资源）+ 安全运维面卡片化。
- **[0.6.7] 安全运维面编辑器**：七键（沙箱档位/codemode/LSP/审批 TTL/
  放行 TTL/代理端口/授权面）WebUI 控件——局部键 PUT + 全键校验拒。
- **[0.6.6] 配置面深度核查**：六处硬编码真缺口收敛进 CONFIG（egress
  临时放行 TTL/MCP 超时等）；刻意保持项逐条记录理由。

## [0.6.5] - 2026-09-25

- **安全机制显式配置化**：新增 10+ security 配置键（L0 命令名/网络命令/
  敏感路径/蜜罐开关/资源桥接），内置表 ∪ 配置追加语义，非法值拒启
  fail-closed；宪法红线（审计链/确认码门/信任门/SSRF）保持不可配。
- **宿主机资源桥接**：`resource_bridges`（ro|rw|dev）+ `shared_readonly`
  同路径 bind 进沙箱（v0.6.5 通用面）。
- WebUI 配置入口补全（引擎/运维/分享/价目五卡+凭证库可视化编辑）；
  放行策略对齐（hook 门与 proxy 门统一三态语义、跨项目只读共享）。

## [0.6.3] / [0.6.4] - 2026-09-24

- **egress 交互管控**：三态档位 off/warn/enforce + 拦截弹卡确认（默认
  ask，403 带 agent 可读指引）+ 会话级 `params.egress` 任务放开 + 全引擎
  sid 归属（direct 引擎 per-session 回环 TCP）；安全中心出口策略编辑器
  （热生效）+ 属性面板任务外联档位。
- 凭证库可视化编辑（merge 语义：密钥留空不改·空串清除，明文只进不出）。
- v0.6.1/0.6.2：出厂白名单补 github/arxiv；hook 审计侧车退跟踪。

## [0.6.0] - 2026-09-23

- **断线回放与任务隔离**：项目子任务独立任务目录（`tasks/<NN>-<slug>/`
  私有进度/宪法祖先链继承；沙箱项目根 ro+inputs rw bind）。
- 轮换 anchor（SCHEMA_REV 2：token 轮换暂存+下 turn 注入即清；尾部摘要
  双信封注入）；中断补记账（pid 死 turn 从输出日志抢救半程入档）。
- provider thinking 续传（anthropic signature 透传/openai reasoning 回放）；
  修 kill-all 从未 await 的全局熔断失效。

## [0.4.0] ~ [0.4.3] - 2026-09-23

- **开源就绪**：去个人基础设施默认值（sms/proxy/邮箱/adb/个人域名→
  通用默认）；引擎插件机制（entry point `loadn.webui.engines` 第三方
  零侵入挂载）；社区文件（issue/PR 模板+ARCHITECTURE+EXTENDING+
  CONTRIBUTING monorepo 版）；lint 债清偿（ruff 215→0）；nvm 硬编码→
  bin 派生（可移植）。
- 0.4.1 修沙箱 node 树符号链 bind；0.4.2 资源中心+密钥入 vault AES-GCM
  （一次性自动迁移）；0.4.3 管理面视觉整备。

## [0.3.0] - 2026-09-23（预发布：功能完备，对外仍 0.x 待稳）

> 版本线勘误：原标 1.0.0 过早——开源发布未做、API 未承诺稳定，
> 降回 0.x 语义（平台线 0.1.0→0.2.0→0.3.0 续）。

### 最终形态（P1-P5 收尾，known-gap 六项关闭）
- **P1** approval_enforce=enforce（skill 文档审批化后切终态——不可逆动作
  全走确认码门）
- **P2** 沙箱全引擎覆盖（claude：nvm 树+projects+净化配置；opencode：
  +config 目录）——三引擎真机 done
- **P3** unshare-net 物理断网：unix socket（var/run/egress.sock）挂载进
  沙箱+沙箱内 lo up+socat TCP 桥（引擎零改动）；**绕代理直连全断**
  （agent 实测 example.com→000）——硬指标 1 完整达成
- **P4** 审计月分表 audit_events_YYYYMM（tail/verify/export 跨表链序）
- **P5** LLM 凭证网关注入：虚拟域 llm-gw.internal——引擎只持 dummy token，
  真凭证由代理控制域注入转发上游；沙箱不再挂 ~/.claude/settings.json，
  **挂载面全扫真 token 零命中**——硬指标 2 完整达成
- 对抗用例 101 条（tests/security/）+契约 8+架构 2，全量 663 passed
- R7 发布系统：release/upgrade/rollback（/opt/loadn 自包含实例+原子指针
  +自动回滚）；实修两坑——venv 须在最终位置创建（shebang 嵌绝对路径）、
  release venv 必须用系统 python 创建（venv-from-venv 链式符号链在
  bwrap 沙箱内断链，exit 126）
- R9 开源前置：loadn init 安装向导+品牌残留清零+默认路径通用化

## [0.2.0] - 2026-09-22

### Added（loadn webui 安全栈——W0-W6 全量，详见 docs/ATTACK_SURFACE.md）
- **W0** Web 加固：token 强制/Host 白名单/SSE ticket/管理面 admin 头/CSRF
  免疫/预览 CSP（对抗用例 T1a/T1b）
- **W1** 确定性权限平面：policy.py（L0 红线/L1 bashlex AST 出口域/glob
  兜底 warn/fail-closed）三消费点（PreToolUse hooks 双引擎物化=执行点 A、
  `loadn-web r` 网关=执行点 B、policy-check --hook）；approvals 确认码门
  （summary 平台渲染防伪造+params_hash+TTL fail-closed+ApprovalBanner）；
  三态工具矩阵（allow/ask/deny）
- **W2** 执行沙箱：bwrap 文件系统隔离（同路径 bind/档案单会话/敏感路径
  物理不挂/clearenv 白名单）——生产 loadn 引擎默认运行
- **W3** 凭证治理：vault AES-256-GCM（LDV1）+spawn env 白名单
- **W4** 供应链：八类静态扫描（红线拒装）+tar data filter 强制+能力声明
  默认禁+MCP 哈希锁（rug pull 检测）
- **W5** 数据流：出口白名单代理（enforce，生产 LLM 流量已收编）+md 导出
  白名单消毒+外链占位+canary 蜜罐（命中熔断）+数据流向面板（流量 tab）
- **W6** 审计与回滚：哈希链账本+链头日锚点（整库重算可暴露）+verify/
  export+文件快照回滚（rollback-pre 双向）+kill switch（会话级+全局）+
  谎报抽查
- 对抗用例 tests/security/ 10 文件 100+ 条（A1-E4 全编号）；全量 662 passed
- docs/PROTOCOL.md v1（引擎↔webui 唯一接口）+tests/contract/ 双面对赌；
  架构铁律测试（进程边界 CI 红牌）

## [0.1.0] - 2026-09-22

### Changed
- **品牌重绑：hahaness → loadn**（loadn-ai monorepo 首版）。包名/CLI/import 根
  `loadn`；env `HAHANESS_*` → `LOADN_*`（HOME/STEER_FILE/PROVIDER/STEALTH 四件
  保留旧名 fallback 一版，其余直改）；数据根 `~/.agent` → `~/.loadn`
  （首启自动搬迁，搬不动回退旧根）；workspace 配置目录 `.agent/settings.json`
  → `.loadn/settings.json`（hooks/permissions/agents/skills 读旧路径兜底一版）。
- 版本线重起 v0.1.0（此前历史见 0.7.1 及更早条目；功能面 = 0.7.1 全量）。

### 迁移注意
- 生产共存期（R2.5 切换前）`~/.agent` 与 `~/.loadn` 双根并存：旧引擎用前者，
  本包用后者；切换时终同步。

---

## 历史版本线（引擎包 0.1→0.7.1，2026-09-22 并入平台线；仅存档）

## [0.7.1] - 2026-09-22

### Added
- **--grind 死磕模式**：纯文本收工前过完工自检关卡（产物核对 + 预算告知），
  未过自动续战；`--budget-minutes` 告知剩余时间。参数与关卡常量：
  GRIND_MAX_NUDGES=8（完工自检最多续战次数——TB 实测 3 次太早放行，
  coq 15 分钟假交付）、GRIND_MIN_TURNS=6（前 N 轮纯文本直接放行，
  聊天/简单问答不受关卡影响）；build_agent / LoopSettings 透传 grind
  与 budget_minutes。
- **反思检查点**：REFLECT_EVERY_TURNS=25，每 N 轮注入进展/死角总结。

### 记录
- 本条目为基线定锚补记（0.7.x 代码先落盘后补 CHANGELOG）。

## [0.6.0] - 2026-09-17

### Added
- **CC-Fingerprint 伪装层**（GLM Coding Plan 通道把请求出口对齐 Claude Code
  客户端形态）：`LOADN_STEALTH=cc`（或 config.json `extra.stealth`）开启，
  仅对 GLM 主机自动生效（`cc-all` 全通道，测试用）。五层：
  ① headers 全家——`claude-cli/{VER} (external, cli)` UA + x-stainless 家族
  （lang=js/os/arch/runtime/runtime-version/retry-count 随重试递增）+ beta 位
  + 只发 Bearer 不发 x-api-key；② 请求体——metadata.user_id 实例级稳定
  （stealth_identity.json 落盘复用）+ 会话后缀 sha8 稳定派生、stream 恒真
  （辅助请求强制流式）、max_tokens CC 档位值、去 temperature；③ system 前置
  CC 官方身份句 + 剥 loadn 自报身份句、全量清扫身份字样（provider 层统一
  出口，摘要/planner 辅助请求同过伪装层）；④ 工具面整形——隐藏
  InteractiveShell、注册 CC 名单 stub（AskUserQuestion/EnterPlanMode/
  ExitPlanMode 无害回执；BashOutput/KillShell 映射真实后台任务治理）；
  ⑤ 校准流程与版本跟追见 docs/compat.md（线格式兼容层）。
  **CC_PROFILE 当前为占位值，上线前须按 docs/compat.md 真机抓包校准。**

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
- **thinking 预算旋钮**（P2）：`LOADN_THINKING_BUDGET` env 或
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
  用户级 `$LOADN_HOME/AGENT.md` 置顶；总量 120k 截最远端
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

[0.1.0]: https://github.com/loadn-ai/loadn/releases/tag/v0.1.0
