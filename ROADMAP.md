# Roadmap

> 方向按主题分组，不承诺时间；勾选状态即当前事实。想认领的项欢迎开
> issue 认领（标注 roadmap 项名）。质量红线见 [CONTRIBUTING](CONTRIBUTING.md)
> §断言强度——所有新功能 PR 都适用。

## 已达成（v0.4 → v0.6.5 + 测试战役）

- [x] 开源就绪：可移植默认值、引擎插件 entry point、社区文件、零 lint 债
- [x] 任务隔离与断线回放：项目子任务独立目录、轮换 anchor、崩溃补记账
- [x] 交互式出口管控：egress 三态 + 弹卡确认 + 会话级放开
- [x] 全量显式配置化 + 宿主机资源桥接（v0.6.5）
- [x] 测试质量战役：83.1% 行覆盖 + 41 文件 2589 变异 76% 杀伤 +
  周跑突变回归门

## 发布与分发

- [ ] **PyPI 首发**（`pip install loadn` 兑现；tag→build→twine 自动化）
- [ ] desktop 真机验证（mac/win 引导与迭代——构建链在 CI，缺真机回归）
- [ ] 文档站（GitHub Pages：README/ARCHITECTURE/CONFIG/EXTENDING 汇编）

## 质量长尾

- [ ] M7 突变批：webui routes/settings_admin/workspace/params、mcp client、
      browser/lsp/codemode/ext、engines 适配器、backup（~20 文件从未做
      断言强度验证——欢迎认领，方法见 TEST-PLAN §七）
- [ ] 子进程覆盖盲区（COVERAGE_PROCESS_START）：83→85%+
- [ ] 性能基准门：启动/turn 延迟/内存的回归防线
- [ ] scheduler 时序 flake 根治（全量跑偶发 1-2 例，复跑绿）

## 能力扩展

- [ ] Ollama provider、DeepSeek 原生
- [ ] Terminal-Bench 基线数字
- [ ] TUI（textual）
- [ ] MCP server 模式（loadn 作为 MCP server 被别的 agent 驱动）

## 历史版本线说明

引擎线 0.1→0.7.1（2026-09-22 止）并入平台线 0.1→（现 v0.6.5），
详见 [CHANGELOG](CHANGELOG.md) 分界标注。
