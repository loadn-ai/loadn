# 贡献指南

感谢愿意贡献！loadn 刻意保持小而清晰（~7k 行，一个运行时依赖），
新贡献请守住这两条。

## 开发环境

```bash
git clone <repo> && cd loadn
pip install -e ".[dev]"
pytest            # 全绿再提 PR（零 token，无需 API key）
ruff check .
```

## 代码约定

- Python **3.10+**（禁 3.11 语法：`asyncio.TaskGroup`/`except*`/`typing.Self`）
- 文件顶部 `from __future__ import annotations`；模块 docstring 说明职责
- 数值阈值一律进 `constants.py`，不在业务代码里散落魔法数
- 工具错误是特性：抛 `ToolError` 回填让模型自救，绝不炸穿循环

## 一文件一扩展点

| 要加什么 | 动哪里 |
|---|---|
| 新工具 | `loadn/tools/<name>.py` 实现 `Tool` 基类（自动注册），纪律常量进 constants |
| 新 provider | `loadn/providers/<name>.py` 实现 `chat()` chunk 流（契约见 `providers/__init__.py`） |
| 新钩子事件 | `core/hooks.py`（Pre/PostToolUse/Stop/Session* 已有骨架） |

测试要求：新分支必须有用例；网络相关一律 MockTransport/控制文件，CI 不碰真端点。

## 提交流程

1. fork → 分支（`feat-xxx` / `fix-xxx`）→ 提交（中文/英文均可，说明 why）
2. `pytest` + `ruff check .` 全绿
3. PR 描述带：改动动机 / 验证方式 / 是否破坏契约（stream-json 事件形状是
   **对外硬契约**，改动需在 PR 里单列说明）

## 安全

漏洞不要开公开 issue——见 [SECURITY.md](SECURITY.md)。
