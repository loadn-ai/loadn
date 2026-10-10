# Roadmap

> 方向按主题分组，不承诺时间；勾选状态即当前事实。想认领的项欢迎开
> issue 认领（标注 roadmap 项名）。质量红线见 [CONTRIBUTING](CONTRIBUTING.md)
> §断言强度——所有新功能 PR 都适用。

## 已达成（v0.4 → v0.7.x + 测试战役）

- [x] 开源就绪：可移植默认值、引擎插件 entry point、社区文件、零 lint 债
- [x] 任务隔离与断线回放：项目子任务独立目录、轮换 anchor、崩溃补记账
- [x] 交互式出口管控：egress 三态 + 弹卡确认 + 会话级放开
- [x] 全量显式配置化 + 宿主机资源桥接（v0.6.5）
- [x] 测试质量战役：83% 行覆盖 + 47 文件 4055 变异 78% 杀伤
  （M0-M6 41 文件 + M7 平台面 10 文件）+ 周跑突变回归门
- [x] 运维加固（v0.7.x）：非空闲升级守卫、技能教学建议闭环、
  管理中心重设计

## 发布与分发

- [ ] **PyPI 首发**（`pip install loadn` 兑现；tag→build→twine 自动化）
- [ ] desktop 真机验证（mac/win 引导与迭代——构建链在 CI，缺真机回归）
- [ ] 文档站（GitHub Pages：README/ARCHITECTURE/CONFIG/EXTENDING 汇编）

## 质量长尾

- [ ] M7 突变批收尾：browser/lsp/codemode/ext 与 resources.py 外部端点面
      （routes/settings_admin/workspace/params、mcp 家族、engines、backup
      已于 2026-09-28 完成；余下文件从未做断言强度验证——欢迎认领，
      方法见 TEST-PLAN §七）
- [ ] 子进程覆盖盲区（COVERAGE_PROCESS_START）：83→85%+
- [ ] 性能基准门：启动/turn 延迟/内存的回归防线
- [ ] scheduler 时序 flake 根治（全量跑偶发 1-2 例，复跑绿）

## 能力扩展

- [ ] Ollama provider、DeepSeek 原生
- [ ] Terminal-Bench 基线数字
- [ ] TUI（textual）
- [ ] MCP server 模式（loadn 作为 MCP server 被别的 agent 驱动）

## 历史版本线说明

引擎线 0.1→0.7.1（2026-09-22 止）并入平台线（v0.6.5 起统一版本线，
现 v0.7.x），详见 [CHANGELOG](CHANGELOG.md) 分界标注。
