# 测试全面化计划（2026-09-25 生成 · 用户指令：全量用例→拆任务→轮询迭代）

> 基线：`pytest --cov=loadn --cov=loadn_webui` = **78.8%**（14092/17884 行），
> 1094 passed。目标：总覆盖 ≥85%，零覆盖模块清零，契约面对赌补全，
> E2E 场景 5→10。迭代纪律与实现队列同（一会话一卡、测试全绿、
> 勾台账、commit+merge+push）。

## 一、覆盖率缺口清单（实测 2026-09-25，升序）

### 零覆盖（死区，优先清零）
| 文件 | 缺失行 | 根因 | 策略 |
|---|---|---|---|
| loadn/cli/main.py | 208 | test_e2e_cli 走子进程（coverage 不追） | build_parser/main 分派进程内单测 + 关键路径子进程断言 |
| loadn/cli/repl.py | 87 | 交互 REPL 无测 | 指令面（/undo /todos /compact /resume /fork）函数级测 |
| loadn/transport/daemon.py | 128 | test_daemon 走子进程 | 协议处理函数进程内测（auth/turn/resume 分支） |
| loadn/transport/bridge.py | 53 | 同上 | read1 循环/stdio 中继进程内测 |
| loadn_webui/backup.py | 185 | **零测试+数据安全最高风险** | 备份/恢复/轮转/损坏降级全单测 |
| */__main__.py ×2 | 6 | 入口三行 | 归并到对应 CLI 卡 |

### 低覆盖（<70%，按风险×ROI 排序）
| 文件 | 覆盖 | 缺失 | 补什么 |
|---|---|---|---|
| browser_mcp.py | 31.5% | 98 | 域策略分支/协议降级/screenshot 路径 |
| ops.py | 40.0% | 314 | release build/upgrade/rollback 状态机（进程内可测段） |
| exporter/md_to_docx.py | 44.7% | 78 | 边表/样式/图片分支单测 |
| titlegen.py | 46.8% | 25 | 触发条件/失败降级 |
| webui/cli.py | 49.0% | 419 | r 子命令面分派单测（不真跑外部效应） |
| skillhub.py | 50.0% | 23 | 目录解析/安装路径 |
| mcp/client.py | 63.7% | 81 | stdio 帧错误/重连/http oauth 分支 |
| core/hooks.py | 64.4% | 31 | 外部命令钩子 exit2/stdout 改写/超时 |
| lsp_host.py | 66.5% | 75 | 帧坏流/进程死亡重拉/截断 |
| resources.py | 67.2% | 235 | ping 降级/端点缺配/各资源分支 |
| codemode_mcp.py | 68.4% | 42 | AST 边角节点/超时 kill |
| api/sse.py | 70.5% | 23 | 断线重连 Last-Event-ID 回放 |
| core/session.py | 72.8% | 22 | fork/resume 边角 |
| repomap.py | 77.2% | 28 | 优先队列裁剪/mentioned 提权 |

## 二、契约面缺口（tests/contract/）
- `permission_result` / `tool_use_failure`：仅 manifest 单元断言，**无真跑对赌**
- `permission_request` durable 落盘（P2-2 选择性落盘）无验证
- todos/plan/stream_event 事件只有类型白名单断言，无 shape 对赌
- turn 级 diff（P3-1 result.diffs）无契约面

## 三、E2E 场景扩展（evals/ 5→10，P3-2 卡面原定 10）
现有：edit-basic / edit-guard / multiedit-atomic / todo-tracking / write-new-file
补：**断线重连 resume 接力 / egress 审批放行后重试 / 压缩后接力
（compact→新任务）/ 子代理并行归并 / 消息编辑改写轮**。

## 四、测试规则完善（T1，全局先做）
1. pyproject `addopts = ["--strict-markers"]`（防 marker 拼写漂移）
2. marker 对齐：`engine.codemode` 入 taxonomy；`sec.e3`/`engine.turndiff`/
   `engine.memory`/`engine.repair`/`engine.warmer` 补 pytestmark
3. CI：loadn 包覆盖率 job（`--cov=loadn --cov-report=xml` + fail-under
   从 78 起步每卡收紧）；taxonomy profiles（smoke-ci/nightly）从纯文档
   变可执行 marker 档
4. conftest：`_trust_tmp_workspaces` 语义注释固化；subprocess coverage
   追踪（COVERAGE_PROCESS_START）若轻量可行

## 五、任务卡（轮询迭代队列；一张一会话）
- [ ] T1 测试规则完善 + marker/taxonomy/CI 覆盖率门禁
- [ ] T2 backup.py 全单测（0%→90%：备份/恢复/轮转/损坏降级）
- [ ] T3 契约 v2 对赌补全（permission_result/tool_use_failure/durable/
      todos-shape/diffs）
- [ ] T4 引擎 CLI/REPL/transport 清零（main/repl/daemon/bridge 进程内测）
- [ ] T5 browser_mcp 补测（31.5%→70%+）
- [ ] T6 mcp-client/hooks/lsp_host/codemode 补测（64-68%→80%+）
- [ ] T7 evals 场景 5→10（断线/审批重试/压缩接力/子代理/消息编辑）
- [ ] T8 exporter(md_to_docx)/titlegen/skillhub 单测
- [ ] T9 session/sse/repomap/resources 补测（70-77%→82%+）
- [ ] T10 覆盖率门禁收口（总 ≥85% 或分域门禁）+ ops.py 可测段
每卡纪律：发现 bug 即修（真测出的问题才算）+ 台账记录；不许为覆盖率
写空转测试（mock 被测物/删断言=违宪）。

## 六、验收
- `pytest --cov` 总覆盖 ≥85%，零覆盖模块=0
- `python scripts/maturity.py` 无 🔴 空 coverage 能力
- CI test job 全绿（3.10-3.12）+ 覆盖率门禁生效
