# Changelog

本项目的全部显著变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [0.7.47] - 2026-10-26

- **修复：巨消息回放冻结根治**（用户二报「仍然无法展开过程细节」）：根因
  定谳到第三层——研究会话单条消息最多 2216 个过程块，新格式穿插回放
  无折叠机制全量渲染（打开会话 1498ms、单次交互 1106ms 冻结；iPhone
  放大数倍=「点啥都没响应」），且消息组件非 memo——SSE 每帧重渲染反复
  parse 3MB JSON。三刀：REPLAY_BLK_CAP=120 分批渲染（「展开更多」每次
  +120 块）+消息组件 memo 化+展开动作增量渲染秒开。长任务打开耗时
  -85% 重渲染（剩余为多 MB JSON.parse 硬成本）。
- **新增：研究模式全通道搜索矩阵**（用户指令「启用所有搜索服务+尽可能
  翻墙+尽量多搜集」）：researcher 角色扇出搜索节重写——每个子问题至少
  2-3 条通道：中文 r search 多引擎轮换（智谱 pro/sogou 腾讯系/quark
  垂直，无 URL 时博查补）/墙外英文=真浏览器+代理直开 Google·Bing·
  DuckDuckGo/arXiv 白名单直连+Scholar 浏览器/被拦=2captcha 打码+出口
  拉黑切节点重试。单通道命中≠搜全。deep-research skill 同步。
- **修复：SSRF×代理冲突（出海链路断点）**：_ssrf_check 用本地 DNS 解析
  目标做 IP 段检查——墙外域常被污染解析成保留段 IPv6（实测 google.com
  →2001::1 Teredo），proxy fetch 对墙外站全断。proxied=True 时解析发生在
  代理远端，本地结果不代表目标：跳过 IP 段检查（协议与敏感域黑名单仍
  全查），出海抓取恢复。
- **重构：统一资源模型 v3——原生/自定义双轨合一**（用户拍板「不要区分
  原生资源和自定义资源，除主 LLM 外一切资源自由组合」）：预置平台集成
  （OCR/沙箱/搜索/短信/邮箱/VLM/打码/ADB/代理/Textr 十卡）从代码写死
  的前端常量降级为 custom_services 种子条目——与自定义服务完全同构，
  可编辑结构（图标/分类/字段/密钥/prompt）、可改 ID、可删除、可重建。
  平台集成零改动续命靠两层锚：① 锚字段投影——条目 config[].key 命中
  14 个平台键（zhipu_engine/ocr_url 等）时值同步进 ResourcesConfig
  （r search/OCR/短信等消费点免重启）；② 密钥锚——vault_ref 直接引用
  8 个原生 vault 键，条目怎么改名不影响消费。rename 改 ID 时条目与
  vault svc: 密钥随行迁移（旧名是预置名则 retire 记名，防重启复活）；
  删除预置卡=真删（retire 名单落盘，锚值/密钥留 settings/vault，平台
  集成继续可用，重建同名卡即恢复管理面）；启动幂等补种（yaml 不动，
  已有同名条目天然跳过），老部署存量扁平值无损上抬进条目（SoT 转移）；
  纯凭证条目（仅 secrets，如 captcha）合法化；ping 探测目标可编辑
  （默认 svc:自身）；title 显示名（预置卡中文名「网页搜索/AIO 沙箱…」
  回归，同时修复 v2 遗留「资源名称填了从不持久化」）。前端 ResourcesPanel
  删 GROUPS/native 双轨，抽屉全开放（ID 可改+rename 确认、自由分类
  input+datalist、密钥轮换输入全卡统一、vault 下拉全卡可换绑）；注入
  预览统一语义（锚字段也按 env 注入，参数对 agent 可见；无「原生资源」
  特殊注释）；分类分组与筛选改动态集合（自由输入分类不再丢卡）。新增
  tests/test_resources_unify 12 例+playwright mock 25 断言全过。

## [0.7.46] - 2026-10-23

- **修复：iOS 移动端灵动岛/凹口遮挡侧边栏**（用户报障：左右侧边栏被
  灵动岛挡住，按钮能看不能点）：已有顶部/底部安全区，但左右
  env(safe-area-inset-left/right) 完全未处理（横屏灵动岛在侧缘），且
  侧栏/右面板是 fixed 定位不受主容器 padding 保护（灵动岛区域 iOS
  拦截触摸）。修复：主容器全端左右 inset + ≤900px 侧栏抽屉/右面板
  三向安全区 padding；容器仍全屏贴边、内容避开系统保留区。playwright
  横屏注入 59px 模拟验证全过（避开/无溢出/hit-test 可点/默认态零回归）。
- **改进：live 运行视图「前面 N 条过程已折叠」从纯提示文本改为可点击
  按钮**（用户插话「想看」）——每次 +200 逐步展开（显式要求优先于
  性能保守，仍防一次全量渲染卡死）。

## [0.7.45] - 2026-10-23

- **新增：「聊天」纯对话角色**（用户反馈没有纯聊天选项）：effort=low、
  零技能、禁 Bash/Task/Write/Edit 等七工具（不落盘不执行不开任务）、
  不参与 auto 路由（显式选择）；前端角色选择器「聊天」项。角色接入
  仍是零代码：profiles/registry.yaml 一条 + profiles/<name>.md 一份。
