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

不动代码。解析序（`loadn` 引擎）：

1. `$LOADN_HOME/config.json`
2. env：`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `LOADN_MODEL`
   / `LOADN_PROVIDER`（`anthropic` | `openai` | `fake`）
3. `~/.claude/settings.json` 的 env 段（已有网关配置原样复用）

Anthropic 形网关（任何兼容端点）与 OpenAI 兼容端点（vLLM/LiteLLM/…）
都原生支持；`fake` provider 用于零 token 测试。加全新协议族则在
`loadn/providers/` 加一个文件（参考 `openai.py` 的适配层写法）。

## 3. 写一个 Skill（最常用的扩展）

Skill = 目录 + `SKILL.md`（frontmatter `name` + `description`，正文是
给 AI 的操作手册）。放置位置任一：

- 项目内：`.claude/skills/` / `.agent/skills/`（随仓库走）
- 用户级：`$LOADN_HOME/skills`（引擎侧全局）
- 平台级：数据根 `skills/`（webui 管理中心可视化管理、可安装/上传）

私有 skill 不进仓库：设 `LOADN_SKILLS_EXTRA=/path/to/dir` 即整目录
overlay 进平台（开源部署与私有资产分离的设计）。
安装第三方 skill 走管理中心——先过供应链扫描（W4 八类检查），能力
声明会展示给用户。

## 4. 接外部工具服务（MCP）

项目根 `.mcp.json` 声明 stdio server，工具以 `mcp__<server>__<tool>`
出现在引擎工具面。首次使用按哈希锁定（防替换）。平台侧的外部资源
（OCR/浏览器沙箱/短信/邮箱…）在 config.yaml `resources:` 配端点与密钥
——密钥只进 config.yaml，永不入库。

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
egress_allow 白名单）、`resources`（外部资源端点与密钥）、`notify`
（bark/Server酱/Telegram 运维通知）、`pricing`（成本页价目覆盖）。
字段语义见 `loadn_webui/config.py` 各 dataclass 的注释（默认值均为
通用值；个人部署的私有端点写在自己的 config.yaml 里，不回传上游）。

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
