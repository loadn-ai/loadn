# 贡献指南

感谢愿意贡献。loadn 是一个 monorepo（引擎 + 平台 + 前端），新贡献请
守住两条底线：**契约稳定**（引擎方言/安全语义不悄悄变）与**默认值通用**
（个人端点/密钥/内网地址不进代码，写进自己的 config.yaml）。

## 开发环境

```bash
git clone <repo> && cd loadn
pip install -e ".[dev]"          # 引擎 + 测试工具
pytest                           # 全绿再提 PR（fake provider，零 token）

# 平台侧（改 loadn_webui / ui 才需要）
pip install -e ".[webui,dev]"
pytest -q                        # webui 测试自动包含
cd ui && npm install && npm run build   # 改前端必跑（dist 随 release 发布）
```

CI（.github/workflows/ci.yml）跑同样内容：ruff + 3.10-3.12 矩阵 +
gitleaks + 覆盖率门禁（83%，只升不降）+ 前端构建 + evals 场景门（nightly）+
突变杀伤率门（weekly，8 文件子集）。本地全绿 ≈ CI 全绿。

## 仓库结构与扩展点

先读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（三个平面/数据布局）
与 [docs/EXTENDING.md](docs/EXTENDING.md)（全部定制缝的地图）。
速查——一文件一扩展点：

| 要加什么 | 动哪里 |
|---|---|
| 新工具 | `loadn/tools/<name>.py` 实现 `Tool` 基类（自动注册） |
| 新 provider | `loadn/providers/<name>.py` 实现 `chat()` chunk 流 |
| 新钩子事件 | `loadn/core/hooks.py`（Pre/PostToolUse/Stop/Session* 已有骨架） |
| 新引擎 | `loadn_webui/engines/` EngineSpec（外部包走 entry point，见 EXTENDING §1） |
| 新 skill | `skills/<name>/SKILL.md`（私有的放 `$LOADN_SKILLS_EXTRA` 目录） |
| 角色档案 | `profiles/<name>.yaml` |
| 工作区模板 | `prompts/` |
| 新 API 端点 | `loadn_webui/api/routes/<域>.py`（sessions/admin/…十二域，__init__ 自动聚合）+ 快照 regen：`python tests/contract/test_api_surface.py` |

## 代码约定

- Python **3.10+**（禁 3.11+ 专有语法：`asyncio.TaskGroup`/`except*`/`typing.Self`）
- 文件顶部 `from __future__ import annotations`；模块 docstring 说明职责
- 数值阈值进 `constants.py`；引擎纪律常量不散落业务代码
- 工具错误是特性：抛 `ToolError` 回填让模型自救，绝不炸穿循环
- 平台代码不 import 引擎内部（架构铁律，tests/architecture 有对赌）

## 测试要求

- 新分支必须有用例；网络相关一律 MockTransport/控制文件，CI 不碰真端点
- 改协议（docs/PROTOCOL.md）须 bump 契约版本并过 `tests/contract/`
- 改安全面（W0-W6）须过 `tests/security/` 并同步 docs/ATTACK_SURFACE.md
- 沙箱×hook 之类组合路径只有 e2e 能抓——动了 sandbox/egress 请跑
  `tests/e2e/`

### 断言强度（比覆盖更严的一层）

本项目用突变测试验证「断言真能杀死 bug」而非只跑到代码（全量 41 文件
2589 变异 76% 杀伤，见 tests/TEST-PLAN.md §七）。贡献时的三条实用纪律：

1. **新守卫必配否定路径对赌**：加了 if 门/白名单/fail-closed 判断，至少
   一个「不该通过」的用例断言拒绝形态——2589 变异实证正向路径测得再全，
   守卫反转照样全存活
2. **新模块登记突变映射**：`scripts/mutate.py` 的 `TARGET_TESTS` 加一行
   （文件→窄测试集），否则突变验证永远够不着它
3. **功能性改动收尾跑窄集突变**（可选但推荐）：
   `.venv/bin/python scripts/mutate.py --files <你改的文件>` ——杀伤率
   <50% 说明测试是摆设，先补断言再提 PR；存活先对照 TEST-PLAN §七
   等价白名单分类

断言写法两条教训：`in str(e)` 子串断言会被整帧原文骗过（须排除回显
噪声）；守卫 `or→and` 类缺陷只有「真值非串」等区分形态能杀死。

## 提交流程

1. fork → 分支（`feat-xxx` / `fix-xxx`）→ 提交（中文/英文均可，说明 why）
2. `pytest` + `ruff check .` 全绿；改前端加 `cd ui && npm run build`
3. PR 按模板自检（影响面/验证方式/契约与安全声明）

## 发布

维护者流程：commit → `pytest` 全绿 → tag `vX.Y.Z` →
`loadn-web release build --tag` → `loadn-web upgrade`（详见
docs/RELEASE.md，含自动回滚）。

## 安全漏洞

勿开公开 issue——见 [SECURITY.md](SECURITY.md) 的报告通道。