- **修复：默认资产跟随只刷 prompts/ 不刷 profiles/**——数据根旧
  registry.yaml 遮蔽代码根且从不随版本更新，仓内新增角色永远进不了
  老部署（chat 上线即被遮蔽实证）。刷新范围扩为 prompts+profiles，
  并支持数据根缺失的新默认文件直接落地；用户定制（与任一历史版本
  逐字节不同）仍保留不动。
- **修复：未登录全屏登录门遮罩挡住聊天区**（用户报障「历史聊天很多
  详情无法展开 + UI 和实时不一样」根因）：cookie 过期/新环境打开时
  auth_required 主动弹全屏 rgba+blur 遮罩——内容可见但全部不可点。
  改非阻断：右下角「🔐 登录」胶囊提示，点开才弹登录卡且点遮罩可关
  （宽限期 GET 本就放行只读浏览）；needs_setup 安装向导保留全屏阻断；
  401 写操作弹门同样可关。e2e 四段对赌（无全屏遮罩/胶囊点开/错密码
  真错误/遮罩关闭恢复浏览）。

## [0.7.44] - 2026-10-22

- **品牌清理：产品面 `WORKDADDY_*` 标记全切 `LOADN_*`（用户指令：WORKDADDY
  是老名字，不应再带任何标记）**。范围 19 文件：①会话 env 注入去双轨
  （workspace.py 9 组变量 + claude_runner TURN_ID 只注 `LOADN_TURN_ID`）；
  ②读取端全部只认 `LOADN_*`（cli/config/engines×3/fetch_page×2：SESSION_ID/
  PROJECT_ID/API_TOKEN/HOME/CLAUDE_BIN/OPENCODE_BIN/ENGINE_BIN/CDP_URL/
  CDP_TOKEN）；③宪法模板真源 4 处教法改 `"$LOADN_CLI" r …`（顺带发现并修复
  包内构建副本落后真源的漂移——以真源覆盖对齐）；④resources.py CDP 子进程
  env 去双设。**有意保留的升级安全垫（注释标注，下个大版本移除）**：收养
  判活对老 spawn 进程双认 `WORKDADDY_TURN_ID`（否则发版窗口正在跑的 turn
  全部判死被误收尸）+ env 透传白名单保留 `WORKDADDY_` 前缀（老部署链不断供）
  + 对应过渡测试。**Breaking（已评估可接受）**：存量会话宪法仍教
  `$WORKDADDY_CLI`，新 spawn 不再注入该变量——agent 发现变量为空会回落
  `loadn-ops`（a060 实证自愈路径），且 v0.7.43 起 console script 自举使
  任意调用形态可用；新会话模板教法已归正。

## [0.7.43] - 2026-10-22

- **修复：任务 agent 调 CLI 报 `No module named loadn_webui`（多任务连环报障）**：
  根因=工作区宪法模板历史教法 ``python3 "$WORKDADDY_CLI" r search …``
  ——系统 python3 显式执行 venv console script 绕过 shebang，目标包
  在系统解释器不可达必炸（v0.7.30 时段同坑当时仅 agent 侧绝对路径
  绕过，未根治；模板 4 处持续教错误写法）。双层修复：①release
  build venv 构建后 `_patch_entry_scripts` 给 loadn / loadn-web
  console script 插入自举段——探测目标包不可达时 re-exec 同目录
  venv python（与 shebang 同源），任意解释器调用形态统一收敛，直接
  执行零影响，防死循环走哨兵环境变量（argv[0] 判据不可用：CPython
  启动把 sys.argv[0] 重写为脚本路径；realpath(executable) 亦不可用：
  venv 与系统 python3 常解析到同一二进制 symlink 链——均实测）；
  ②模板 4 处改为直接执行写法（新会话教法归正；fetch_page 纯标准
  库保留 python3 前缀）。对赌 4 例：改写插入位置+幂等 / 真实
  re-exec 行为（对照组=生产报障精确复现，实验组=同命令成功出
  usage）/ 防死循环哨兵 / ops+ws 回归 22 passed。

## [0.7.42] - 2026-10-22

- **功能：主聊天流 Markdown 排版系统升级**（设计稿 `.loadn-prose` 现代
  开发者级排版）：标题层级呼吸感（h1 底部分隔线/h2 左侧靛蓝→紫渐变
  竖条/h3 层级字号）；**行内代码胶囊**——mono 字体+浅底+细边框+5px
  圆角+`box-decoration-break: clone`（路径/命令与中文混排不再粘连，
  长命令换行每段自带边框）+hover 高亮；列表悬挂缩进+自定义靛蓝圆点
  （替代默认 disc）；有序列表 mono 计数徽标（方块数字，替代默认
  decimal）；代码块升级深色终端风（#0f172a 底，编辑器语义亮暗主题
  一致）；引用块左缘靛蓝条+浅底+右侧圆角。全部走双主题令牌，聊天
  流气泡（.md）与文件预览（.md-preview）同步生效。computedStyle 断言
  9 项全过（竖条渐变/胶囊边框圆角 mono/圆点色/计数徽标 20px 盒/深色
  pre/引用条/表格回归/溢出 0/深色主题令牌自动适配实证）。

## [0.7.41] - 2026-10-21

- **修复：资源面板输入框裸 UA 样式（方正黑框）**：`admin-input` 类自
  资源 Hub 首版起在 CSS 中**零定义**——全部 20 处输入框（搜索/抽屉
  表单/行式参数编辑/内联密钥面板/凭证库存入与轮换）渲染浏览器默认
  样式（黑色边框、无圆角）。补完整控件基类（语言对齐管理中心
  `.setting-row input` 统一面）：浅色令牌边框+8px 圆角+focus 光环
  （`--accent-ring`）+hover 边框加深+disabled 态+mono 变体；
  select/textarea 同一适用。computedStyle 断言：全部输入框
  border=令牌渲染色、radius=8、focus 光环 3px 在位（修复前=UA 黑框）。

## [0.7.40] - 2026-10-21

- **功能：工作区文件管理写面**（用户需求：临时调整内容不再只能靠
  prompt）：`files.py` 四写端点——PUT 编辑保存（UTF-8 文本 2MB 上限）/
  POST 新建 file|dir（409 重复）/DELETE（仅空目录可删，防误删整区）/
  PATCH 重命名移动（父目录自建），统一 `_write_target` 防穿越（resolve+
  relative_to+绝对路径显式拒+工作区根保护），写后树缓存即时失效；上传
  复用既有 /upload。前端工作区 tab 文件子页：工具条（上传/新建）+
  hover 行操作（编辑抽屉等宽 textarea+dirty 态/改名/删除二次确认）。
  对赌 5 例（中文写回读/路径穿越 403/2MB 413/重复 409/非空目录 400）+
  契约快照 147 项 + mock 11 断言全过。
- **功能：专业领域任务强制点名员工**：`_type_help` 员工清单尾加派单
  规则（专业领域必须 subagent_type=职责匹配员工；不点名将随机指派
  临时工）+ Task 工具参数描述强化（含法律风险审查→法务顾问示例）。
  引擎面 92 passed。
- **功能：资源 Hub 设计稿 v2 重构**（管理中心「资源」tab）：SVG 图标
  库 15 键彻底取代 emoji（存量 emoji 数据自动回退渲染，零迁移）；
  图标选择器（5 列弹出网格+外点关闭）；名称→ID 智能联动（手改保护）；
  字段 label→注入环境变量名自动推导（端点/账号/超时等中文标签→
  `LOADN_<ID>_<SUFFIX>`，手改 env 优先）；快捷参数胶囊；架构蓝图
  5 卡（HTTP 微服务/邮箱收发/AI 模型网关/物理真机/空白自定义，套用
  即回填）；抽屉内联新建加密密钥（`svc:<资源id>` 绑定，保存落库，
  明文只写不读）；vault 引用下拉带已加密/未设状态；凭证库写面——
  存入新密钥+行内轮换（复用 `PUT /admin/vault/{platform}` 唯一编辑
  面端点，merge 语义与审计不变）。后端零改动。mock 41 断言全过 +
  资源面回归 25 passed。

## [0.7.39] - 2026-10-21

- **修复：pip 安装形态创建会话 500（运行时资产三处遗漏）**：仓库根
  `prompts/`（会话宪法模板 workspace.md.tmpl）、`profiles/`（档位人设
  coder/researcher 等 + registry.yaml）、`scripts/fetch_page.py`（宪法
  与 env 注入引用的抓取脚本）**不属于任何 Python 包**——setuptools
  从不打它们，源码树/release 树 CODE_ROOT 命中掩盖，wheel 形态
  CODE_ROOT=site-packages 父级必然落空：`POST /api/sessions` 在
  `render_claude_md` 处 `FileNotFoundError` 500（用户新装实测现场）。
  修复：`behavior_dirs` 增加包内搜索位（数据根覆盖 > 源码树 > 包内
  兜底，语义不变）；fetch 脚本同构回落；打包链扩 prompts/profiles/
  scripts（CI 构建时拷贝进 `loadn_webui/` + package-data）。
  验证：wheel 解包 5/5 在包；宿主 Python 3.10 与 3.12 双 throwaway
  venv：创建会话 200、CLAUDE.md 宪法生成（含包内 fetch 路径）、
  profiles 正常读取；**docker 干净环境（ubuntu:24.04）全流程终验
  通过**：UI HTML / 创建会话 200 / 宪法文件 OK；ruff 绿 + 82 passed
  （admin/init/projects/settings/w0）。

## [0.7.38] - 2026-10-21

- **功能：PyPI 包内嵌完整 Web UI**（此前 `pip install "loadn[webui]"`
  起服务为 api-only，`/` 仅返回提示 JSON）：前端 dist（约 1MB）打进
  wheel——package-data 收 `loadn_webui/frontend/dist`，release CI 的
  PyPI 发布 job 增加 npm build + 拷贝步骤；运行时 `_DIST` 双路解析：
  源码树 `CODE_ROOT/ui/dist` 优先（开发者与生产 release 树同此命中，
  行为零变化），包内 `loadn_webui/frontend/dist` 回落。干净 venv 装
  wheel 实测：`/` 返回完整 SPA HTML、静态资产 200（修复前 api-only）。
  w0 安全面 49 passed。

## [0.7.37] - 2026-10-21

- **修复：wheel 打包遗漏数据文件**：`[tool.setuptools.package-data]` 仅
  收 `py.typed`——`models.json`（模型目录：窗口/价格/cache economics，
  缺失则全降级保守 128k）、`skills.lock.json`（技能内容锁基线）、
  `protocol/manifest.json`（协议自描述）、`prompts/`（标题生成模板
  title.txt + 模型人设 models/*.md）**从未进过 wheel**——本地 editable
  安装直读源码完美掩盖，PyPI 用户全部受影响，且 **TUI 首屏整屏空白**
  （根因同此）。修复：package-data 补全 4 类路径。验证：本地
  `pip wheel` 解包 5/5 在包；docker 干净容器（ubuntu:24.04 + PyPI）
  force-reinstall 后文件级告警消失、TUI 首屏原生渲染恢复。
  （发现方法：v0.7.36 干净环境 CLI/TUI/webui 全流程复测）
- **文档（README 双语）**：extras 表加 TUI 注记——全屏 TUI 已在
  Python 3.10/3.11 验证；Python 3.12 伪终端环境（pexpect、`script(1)`、
  部分 CI runner）中 textual 8.x 输入框可能收不到键事件，基础 REPL
  （`LOADN_NO_TUI=1`）不受影响（外部依赖组合问题，经 textual 最小
  Input 世界复现 + 3.10 对照 + 8.x 全系版本排查定性，非本项目代码）。

## [0.7.36] - 2026-10-21

- **修复：新装实例 `/api/health` 500（config 裸键打穿默认实例）**：
  `loadn-web init` 模板的 `engines.loadn:` 为裸键（子项全注释、等用户
  填 key），YAML 解析为显式 null，`_apply_section` 直接 setattr 把
  dataclass `default_factory` 实例覆盖为 None → `resolve_bin` 取
  `CONFIG.engines.loadn.bin` 即 `AttributeError`——干净环境（docker/
  新服务器）按文档从 PyPI 安装后 health 必 500（生产配置完整故从未
  暴露；Ubuntu 24.04 容器全链实测发现）。修复：裸键（null）作用于
  嵌套 dataclass 字段时视为"未配置"，保留默认实例；标量字段 None
  语义不变。对赌 `tests/test_config_barekey.py` 4 例。
- **修复：`loadn setup` 非 tty stdin EOF 死循环**：docker exec/CI/
  管道场景下（`loadn setup < /dev/null`），端点选择循环把 EOFError
  当"无效选择"无限重问。修复：EOF/Ctrl-C 且无默认值可接受时优雅
  中止——exit 2 + 免交互指引（环境变量 / `$LOADN_HOME/config.json` /
  交互终端重试）；有默认值的问询保持"EOF=接受默认"（管道喂答案不受
  影响）。对赌 `test_onboard_stdin_eof_aborts_not_loops`。
- **文档（README 双语）**：安装节两坑扩三坑——裸 Ubuntu 服务器/容器
  24.04+ 不带 Python，补 `apt-get install python3 python3-venv`；部署
  平台节改双路径（PyPI 包 api-only——wheel 不含前端 dist、`loadn-web
  token show`、交互 API 文档 `/docs`；源码部署 `npm ci && npm run
  build` 后完整 Web UI）；onboarding 触发条件写准（仅交互终端；
  headless `-p`/非 tty 快速失败 exit 2）。
  （测试方法：ubuntu:24.04 干净容器 + PyPI 真源七场景实测）

## [0.7.35] - 2026-10-20

- **修复：组织架构页矮窗口下员工卡与召集面板重叠**：`.org-page` 复用
  `admin-body`（overflow-y:auto 的 flex 纵向容器），子项默认
  `flex-shrink:1`——窗口矮、内容总高超容器时 flex 按比例压缩子块，
  员工卡所在的 `.org-main`（带 min-height:0）被压塌，卡片溢出盒外，
  视觉上穿进下方「临时工召集」面板/池卡区域；窗口够高则不触发。修复：
  滚动容器子项禁收缩（`.admin-body > * { flex-shrink: 0 }`），正确
  语义=超出出滚动条而非压缩重叠；管理中心 12 个 tab 同用 admin-body，
  同构隐患一并修复，正常窗口布局零变化。验证：五档视口（1610×940/
  760/640、905×700、805×700）卡片×面板相交 14→0，矮窗口可滚动到底、
  召集面板完整可达。
- **文档与官网资产**：README 双语重写（正式专业语体、恢复品牌格言、
  安装节补 Python>=3.10 前提与 PEP 668 venv 指引、首屏图换虚拟项目
  实拍）；社交预览图 v2（格言版）；官网展示七图按 1920×1080 主流桌面
  视口 3840×2160@2x 重拍（loadn.net 官网独立部署，不随本包）。

## [0.7.34] - 2026-10-20

- **修复：TUI 通道自 textual 8.x 升级起全断**：`App.run()` 内部
  `asyncio.run()` 与 CLI 主事件循环嵌套即炸 RuntimeError——改
  `run_async()`+await 修复（CLI 真机 e2e 首跑暴露）。
- **修复：init 生成的 config.yaml 从未生效**：模板首行藏一个反斜杠
  转义字符，YAML 解析直接炸且被 `except YAMLError` 静默吞掉——新
  用户填什么配置都是默认值。模板修正。
- **修复：config.yaml 端点/密钥无生效路径**：`engines.loadn` 段的
  base_url/api_key 没人读。补全链路：`PerEngineConfig` 加字段 →
  spawn 注入 `LOADN_BASE_URL/LOADN_API_KEY` → 引擎 provider_config
  环境层消费。`loadn-web init --model/--base-url/--api-key` 三值
  落 config.yaml 全链实证（init → yaml → CONFIG → 引擎进程）。
- **CLI/TUI 真机 e2e 套件**：CLI 14 例（三格式/--model 进 init 事件/
  stdin 管道/session 锁/--resume/tools 全链/子命令）+TUI/REPL 6 例
  （pexpect 伪终端），归档 `tests/e2e/`。
- **CLI/TUI 首跑引导（onboarding）**：裸 `loadn` 交互模式此前零引导——
  未配置模型端点的新用户面对空输入框，发消息才在 build_agent 深处裸
  Traceback。新增 `loadn/cli/onboard.py`（对标 Claude Code 首跑体验）：
  - 首跑（无 marker 且端点/密钥有缺口）在 TUI/REPL 之前自动进终端
    问答：预设端点选单（GLM 开放平台/DeepSeek/Z.AI/Anthropic/Kimi/
    自定义 OpenAI 兼容），base_url/model 预填默认回车接受，api_key 走
    getpass 不回显；合并写 `$LOADN_HOME/config.json`（0600 原子写，
    既有键不碰）即刻生效于本次会话。fake provider（测试/回放通道）
    不触发。
  - `loadn setup` 子命令随时重进引导（已配置也重问）；`.onsite_done`
    marker 幂等——已引导过/已配置零打扰；headless `-p` 与非 tty 不
    触发（fail-closed 哲学与信任门一致）。
  - **零配置快速失败**：端点全空时启动即给「运行 loadn setup」指引并
    exit 2（原为 provider 构造处裸 Traceback；fake/扩展 provider 不受
    影响）。
  - TUI/REPL 欢迎行与 `/help` 带 `loadn setup` 指引。
  - 测试：单测 12 例（判定矩阵含 fake 豁免/合并写/交互全流程）+
    pexpect 真机 e2e 10 断言（归档 `tests/e2e/onboard_e2e.py`）；
    顺手清上一批 e2e 遗留 ruff 9 错（CI `ruff check .` 守卫）。

## [0.7.33] - 2026-10-20

- **数字员工能力三件套：工作职责召唤闭环 + 技能/资源绑定 + 员工记忆域**
  （用户需求「数字员工缺少工作职责、技能/资源绑定、记忆管理」）：
  - **召唤闭环**：Task 工具枚举描述此前只列员工名——模型不知什么活找
    谁；现带职责渲染（`方维正=法务部·法务顾问｜职责：合规审查…`，
    def description 截断放宽 80→160 字），按活找人一步到位。
  - **技能/资源绑定**：员工 def 新增 `skills`/`resources` frontmatter 键
    （DB rev16 同步两列；空=随会话全量）。派发该员工的子代理时注入
    「你的技能面/你的资源面」声明块（技能清单实时发现过滤；资源按
    svc→env 静态映射，secrets 不列）。软边界诚实设计：subagent 与主会话
    同 cwd 同进程，硬边界（独立 workspace）留 backlog。管理面：员工
    编辑抽屉 chip 多选（技能源=技能库、资源源=custom_services，未知名
    保存即拒），员工卡绑定徽章。
  - **员工记忆域**：memorystore 第三域 `e:<员工名>`（`memory/_emp/<slug>/`，
    git 版本化/护栏/LRU 与两域同协议）；员工 def 恒绑 `memory` 键，派发
    注入「岗位长期记忆」块（跨会话沉淀岗位惯例）；管理面：记忆页域列表
    自动出现（label=员工·名（头衔）），组织页员工卡新增「岗位记忆」抽屉
    （列表/新增/编辑/删除）。

## [0.7.32] - 2026-10-20

- **修复：聊天记录重载与实时渲染不一致**（用户报障「重载的样式/结构和
  实时不一样」）：重载回放此前缺三样（任务清单/插话卡/思考累计行从未
  落库，blocks_json 只存 text/thinking/tool 三类块）。修复：todos 快照
  块（TaskCreate/TaskUpdate/TodoWrite 每次变更落全量快照进时间线，回放
  取最后一块顶部渲染与 live 同位）；steer 块落库原位+撤回同步摘块；
  subagent_thinking 带 cum（回放收起行「已 Nk 字」）；思考卡默认态
  live/回放同构（主代理展开、重载不再一律收起）。
- **资源 Hub 设计稿重构 + custom_services v2**：资源 tab 全量重写
  （三子视图计数徽章/分类筛选+搜索/展示态卡片墙四态探测徽章/抽屉编辑
  器：结构化表单 ↔ Resource.md 深色编辑器双向实时同步/四模板预设/
  Harness 注入预览/导入 .md）；后端自定义资源结构化模型（多 config 字段
  env 名自定义注入、secrets vault_ref 绑定、prompt/分类/图标/开关），
  老条目完全向后兼容，纯凭证驱动（无端点）资源合法。
- **README 全面专业化**：植入 slogan「天行健，oldman，地势坤，loadn」；
  新增三大原创理念主段（Agent=Model+Harness+Resource / Digital
  Employee=Agent+Skills+Memory / Security=Sandbox+Control Points+Vault+
  Audit，每公式配生产级截图）；新构 4 张模拟数据演示截图（工作台/
  资源 Hub/抽屉/技能页）；移除论文关联段落（专注项目介绍）。

## [0.7.31] - 2026-10-19

- **修复：搜索框被自动填入账号密码**（Chrome autofill）：搜索框语义化
  `type=search`+`name=search`——对已保存登录凭据 `autocomplete=off`
  无效（Chromium 既定策略）；TokenGate 登录字段包真 `<form>`（密码
  管理器以表单聚类，凭据配对锁进表单不再溢出），顺带修用户名框
  Enter 不提交。
- **修复：切页回来插话/输出重复**（三根因）：SSE 断点游标提升为模块级
  跨连接持久（resync 重建连接游标归零→fresh 回放活跃 turn 尾窗叠进
  保留流水=主根因）；同 sid 双 EventSource 连接泄漏补「上岗关前任」；
  steer/tool_use 按 id 去重+Composer 同文本 400ms 去抖。
- **修复：超长消息 turn 直接报错**（E2BIG `spawn_failed [Errno 7]
  Argument list too long`）：三引擎 `['--', prompt]` 契约下用户消息
  全文是单个 argv 参数，超 Linux 128KB 上限 spawn 必炸；超 100KB 时
  摘出命令行改走 stdin 管道注入（三引擎 `-p` 原生支持）。
- **技能学习卡：交互重构 v4**（用户设计稿「智能技能学习」五改造点）：
  核心规则置顶可直接改（原来埋在预览正文里）+关键约束自动切分 ✓ 胶囊
  2 秒扫读可判提炼对错；学习来源（教导原话+上轮行为）折叠进抽屉双栏
  对比不再重复占屏+落盘路径一键复制；说明书小字收敛进 ? 悬浮气泡；
  「SKILL.md 源码」tab 直接编辑最终落盘文件且与可视化视图双向联动；
  按钮自解释（保存为工作区技能/仅本次生效不保存/误判）+决策后原地
  结果条；技能名前端同款校验非法即禁存；修 20s 轮询覆盖编辑中草稿。

## [0.7.30] - 2026-10-17

- **安全中心：设计语言全量重构**（用户设计稿对齐）：`.sh-*` 命名空间
  ——8 子 tab 白底容器+渐变 active+真实数据计数徽章 / hero 三态横幅
  （左缘色条+图标+语义徽章+warn-tip）/ 总览态势卡+7 大卡 hover 直达 /
  SaaS 表格 / 行式安全运维面（seg 分段策略组+纯 CSS 开关）/ 白名单
  胶囊池 / 凭证行内 8 字段抽屉 / 蜜罐空态大卡；全走双主题令牌。
- **技能建议卡：设计稿重构 + markdown 渲染**：hero 左缘渐变色条+kind
  语义徽章（教导=靛/纠错=琥珀）；SKILL.md 正文 react-markdown+GFM 渲染
  （标题/引用/列表/表格/行内码成真格式），「源码」模式逐字保真；
  frontmatter 拆行式网格；长文折叠改段落感知（不撕裂语法块）；修复
  决策成功反馈随卡片卸载永不可见→改全局 toast。
- **安全页：补全设计稿三交互件 + 修复待审清单真缺陷**：审批「一键
  清理已超时」（新端点 POST /api/admin/approvals/sweep，幂等+审计）；
  修 `GET /api/admin/approvals` 不过滤 status——已批准/已驳回/已执行
  的历史记录混入「待审清单」且 tab 徽章计数虚高，现读前惰性清扫+
  只回真正在等的；出口白名单池尾部手动添加框（热生效）；凭证
  「+ 新增凭证」（平台名输入+临时行+8 字段编辑，首存即建）。

## [0.7.29] - 2026-10-15

- **修复：发版后前端无感知**（用户反馈「前端没收到更新」）：
  「平台已更新 · 点击刷新」提示条自前身迁移以来只有 UI 没有触发逻辑
  （死 UI）——补 60s 轮询 `/api/health` 的 release.version 与首查基线
  比对，变化即亮提示；走认证封装（强制认证部署下裸请求会 401）。
  另：本部署面向的服务端此前已是新版（v0.7.28 的 no-store + 新资产
  hash 已核实），刷新页面即可拿到新前端。

## [0.7.28] - 2026-10-15

- **性能：每日维护任务框架**（数据增长无人值守治理）：凌晨低峰窗口
  （本地 03:00-06:00）+ kv 日游标自动执行一轮——messages 冷归档、WAL
  checkpoint、周度 VACUUM（删除行后的死空间真正回收磁盘；独立连接执行，
  独占拿不到自动留待下轮）；游标崩溃安全（全部完成后才推进，中途崩溃
  下轮幂等重跑）；管理面手动触发端点（dry_run / force_vacuum）。
- **性能：文件层滚动清理**：turn 调用日志 30 天滚动（backfill 对缺失
  文件已容错）、pages_cache 90 天、备份目录散文件 30 天——只删文件永不
  删目录；旧名库残留一次性清理端点（workdaddy 家族 395MB，默认 dry_run
  手动确认）；存储概览端点（var/ 各子树占用 + workspace top10）。
- **安全：audit 月表导出归档**：冻结月份账本导出 gzip 归档文件（全字段
  含哈希链 prev_hash/hash，离线可自行验链）；**只导不删**——账本 DROP
  断链风险留给链验证改造卡；每日维护自动执行 + 管理面手动端点。

## [0.7.27] - 2026-10-14

- **性能：会话列表热路径**（用户反馈「sessions 接口慢、随使用时间增长
  越来越卡」）：N+1 消灭——列表接口从每会话 2 次连接（2N+2 连接/请求）
  改单连接批量聚合；启用引擎已维护的 usage 物化字段（弃运行时重算）；
  连接初始化短路（每连接重跑 DDL/迁移改哨兵表集自愈）+ 关键索引补建
  （rev17：sessions.updated_at / jobs.session_id）；认证续期 TTL 半衰节流
  （高频轮询不再每请求写库）；前端轮询 5s→10s + 失焦暂停 + 回焦即刷。
- **性能：历史消息冷归档**（长会话消息表瘦身）：冷判定三重守卫（超期+
  无活跃 turn+无唤醒任务全过才动）；gzip 分片原子落盘（tmp+rename，
  mtime=0 可重现）+ kv 增量游标；**文件先落盘后删行的事务铁律**——归档
  文件是原文唯一新载体；回源端点（owner 门+坏分片可恢复报错）+ 前端
  折叠展开；管理端 dry_run 预检 + 审计留痕（行数/字节/sha256）。
- **技能页设计语言重构**（参考设计稿）：kicker 层次页头（技能库 ·
  SKILLS）+ 技能语义说明 banner + 四档筛选胶囊（全部/本地/市场/已禁用，
  带计数）+ 搜索框（按名/描述/译文过滤）+ 分层卡片（来源徽章语义色：
  本地绿/市场靛/锁校验失败警示，hover 上浮光晕，操作按钮化）+ 空态
  引导卡；功能逻辑全保留。

## [0.7.26] - 2026-10-14

- **12 条产品反馈全量落地**：
  - 下载文件不再全叫 "file"——根因=下载接口未带文件名头，后端补
    `Content-Disposition`（RFC5987 中文兼容）+ 前端显式传名双保险；
  - 任务列表首屏条数按视口高度自适应（1500px 高屏实测 20→42 条；
    病根=固定 20 条而非 CSS）；
  - 预览页加最大化按钮（Esc/再点退出，切文件自动退出）；
  - 基本信息长值（ID/skills/工作区）单行省略+展开按钮，可复制全文；
  - 分类未选图标时按名字智能匹配（代码→Code、数据→数据库…15 类）；
  - 工具表加说明列（Task=派子代理独立上下文执行子任务，只回传结果）；
  - 定时任务页：模板库独立 tab+搜索+状态筛选+列头排序+「上次执行」列，
    默认按最近执行时间倒序；
  - 零输入建任务不再叫「新任务」——标题取当前时间（如「10月14日
    12:00」）、目录用纯时间戳+随机后缀；显式命名不受影响；
  - 安全页子 tab 化——页内 pill tab：总览+七个功能区各一页（介绍头+
    具体内容），事件流归审计页，熔断条全 tab 常驻；
  - **模型库**（设置→引擎与模型）：多模型登记（端点/密钥/模型ID）、
    设默认（新会话自动联动）、一键应用（即刻切换引擎默认模型）；
    密钥只写不读；
  - 插话可撤回——发出后、注入前可点撤回；已注入诚实提示不可撤；
    （排队消息撤回与执行停止此前已备：排队区撤回钮/输入框停止钮）。
- **前后台风格统一**（用户反馈「后台管理页面和前台页面风格差异很大」）：
  量化审计定谳——前台设计语言（氛围光+渐变+kicker 标题+卡片阴影+
  primary 锚点）后台全无。管理页拉进同一语言：背景氛围光、kicker
  层次标题+随 tab 动态 h2、卡片底色+阴影 hover 升级、三个 tab 主
  CTA 升 primary 靛蓝。
- **desktop 真机联调四缺陷修复**（arm64/MAC 端到端实证发现）：
  vz.rs kernel-args→kernel-cmdline（vfkit≥0.6 flag 更名）；rootfs 预置
  vsock transport 自动加载（修 exec.service 鸡生蛋）；guest 侧 HTTP
  vsock 桥服务（socat VSOCK:8792→TCP）；CI vfkit 下载直链修复。

## [0.7.25] - 2026-10-14

- **修复：Chrome 把登录用户名自动填进筛选/搜索框**（用户实证「点后台
  tab 后任务列表被筛选 woldy」）：根因=全站输入框零 autocomplete 标注，
  Chrome 将登录框输过的用户名启发式 autofill 进切 tab 后新挂载的输入框
  （受控组件 onChange 被 autofill 触发=搜索框被填即成「筛选」）。修复：
  登录框语义标注（username / current-password / new-password——教浏览器
  该填的地方）+ 全站 119 处 input/select/textarea 补
  `autocomplete="off"`。
- **安全页全面类化**（用户反馈「交互乱没章法 / 布局不一致 / 文本框没
  美化」）：①态势导语行——卡片上方一句总览（「N 面全部正常」/「⚠ N 面
  告警需处理」），先总后分的章法；②详情面板统一一种容器（此前裸 div /
  setting-card / 再套 card 三种混用）；③操作区分组（校验账本与紧急
  停止视觉分离）+ 熔断激活横幅独立成条；④全部控件（TTL/端口/等待秒/
  多行列表/搜索）吃全局统一观感——与任务/技能等 tab 同底色/边框/
  圆角/聚焦态；⑤卡片 hover/选中/危险态全 CSS 化（退役 JS 副作用写法）；
  ⑥凭证编辑器网格与白名单 chip 行类化。验证：七卡逐张点开，布局重叠 0、
  控件全达标（≥24px 高+带样式）、类化断言全过。

## [0.7.24] - 2026-10-14

- **修复：任务列表按真实活动序**（用户实证「执行中的子任务应把项目组
  置顶，完成后回原位」）：项目组此前按 `projects.updated_at` 排序，但
  子任务排队/完成从不触碰该字段（实证项目 updated_at 停在三天前）——
  排队与完成两处联动 bump 父项目，纯标记 starred 不扰动排序；对赌
  测试三例（排队 bump / 完成再 bump / starred 不扰动）。
- **子代理文字卡渲染层收编**：上版 `subagent_text` 引擎链的前端精化
  ——聊天流「子N·人名」文字卡 live/回放两态、Agent 视角页文字时间线
  与工具卡穿插、归属色条；引擎静默空收尾兜底补 2 例测试；记忆页
  文案贴用户原话（「兄弟子代理」四行说明）。
- **图标风格统一**：五维审计（线条/填充/配色/圆角/透视）19 候选——
  Box/Layers 等距立体透视违规重绘为平面正视，圆角统一 rx 契约并固化
  注释（后续 picker 复用自动一致）；几何断言 20/20。
- **后台 UI 巡检五件**（生产实测 23 张截图+DOM 几何审计 29 项发现）：
  ①组织架构页贴边（根因=复用无 padding 的 admin-body 且未套外层）补
  边距，贴边 2→0；②「新建记忆/Webhook/技能」表单 flex 塌陷（webhook
  输入框被压到 13px 高）控件全宽+最小高度，实证 1018px 满宽/30px 高；
  ③安全页圆角令牌化（radius 8/6 混用→`var(--radius)`+公共类）；④两
  抽屉补 Esc 关闭（此前仅鼠标点遮罩/×）；⑤TokenGate 一次 401 弹全屏
  登录框后永不自动关（probe 只开不关、z999 遮挡全页）——认证恢复自动
  关框。

## [0.7.23] - 2026-10-13

- **修复：egress 隧道 CLOSE_WAIT 泄漏打满 fd**（生产事故实证）：任务侧
  断开后隧道挂在「双向 EOF 才收尾」上（上游 keepalive 不发 FIN），挂死
  的转发协程被回收销毁时，上游 socket 的关闭义务随之丢失——长时间运行
  积累上千个 CLOSE_WAIT 打满进程 fd 上限，表现为服务无响应+数据库
  打不开（两者均为 fd 耗尽症状）。三层收敛：单方向 EOF 即对上游
  半关（在途下行数据照常泵完）；空闲看门狗（300s 双向静默判死隧道）；
  try/finally 兜底——任何异常/取消路径必关两侧连接。
- **记忆分区**（用户反馈「管理中的记忆全是 p:hex 天书」延续）：管理页
  记忆只管**用户级（跨项目）**，顶部横幅指路——项目记忆去任务右侧边栏、
  数字员工记忆去组织架构页；任务右侧边栏·工作区拆**三子 tab**：
  文件 / 记忆（本工作区项目记忆域：列表/新建/删除/看正文）/ 历史
  （文件快照清单+一键回滚）；新端点 `GET /api/sessions/{sid}/memory`
  （会话属主收口），空项目域首建不再 404。
- **子代理文字输出**（用户反馈「subagent 只有思考和工具调用，没有文字
  输出」——病根：引擎侧 TextBlock 按「噪声不转发」整体吞掉）：
  `subagent_text` 即时转发（8k 单块截断）→ stream_json 透传 → 宿主
  归属派生（blocks+SSE 带「子N·人名」扩展、**不进主控正文**——不会
  变成主控说过的话）→ 前端按归属合并渲染；引擎零正文兜底扩到
  end_turn 静默空收尾（模型只想不写时注入收敛续轮逼出正文）。
- **数字员工职责与任务记录**（DW-6）：`duty` 职责说明字段（DB rev15，
  18 人种子全员有职责）——员工卡两行截断展示+编辑抽屉可改+注入 agent
  提示「职责：」节+进 def description（**模型选人时能看到谁负责什么**）；
  `GET /api/org/tasks` 双归属任务回查（`subagent_type=名` 点名派发 /
  `agent_name=名` 临时工出勤），员工卡/临时工卡新增「任务记录」入口
  （任务描述/所属会话/时间）。

## [0.7.22] - 2026-10-12

- **数字员工（Digital Workers）**：把子代理从「每次随机起名」升级为
  「有组织架构的拟人团队」——法务的事直接点名法务部员工：
  - **组织架构数据面**：DB rev14 三表（org_departments / org_employees /
    temp_pool）+ `/api/org` 10 端点（部门树/员工 CRUD/init/recruit/池）+
    审计 org 类型；空态一键初始化默认组织——**8 部门 18 人**（总经办/
    产品/研发/设计/法务/HR/运营/数据），每人 MBTI+技能标签+人设口头禅
    （顾长风 ENTJ「先说结论再说路径」、方维正 ESTJ「先把丑话说在前面」、
    陈默 INTJ「最好的代码是没有的代码」……名字全原创），init 幂等护栏；
  - **员工即 agent 定义**：员工 CRUD 热注入 SubagentManager——保存即落
    `$LOADN_HOME/agents/<名>.md`，引擎天然加载：`Task(subagent_type="方维正")`
    即点名派发，persona/MBTI/性格注入其 system 提示；编辑抽屉全字段
    （工具白名单/独立模型/人设/MBTI），停用开关=不注入，删除同步摘除；
  - **临时工池（召集）**：召集面板（1-8 人+要求一句话）→ LLM 按要求生成
    名字/MBTI/性格（失败自动本地姓名库兜底）→ 入池卡展示（空闲/出勤中/
    已退役，池 32 LRU）；未点名子代理**优先从池中取名**（FIFO），池空才
    回落旧固定人名库；
  - **前端**：`#/org` 组织架构页（IconRail 底部 Users 图标入口）——
    部门树+员工卡网格+编辑抽屉+召集面板。

## [0.7.21] - 2026-10-12

- **子代理思考可见性**（此前按设计不转发——多 agent 场景下子代理全程黑箱）：
  - **主控端低频转发**：subagent fwd_emit 聚合思考（20s/4k 字双阈值节流、
    单块 8k 截断、cum 全量累计、收尾冲刷）外发 `subagent_thinking` 事件，
    stream_json 透传（旧宿主静默跳过零影响）；引擎宿主派生归属
    （agent_id=子N）落 blocks_json + SSE thinking 事件；前端 ThinkingCard
    「子1 思考中… 已 12k 字」折叠卡（live 默认收起）+子N 徽标+归属色条；
  - **Agent 视角思考时间线**：AgentTab 视角页派发组插思考时间线（历史
    blocks + live 按归属过滤、与工具卡穿插）；
  - 顺手修产品缺陷：单发 TaskTool emit=None 全程黑箱——loop.run_turn 注入
    ctx.extras["emit"]，非 plan 并行路径同样转发。
- **技能建议卡透明化**（用户二连反馈：不知道要干啥/把什么改成什么/为什么
  改/有什么用/生效范围/存哪里/怎么编辑取消——一个都不敢保留）：
  四段式重写——①触发原因（引擎侧落盘被识别的原话 `trigger` 字段，卡片直接
  引用）；②将写入什么（最终 SKILL.md **全文预览**=与确认后落盘内容逐字一致；
  完整绝对路径+技能库副本+安全扫描说明+「确认前不落盘」）；③确认后会怎样
  （仅本工作区新会话自动加载/设置→技能可编辑禁用删除/删目录即失效）；
  ④按钮后果随行（确认写入/跳过=7 天抑制/这不是教导=仅清卡不记录/编辑内容，
  底部一行三按钮后果速览，成功提示带路径+管理入口）。人话描述优先，
  slug 机器命名只在路径中出现。

## [0.7.20] - 2026-10-10

- **技能建议卡三处交互缺陷修复**（用户实证：轮换会话发「升级」弹出
  纠错建议卡，但看不懂要固化成什么、什么场景会用——只能拒绝）：
  - **误判**：会话轮换恢复协议段（含「不要重做」）被纠错词表命中，
    协议文本被当成用户纠正——检测/指纹/正文一律先剥离协议段
    （固定头尾锚点）；
  - **零信息**：description「用户纠正的规则」占位 → 首句提炼；正文
    原话复读两遍 → 单次引用 ≤600 字+空上下文省小节+提炼句无增量省
    规则节；技能名连续 `--`/尾 `-` 合法化；
  - **拒绝语义**：误判无通道（拒绝=全文指纹 7 天抑制，同文再现
    概率≈0 无效且误导）——decide 增 misjudge 分支（清槽+审计，不记
    负样本），前端加「这不是教导」按钮+生效说明行（确认后写
    `.agents/skills/` 此后新会话自动加载）+超长正文折叠展开。
  - 文档：EXTENDING §3 补「用户教学自动建议（P12）」闭环小节，
    docs/assets/ 卡片截图。

## [0.7.19] - 2026-10-10

- **升级非空闲守卫**：`upgrade --require-idle` 严格模式——有活跃 turn 在
  preflight 后、DB 备份/资产重刷之前直接拒绝（零副作用退出，防误切）；
  `--wait-idle 0` 跳过等待且非 `--yes` 时 stderr 强警告（活跃 turn 清单+
  两档中断语义：直跑档收养续跑/沙箱档 die-with-parent 必断），不再静默
  强切。背景：DB 实证历史 18 个 turn 因 server_restart 中断，均为非空闲
  窗口静默跳过等待所致。

## [0.7.18] - 2026-10-10

- **四项交互批**：
  - **停止按钮防重复**：在途状态机（双击即拦/四通道恢复/SSE 终态回执
    确认后才解锁，30s 超时兜底）——引擎未停前可反复点击的问题消除；
  - **属性面板折叠**：七分区统一折叠件，低频分区（沙箱/外联/skills/
    MCP/历史）默认收起，开合态持久化（只记显式 toggle，升级改默认不被
    旧值钉死）；
  - **切换到主控**：Agent 筛选挂上时 AgentChips 常驻一键回主控退路；
  - **交付物/产出物联动筛选**：产物清单与工作区树同条件过滤（全部/
    主控/按子任务），tab 计数 n/total，切会话重置。

## [0.7.17] - 2026-10-10

- **属性面板专项重设计**（右侧栏 320px 窄容器布局与交互）：
  - 模型参数行单行化（label 窄列+输入弹性+↺ 条件渲染，原四件套必换行
    错落）+ 脏状态（● 未保存 / dirty 才可保存 / 成功 toast）；
  - 出口记录五列表 → 行卡（域名+操作首行 / 统计+判定+时刻次行）；
  - 长标识一键复制（会话 ID/引擎会话/工作区点 code 复制全文）；
  - 放行/授权收回失败补 toast（原吞错）；turns 明细补表头；
    details 折叠箭头样式化；沙箱节 h3→h4 统一。

## [0.7.16] - 2026-10-10

- **字号体感校准**：五档字号令牌基准整体上调一档（10-14 → 11-15px，
  +10%）——用户反馈浏览器 110% 缩放才舒适，折回令牌基准后缩放可回 100%。
  正文 12→13、主内容 13→14、辅助 11→12、微标 10→11、强调 14→15；
  body 基础字号与 .md 相对单位自动跟随；15px+ 标题结构字保持（层级
  对比不变）。

## [0.7.15] - 2026-10-10

- **管理中心全量优化**（三份走查报告 42 条 → 6 批收官 + backlog/留长专项，
  共 51 commit；0.7.9-0.7.14 为内部迭代未留条目）：
  - **权限与安全**：管理面读分级（九敏感面普通用户 403）+ role 裁剪
    （普通用户仅任务/定时/Webhook 三可见面）；vault 收敛唯一编辑面（八字段
    merge 编辑器，entry 端 deprecated 补审计）；审批驳回（admin 一键否决
    +TTL 剩余）；裁决端 sqlite3.Row 兼容修复（e2e 抓出 500）。
  - **导航与去重**：tab↔hash 双向同步 + 五组重排（能力·自动化·观测·治理·
    平台）；流量 tab 并入安全出口卡（12→11）；设置页资源卡/资源页 MCP 收敛
    只读跳转；台账双源审计视图（ops·audit·both + CSV 导出）。
  - **补缺**：系统 tab（版本/uptime/磁盘/调度器/备份触发+校验，409 防重入）；
    任务页服务端分页（过滤/排序下推 SQL）；成本时间窗 7/30/90 + by_owner
    按属主聚合（「(无主)」桶不丢数）。
  - **流程与视觉打磨**：cron 编辑回填+触发 diff 确认；脏状态保护（切 tab/
    返回拦截）；Webhook 补编辑（token 不再随改吊销）；全局 Toast；命令面板
    Ctrl+K + tab 栏 ARIA/方向键漫游；设计令牌收敛（双红归一/硬编码色清零/
    圆角四档/字号五档 CSS+TSX 284 处/等宽栈单源）；emoji→feather 线性图标
    统一；触屏热区；单滚轴列表容器；时间戳统一 MM-DD HH:MM。
  - **交互升级**：原生 confirm/alert 全量退役（35+25 处）——全局确认对话框
    （danger 红键/Esc/回车肌肉记忆）+ toast 反馈。
  - **可观测**：资源探测历史入库（ping_history，最近结果+24h 失败数常驻）；
    workspace 占用落库（sessions.size_bytes，sort=size SQL 下推修分页页内
    重排旧缺陷）+ 后台全量重算端点。
  - **schema**：SCHEMA_REV 13（rev12 ping_history 表；rev13 sessions 占用
    缓存双列）；API 契约快照 +4 端点。
  - **测试**：1593 passed（新增 17 例对赌：读面分级/分页翻页/ping 聚合/
    by_owner 全覆盖/size 排序全局序等）；ruff/build/lint 全绿。

## [0.7.7] - 2026-10-09

- **多 Agent 工作台**：子代理自动人名 + Task 卡 agent_name 归属、子任务
  标签页、派发卡、agent chips、产物按 agent 归属分组、分类图标
  （SCHEMA_REV=9）；webui 多 Agent 工作台前端（icon rail 空间侧栏）。

## [0.7.8] - 2026-10-09

- **循环熔断双修：同名连败守卫 + 死工具不上面**（第四起生产实证）：
  venv 缺 playwright 使 9 个 browser 工具调用必死，模型陷入
  browser_click 逐像素递增 50 连败的搅动循环（两 turn 手动停）。根因：
  ①LoopGuard 指纹=name+参数，参数搅动即指纹变化，计数永远重置守卫全程
  沉默；②browser_mcp tools/list 无能力探测，死工具照常广告=纯诱饵。
  修复：LoopGuard 新增同名连败维度（`LOOP_NAME_FAIL_LIMIT=8`——同名工具
  无论参数怎么换连续失败即硬打断，文案带最近错误与环境缺失指引；本名
  成功才清零、交错免疫）；browser_mcp 能力缺失时 tools/list 空表不广告
  （tools/call 兜底错误保留）。

## [0.7.6] - 2026-10-09

- **直跑档位 MCP 执行域门**（三起生产实证的结构性收口）：冷启动模型反复
  被 sandbox 容器 bash 的「在场感」吸进外置容器，把「容器里看不到宿主
  路径」误诊成「工具未注入/执行域降级」——第三起（2026-10-09）thinking
  引用宪法路标原文仍进容器，宣告 prompt 层防线（宪法 §2.5/轮换 anchor/
  平台更正）到顶。结构性门：`security.off_tier_mcp_disallow`（默认
  `["mcp__sandbox__sandbox_execute_bash"]`，fail-closed 校验）在 sandbox=off
  档写进会话 settings 的 deny——引擎 PermissionEngine 执行时回填
  「权限规则拒绝」把模型推回内建 Bash（与 claude CLI deny 语义同构，
  WebSearch/WebFetch 禁用的同机制先例）；隔离档不受影响。升级流程新增
  存量会话 settings 重刷扫描（单会话失败跳过不阻断）。三落点同步：
  .claude disallow / .loadn / .agent。

## [0.7.5] - 2026-10-08

- **修 upgrade healthcheck 无凭证必 401**（发版被卡实证）：token 生效的
  生产面裸 urllib 探测必 unauthorized（机生 var/server_token 14 天宽限期
  一过），曾致 v0.7.0/v0.7.3 升级误判失败连滚两级（新版本实际已在跑，
  只能手动切指针）。healthcheck 现带 Bearer 凭证：人配 server.token
  优先，回落机生 var/server_token。
- **轮换 anchor 补执行域路标**（生产实证续修）：v0.7.2 的宪法路标对轮换
  冷启动无效——context_inflation 轮换后模型无「内建 Bash 曾成功」的历史
  惯性，anchor 只说「去读盘」不说「在哪个域读」，任务第一步物化
  `mcp__sandbox__` 的 bash 在外置容器打转三轮、误诊「内建工具未注入」
  报无法自救（内建工具一直在工具面）。现轮换 anchor 复用宪法 §2.5 同源
  真源按档位分形渲染路标（直跑=宿主全机/隔离=平台沙箱，均点名 sandbox
  MCP 是另一个域），渲染失败降级纯台账指引不阻断。二道防线（ToolSearch
  物化域警告/跨域混淆探测）记 backlog。

## [0.7.4] - 2026-10-08

- 修 refresh_default_assets 的 PATHS 取值形态（dict 键索引——首版
  属性访问在生产炸 AttributeError 且被不阻断捕获掩盖）。

## [0.7.3] - 2026-10-08

- **数据根模板遮蔽根治**（生产实证续：v0.7.2 的执行环境节修复被数据根
  v0.6.x 旧模板拷贝遮蔽——behavior_dirs 数据根优先覆盖代码根，而部署
  期复制的默认拷贝从不跟随版本更新）：①升级流程新增
  refresh_default_assets——数据根 prompts 里与**任一历史 release 原版**
  逐字节相同的未定制拷贝自动跟随新版刷新，用户定制版（与全部历史版
  不同）原样保留②生产数据根旧模板已手动刷新（备份 .bak-v072）。

## [0.7.2] - 2026-10-08

- **会话宪法新增「执行环境与 Bash 路标」节**（生产实证修复）：直跑
  档位下模型曾选 `mcp__sandbox__*` 的 bash 进了**外置资源容器**
  （独立文件系统 /home/gem），找不到宿主代码仓——内建 Bash（宿主）与
  sandbox MCP（容器）是两个执行域，模型无从分辨。现按档位动态渲染：
  直跑=「内建 Bash 就在宿主全机；sandbox MCP 仅资源桥接，读本机代码
  绝不用它」；隔离=「内建 Bash 在沙箱内按挂载表可见；两个容器不同域，
  需宿主资源时声明需求停下」。存量会话不回填（宪法是脚手架期文件）。

## [0.7.1] - 2026-10-08

- **用户徽标归位**（UI 反馈）：登录后的用户身份/登出从右下角悬浮块
  移到**侧栏底部**（sidebar-foot，管理中心按钮上方）——悬浮形态反馈
  突兀；TokenGate 回归纯登录门职能。浏览器对赌：徽标在侧栏容器内+
  悬浮形态不存在。

## [0.7.0] - 2026-10-08

- **生产实证修复：非 ASCII token 头 500**（生产隧道链路
  ERR_HTTP2_PROTOCOL_ERROR 排查定位）：客户端存了含非 ASCII 的坏
  token（latin-1 高位字节经 h11 放行进 str）→ hmac.compare_digest 抛
  TypeError → 500，经 HTTP/2 隧道层（花生壳云端）表现为流 RST=浏览器
  报 ERR_HTTP2_PROTOCOL_ERROR 200 (OK)。修：三通道比较统一 utf-8/
  replace 编码 bytes（恒定耗时保持）；对赌：单元级坏 token 双通道
  （头/query）安全拒。排查结论：服务端三端点毫秒级完整 200、gzip/
  长度正确——隧道 RST 的直接诱因是 500 响应被云端转成协议错。
- **多用户批3：记忆域收口+渠道认领归属**：①记忆管理面属主收口
  （隔离缺口修复：domain key 直读无复核——user 域=全局知识库改 admin
  维护（普通用户 403）；p: 项目域经 hex→projects 反查（git 根探测
  结果缓存）属主判定，他人/无主域 404 不暴露；读写同门单点收口）
  ②渠道归属设计落地（渠道线程无 cookie——由认领表决定归属）：admin
  在渠道卡绑定表把 chat 认领给用户（PUT bindings/{chat_id}，审计
  入账）；认领后该 chat 经渠道 /new 建的会话自动落属主、换绑保留
  认领③share 铸链复核确认已收口（经会话属主门）；vault 维持 admin
  全管（按用户分级记 backlog）。对赌：user 域 403/404 语义区分、
  他人项目域 404、认领前后渠道会话归属断言；测试卫生（白名单还原/
  binding 清理/查询精确化）。
- **多用户批2：用户管理面+隔离收口**：①用户管理（admin）：
  /api/auth/users 列表（带活跃会话数）/建号/改角色/启停/重置密码——
  禁用即时踢下线（删全部会话）、不能禁用/降级自己（防锁死管理面）、
  全操作入审计；前端 SettingsTab「用户与账号」卡（非 admin 显示权限
  提示）②属主接线补齐：webhooks/projects/schedules/categories 四面
  （建号落主+列表过滤+patch/delete 属主 404——categories 补 owner_id
  列）③hooks/schedules 写面从 admin 双头降为普通面（多用户语义：
  普通用户自管自己的自动化；owner 复核在路由内；W0 钉版与 hooks
  认证测试同步更新）④审批 decide 属主复核（cookie 普通用户裁决他人
  会话的审批=404；Telegram 渠道线程 user=None 放行不变）⑤内置心跳
  job：普通用户可见可暂停（全员有意义）但删除需 admin。对赌：批2
  e2e（四资源过滤/越权裁决 404/禁用踢下线/防自禁）+W0 钉版 regen。
- **多用户账号密码登录与空间隔离（批1：认证内核+核心面）**：用户
  反馈「让用户记 token 不合理」——按方向评审定完整多用户+隔离。本批：
  ①users/auth_sessions 表（pbkdf2-sha256 260k 轮标准库哈希——依赖红线
  内）+sessions/projects/scheduled_jobs/webhooks/channel_bindings 加
  owner_id 列（migration）②登录/登出/安装向导/改密/me/status 端点：
  httpOnly SameSite cookie 会话（30 天滑动续期、UA 留痕、登录失败恒定
  耗时、审计 auth 全入账）③中间件双通道：cookie 会话优先（管理面要求
  admin 角色），token/宽限通道原语义不变（CLI/存量部署与全部既有测试
  零破坏——通道 user=None 时属主检查放行）④属主隔离核心面：
  _get_session_or_404 单点收口（五路由文件共用——越权=404 不暴露存在
  性）+会话列表按属主过滤+建会话落 owner；contextvars 贯穿 sync 路由
  线程池⑤setup 安装向导：首账号=admin 且存量数据自动归并（legacy
  owner NULL 行 claim）；账号体系建立后未登录访客主动弹登录门
  （auth_required——不靠等第一个 401）⑥前端 TokenGate 重写：安装向导/
  登录表单/已登录用户徽标（登出）+token 输入降级为「高级」折叠。
  e2e：内核全链（setup 归并/登录/列表过滤/越权 404/B 非管理面 403/
  登出失效）+浏览器登录门渲染与错误反馈。
- **浏览器级 e2e 补面 + 设定↔UI 全量审计补齐（9 项 UI 能力）**：
  ①新增 tests/e2e_ui/（playwright chromium headless 连真 uvicorn+fake
  引擎，零 token）三条旅程：SSE 流渲染+resync 快照消费不清空（六轮修
  A1 的运行时对赌）、审批卡实时出现不刷新（A2 对赌）、「始终允许·确切
  →策略落库（P10 新 UI 对赌）——前端从此有运行时验证面（此前仅 tsc
  +build，resync 清空这类纯运行时 bug 无测试可抓）②**P10 per-target
  三档补全 UI**（设定审计最大缺口：审批卡加「始终允许·确切/域名+动作/
  目标全放行」三键 + 安全中心「目标放行策略」管理卡——list/改档/删/
  手工建/skill targets 只读建议；此前全链零 UI）③**P12 技能建议卡**
  （SkillSuggestCard：引擎检测教学/纠错 → 会话内可编辑卡片 → 确认固化
  （过八类扫描）/拒绝（7 天抑制）——闭环此前断在决策端）④Telegram
  启停**热起轮询线程**（原勾选启用后实际收不到消息直到重启）⑤skills
  供应链锁状态露出（pinned/lock_ok 字段+徽标——「挂着但 agent 说没有」
  的不可见故障可见化）⑥记忆 draft「待确认」角标⑦渠道卡会话绑定表
  （chat↔会话+游标）⑧heartbeat 降频「降频中」徽标⑨routines 安装
  existing 幂等文案区分。审计确认已良好覆盖：egress 三态/白名单/授权、
  KILL_ALL、webhook、审计链、MCP、引擎切换、vault/成本/notify 等。
- **六轮复查批（前后端契约 diff+修复对抗复审，12 项）**：换三新方法论
  ——前端 ui/src 从未系统审查过 + 近六 commit 的修复本身是最大新 bug 面。
  ①**resync 空载荷清空会话**（Critical：删/改消息发 {}，前端当全量快照
  消费——另一 tab/断线回放路径把 messages/turns/artifacts 全清成空）→
  改带整包快照 ②**purge 实例会话级联删掉整个递归 job/内置心跳**（回填
  session_id 与 delete_session 级联的组合炸弹——清一个旧会话=日更任务/
  心跳静默消失）→ 级联前摘 new_session 类指针 ③**approval 事件从未被
  SSE 订阅**（agent 请求审批时卡片永不实时出现，冻结等裁决须刷新页面）
  ④**egress '*' 广播永不投递**（_subs.get('*') 恒空=数据流面板「SSE
  驱动不轮询」的设计从未生效）→ publish('*') fan-out 全订阅+前端放行
  广播 ⑤job_fired/interrupted_salvaged 补订阅（定时触发/重启补记账的
  即时反馈）⑥decideApproval 吞 ok:false（host 非法/并发窗口/非 pending
  时点击无反应零反馈）→ alert 真因 ⑦调度 claim 改 **compare-and-set**
  （due_jobs 读与 claim 写无锁——服务端循环与手动 tick 并发双 fire，
  fires 翻倍+双投递）⑧单次 job 投递失败置 paused+审计（原 claim 推
  +24h 后无告警面——「明天才补」且面板无异常）⑨upsert ON CONFLICT 真
  兜底（索引建置失败的库回落旧路径——注释宣称的 fallback 此前不存在）
  ⑩surrogate 漏网两处（add_message/scan_session——API JSON 体转义与
  rglob 文件名）⑪advance_boundary 锁结果检查（fail-closed 漏网实例）
  ⑫Telegram 投递拒绝回执（熔断期间消息静默消失）+前端 new_session
  回填实例链接放行。对赌新增 6 条（purge 保 job/广播 fan-out/resync
  快照/单次失败 paused/add_message 清洗）。
- **三轮 backlog 中优清偿批（9 项）**：①lone surrogate 三通道清洗
  （sanitize_text：transcript 两处写+session_events+外部命令钩子——原
  surrogateescape 非常规文件名经工具结果进入事件流即 UnicodeEncodeError：
  整 turn error/上下文丢失/PreToolUse 安全钩子 fail-open）②skills.create
  的 description 单行化（多行值注入 source:=供应链锁 fail-closed=skill
  被引擎拒索引自毁）③webui/引擎两套 frontmatter 解析器统一（原首键胜
  vs 后键覆盖——重复键时管理页与引擎注册名错位）④artifacts (session,
  path) 唯一索引+upsert 原子化 ON CONFLICT（原 SELECT→INSERT 竞态双行，
  mtime 更新丢失+摘要双计费；migration 先清存量重复行）⑤DELETE 单条
  消息级联置空 turns.message_id（生产 4 行悬挂）⑥_finish 启动即置
  closing：收尾窗口内到达的 steer 拒收回落排队（原落在 missed 快照与
  active.pop 之间=永久丢失）⑦收养闸挪到全局信号量外（原 gate 等待者
  空占 max_concurrent 槽=收养期间全引擎新 turn 冻结）+loop/done 防御
  （单例状态跨 loop 残留闸不再挂死）⑧submit 的事件发布失败不再让 turn
  卡死 queued（SSE 丢一条可接受，turn 永久排队不可）⑨记忆域锁超时
  fail-closed 补齐 edit/promote（remember/forget 之外的最后两处无锁
  RMW）+session_events 启动全局清扫（原只在该会话下一 turn 收尾时触发，
  长期不活跃会话存量永不收缩——生产 63% 超期）。**过程教训**：_migrate
  热路径加的每连接 DELETE+CREATE INDEX 写事务与高频事件写并发=写锁排队
  风暴（export 30s 超时实证）——索引建置加 sqlite_master 门禁后过。
  对赌新增 7 条（surrogate 两面/skills 单行+解析统一/upsert 原子/steer
  closing 拒收/消息级联/域锁 fail-closed）。
- **三轮 backlog 高优清偿批（5 项）**：①Telegram offset 持久化（kv
  落盘，重启从确认位续拉——原归零重放 24h 内全部 updates：消息双投、
  /new 重建会话重绑、旧会话孤儿化）②重启丢插话修复：salvage 现在读
  steer 文件——输出日志重放时按 steer 回执摘除已注入的，未送达的回队
  为新消息（原插话彻底丢失无提示）；turn 终态/salvage 后清 steer 文件
  （原跨 turn 无限累积）③KILL_ALL 全局熔断 fail-closed 到 submit 面
  （原只拦调度+已 lock 会话：应急制动期间聊天/API 对空闲会话照常驱动
  agent——端点宣称的「拒绝新任务」半开）④backup restore 三守卫：源
  须完成品（有 manifest）、主库 WAL 非空拒恢复（活库覆盖二次损坏）、
  预备份失败中止+rsync 返回码必查（原半恢复=新 DB 配旧工作区）⑤广播
  事件行（session_id='*'，egress 逐请求双写不挂 turn）按保留窗清理+
  hard_keep 兜底挂调度 tick（生产 2.5 万行无界增长，275MB 库主因）。
  对赌新增 5 条（offset 续拉/salvage 摘除+回队+清文件/熔断拒新+解除
  恢复/restore 三守卫/广播清窗口+兜底）。
- **三轮多方法论复查修复批（并发红线 6+高/中 5 项）**：故障注入/
  并发时序矩阵/序列化边界/重启幂等/生产数据逆向五路侦查——①**审批并发
  双写**（decide/consume 的 SELECT→UPDATE 无写事务无守卫：Telegram 轮询
  线程×webui 线程池双开时否决可盲写覆盖批准、同码可双消费=不可逆动作
  双执行）→ BEGIN IMMEDIATE 串行化 + UPDATE 带 status 守卫 + changes()
  核对②**审计哈希链并发分叉**（生产库实证 3 处两行 prev_hash 相同——
  SELECT 链尾与 INSERT 之间无锁）→ 进程内 threading.Lock + BEGIN
  IMMEDIATE 双保险③**heartbeat 空转计数是死代码**（_heartbeat_last_empty
  返回值被丢弃且 _hb_advance 无 count=True 调用点——三防②③从未生效，
  旧测试手工改 label 才绿）→ 真路径接线 + 实投回填 session_id + 低频档
  2h 探针自愈（防 ×3 后永久停投）+ 产出恢复高频④**调度 fire 半途失败
  20s 重投风暴**（settle 在副作用后：submit 抛/记账写失败时 due_at 留在
  过去，new_session 类每 20s 造一个孤儿会话）→ fire 入口先 claim 预推
  due_at⑤**记忆域锁超时后无锁 RMW**（与持锁方整文件互覆盖=丢条目/复活）
  → remember/forget fail-closed 放弃本次变更⑥**skill 供应链锁裸 RMW+
  非原子写**（并发丢条目=skill 被 fail-closed 拒索引；写一半截断=全部
  外部 skill 失索引）→ flock+进程锁+tmp/rename 原子写⑦edit_entry 单行化
  （#15 漏的编辑通道：多行 summary 使 frontmatter 提前闭合）+reason 同款
  ⑧prune_events 失败不再把 done 覆写成 error⑨审批过期清扫挂调度 tick
  （生产 23 行 pending 全超 TTL 僵尸；顺带补 decided_at）⑩用户消息 argv
  加 -- 终结符（单词消息 --version/-h 被 flag 劫持）⑪routine 安装幂等
  （双击/重发=每天双份推送）。对赌新增 12 条（并发三面：审批裁决/同码
  消费/审计链 8 线程；真路径空转计数 ×1→×2→×3 降频；claim 防风暴；
  8 线程锁不丢条目；sweep 幂等+decided_at；flag 不劫持；install 幂等）。
- **二轮全面分析修复批（高/中 13 项）**：致命批之后的高/中清剿——
  ⑥常规压缩点补反思调用（原只挂 overflow 自救路径，正常压缩的摘要
  从不进反思=P12 特性半残）⑦heartbeat cron 归正 7,37 双分钟点（"7,30"
  在本仓 cron 解析=单值每小时一次≠30min 档）⑧记忆注入单读共享
  （select_injected 调一次，hits_of+render_block 共用 picked——两读盘
  间隙后台抽取落盘会造成 memory_hits≠实际注入的 TOCTOU）⑨CLI _Patched
  属性委托（非下划线属性透传内层——诊断器探属性 AttributeError）⑩
  Telegram 回调**绑定校验**（审批须属于绑定到本 chat 的会话：原任一
  白名单 chat 可枚举小整数 aid 裁决他人审批；码路由到审批所属 sid 非
  当前绑定）⑪send_reply Markdown 失败降级纯文本重发（原走断线退避=
  游标不推进→同一失败行无限重试+队列头阻塞）⑫引擎注入 user 事件带
  engine:true（consolidate 不把截断 nudge/自检 gate 当用户教导触发
  建议卡）⑬内置心跳 job：PATCH 403 禁改（三防档位参数不可绕）+ 删除
  =kv 哨兵永久关闭（原删即重启复活）+ 去重键改恒定形态（is_system
  AND kind，原 label LIKE 用户可骗）⑭remember dup 原位更新返回自身
  条目（原 [-1] 挪尾后返回别的条目）⑮⑯summary/origin_session 写入
  前单行化（换行=frontmatter 串键/伪造溯源键）+ advance_boundary 入
  域锁（RMW 与写入并发丢 entries）⑰decide 并发窗口如实回执（重读
  真态 approved-race，不谎报失败）⑱executed 态不算否决⑲浏览器预算
  随 open 重置（新任务不吃上一任务剩额度）+ 冻结拒步不烧预算⑳回调
  answer 面 try 包裹+审计带 by ㉑notify_approval fetchall 多绑定全送
  ㉒MCP discover 降级分支 stop 再抛不炸㉓webhook 未知 token 恒定耗时
  比对+审计带来源 ip。对赌新增 11 条（system job 守卫三段/单读一致/
  dup 返回/frontmatter 单行/engine 标记/常规压缩反思/预算重置/ip
  留痕/绑定校验/Markdown 降级）。backlog：decide 端 admin 面授权、
  promote 跨域原子性、update_lock_entry 无锁、_fm_upsert 换行防御。
- **二轮全面分析修复批（致命 5 项）**：三路侦察 + 逐条实证后修——
  ①调度器 Row/dict 双态统一在 fire() **入口**（to_dict 原插在 KILL_ALL
  检查之后：:155 job.get("id") 对 Row 炸穿被 pass 吞掉→**kill 开关
  fail-open**；_heartbeat_last_empty 同款→heartbeat 20s 热循环永不投递；
  接线级对赌再抓回一次——Row 直传+KILL_ALL 双形态）②heartbeat ×N 语义
  归正：实投成功清零（原 _hb_advance(fired=True) 死代码，3 次忙跳过后
  **永久停投**）、忙跳过/降频不计数（忙≠无产出）、label 解析带 try、
  降频后 cron 同步进本地 dict ③**后台任务排空**（fire-and-forget 三连
  在 -p 模式 asyncio.run 收尾被取消——平台 webui 主路径上记忆抽取/P12
  检测从未写完过）：_spawn_bg 登记防 GC + drain_bg（30s 超时兜底）挂
  _shutdown_bundle ④P12 库副本**先扫后写**（原顺序红线内容留在平台
  技能库任意后续会话可挂载执行）+ 同名落位用指纹后缀循环（-taught 二次
  冲突静默覆盖）+ description/origin 单行化（frontmatter 换行注入）⑤
  ws_of 统一 DB 感知（P7 sources/P12 suggest/P13 票三处原用
  workspace/<sid> 拼接——**项目子任务会话全错位**：sources 恒 deleted、
  固化 404、票永不生效）；.mcp.json SID 注入改 DB 反查；票写失败留痕。
  测试侧：decide 类测试隔离平台技能库根到 tmp（原直写真实 skills/——
  污染+跨 run 409）；新增 Row 生产路径/KILL_ALL 翻转/库红线零残留/
  drain 排空四条接线级对赌。
- **P1-P13 全面复查修复批**：修复生产路径三严重 bug——①heartbeat 三防
  是死代码（fire() 不路由 is_system job，生产从未执行忙跳过/降频；修路由
  + 经 fire() 的接线级对赌）②P12 教学检测在带工具的 turn 恒漏检（users[-1]
  恒为 tool_result；过滤非人话块）③P7/P8 项目域记忆 hits 在 sources/chips
  恒误报「已删除」（域枚举名≠目录键；按会话项目域目录解析）。设计缺口
  四项——④P13 敏感冻结补全「单步放行」三层（.mcp.json 注 SID env→审批
  批准落一次性票文件→冻结处验票消费；此前审批从不建、文案对模型说谎）
  ⑤P12 技能确认改双写（会话工作区立即生效 + 平台技能库跨会话持久——
  原只写工作区，「下次自动启用」跨会话不成立）⑥P5 promote() manifest
  读改写入域锁（并发丢更新残留点）⑦P12 pending 建议卡单槽保护（未决策
  不被覆盖）。过程又抓一 bug：sqlite3.Row 无 .get()——fire() 新增路由
  对全部调度 job 抛 AttributeError（7 测试红出）；测试隔离修复（busy
  检查全局语义下跨文件残留 running turn 串扰）。中项留 backlog（P13
  预算进程级语义/敏感正则过宽可配、P9 回信 25s 延迟、P3 token 入 URL
  日志面、P6 frontmatter 换行值、P12 双通道同捕）。
- **视觉 GUI 工具层（P13，CUA 兜底）**：browser_mcp 扩四纯视觉工具——
  browser_screenshot（viewport png→base64 vision block，**不注 DOM 信息**）、
  browser_click(x,y)/browser_type(text)/browser_scroll(dy)（坐标/键盘，
  无 CSS 选择器）。**敏感冻结**（确定性、模型之外）：动作前 URL 命中
  支付/登录/验证码等模式（SENSITIVE_URL_RE 可配正则族）→ 拒本步 +
  approve.create 审批请求 + 通过后仅放行该单步（下一步重新冻结——
  连续两步敏感操作永不自动放行）；screenshot 只读不冻结（能看不能动）。
  **预算**：screenshot≤20/click≤30（超限 RuntimeError 终止汇报）；步间隔
  ≥800ms 节流。**自纠**：click 后自动补 screenshot 回图供模型验证。
  审计 browser_cua（坐标/域名/动作）全入账本；截图不留存。四工具经既有
  stdio MCP server tools/list 面注册（引擎 .mcp.json 即得）。fake
  playwright 页面测试四件全绿；真机静态页「截图→点→验证」与支付页冻结
  手测未做（无 CDP 沙箱环境）。
- **经验→技能固化闭环（P12，本批主战场）**：纠正/教学一次→沉淀可复用
  技能。**纠错信号检测**（确定性词表挂 turn 收尾，禁模型猜常开）：显式
  教学（以后都/记住要/always/never…）与否定纠错（不对/错了/重做…）两类
  才触发；防自激每会话 ≤2 次。**建议卡**：命中写会话工作区
  .loadn/skill-suggest.json（预填名/描述/正文=用户原话+上轮摘要，不改写
  语义），webui 卡片可编辑；确认→写项目 .agents/skills/（**过 W4 八类
  供应链扫描**，红线拒写；origin: user-taught 戳——自备内容不进 P0-3
  锁，实测 source 字段会撞锁拒索引）；拒绝→负样本（同类指纹 7 天抑制，
  引擎侧同文件）。**压缩后反思**（默认 off，env
  LOADN_REFLECT_AFTER_COMPACT）：cheap 通道读压缩摘要 → ≤3 条操作教训 →
  项目记忆域 **draft 条目**（带角标待确认——P6 记忆页编辑保存即转正，
  绝不自动落盘）；反思不触发纠错检测（防自激）。全链路审计 type=
  consolidate（accepted/rejected/scan_rejected/lesson_confirmed）。
  架构：顶层 loadn/consolidate.py（memorystore 先例，引擎 loop 与 webui
  决策面共用，进程边界安全）。手测真实纠正→存技能→新会话命中未做
  （loop 级触发/接受/索引可见/拒绝抑制/反思 draft 全链 API+引擎对赌）。
- **Heartbeat 巡检 + Routine 模板包（P11）**：schedule 加 destination
  三档（dashboard 默认零改 / notify 推送 / notify+artifact 推送+确保
  会话 artifacts 目录并附路径）。系统级内置 heartbeat（🫀 is_system=1，
  30min cron、assistant 档、可删即关）：三防——主队列忙跳过本轮、
  连续空轮计数编 label 尾标（实投清零）、连续 3 轮无产出自动降频 30min→2h
  （🫀·low 前缀+审计，面板可查）。routine 模板包 5 例（晨报/资讯论文
  巡检/凭证预算体检/日程提醒/仓库日报），调度页「模板库」：Ready 一键
  启用 / Needs setup 显示缺什么+去配置链接；**安装=复制为用户 schedule**
  （is_system=0，与平台升级解耦）。SCHEMA_REV 8（destination/is_system
  两列 additive）。手测空转心跳未做（三防+安装 API 全对赌）。
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

- **内置心跳 job 的面板适配**：PATCH 对 system job 放行 status-only
  请求（「停用走暂停」兑现——暂停心跳与永久关闭是两档能力，夹带其他
  字段仍 403 fail-closed）；前端排程面板对内置 job 隐藏编辑入口、删除
  确认提示「永久关闭，重启不重建」。

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
