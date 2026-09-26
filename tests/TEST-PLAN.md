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
- [x] T1 测试规则完善 + marker/taxonomy/CI 覆盖率门禁
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

## 六、验收（2026-09-25 收口实录——诚实口径）
**终值：83.1%（15013/18064），基线 78.8% → +4.3pp；1224 passed。**

- ✅ 记分卡 26✅/0🔴/0🟡（零红零黄）；evals smoke 10 场景全绿
- ✅ 原零覆盖模块全部清零（backup 88.9%/repl 80%/hooks 89.7%/
  titlegen 78.7%/skillhub 91.3%/docx 82.3%）；唯余两个 __main__.py
  三行入口（e2e 子进程已跑、进程内 coverage 不追——结构性盲区非测试缺失）
- ⚠️ 85% 目标未达（差 1.9pp），差距构成诚实分析：
  1. **子进程 coverage 盲区**（最大头）：cli/main 69.7%、bridge 34%、
     ops ~40%、webui/cli 49%——这些面的测试**存在且全绿**（e2e_cli/
     daemon 子进程中继/release build），但 pytest-cov 不追踪子进程；
     可用 COVERAGE_PROCESS_START 补量测（backlog）
  2. resources.py 67%（外部端点面，需真端点/重桩——backlog）
  3. mcp/client HTTP 半边（oauth 重试等已有测试但部分分支需伪造 SSE）
- CI 门禁 78 → **83** 收紧（与实值对齐）

## 七、突变测试收口（M0-M5，2026-09-27；runner=scripts/mutate.py）

**全组终值：483/731 变异被杀死（66.1%）**；5 算子（比较反转/and·or/
True·False/if 恒真恒假/常数+1）× 19 个安全与核心文件。

### 逐文件终值

| 批 | 文件 | 杀/总 | 率 | 补测杀死的代表性盲区 |
|---|---|---|---|---|
| M1 | policy.py | 53/88 | 60% | hook 写路径敏感路径分派；L184 parts 缺失迭代 None |
| M1 | approve.py | 43/84 | 51% | _render_summary 8 动作类型摘要内容 |
| M1 | vault.py | 33/60 | 55% | LDV1 格式门/34B 边界/资源密钥未知名 |
| M1 | truststore.py | 19/34 | 56% | 纯 skills 工作区=项目根（供应链漏检面） |
| M1 | canary.py | 12/21 | 57% | is_locked 空标记/嵌套新目录 |
| M1 | net_policy.py | 37/45 | 82% | 存活全等价（缓存容量/偏移+1） |
| M1 | bash_policy.py | 41/55 | 75% | 决策平局第一条语义/命令替换 fail-closed |
| M2 | edit.py | 13/43 | 30% | fuzzy 单命中门/无命中/ipynb 禁 fuzzy |
| M2 | write.py | 6/11 | 55% | mtime 复查守卫（真 bug 修复面） |
| M2 | multiedit.py | 5/7 | 71% | 参数校验/原子性/链式顺序（原 0%） |
| M2 | turn_diff.py | 15/19 | 79% | 超限粗粒度/同内容空 diff |
| M2 | autocommit.py | 17/21 | 81% | — |
| M2 | autolint.py | 15/18 | 83% | — |
| M3 | sandbox.py | 43/53 | 81% | UDS 桥双态/降档告警去重/空 path 展开成 cwd |
| M3 | egress_proxy.py | 66/89 | 74% | **dns-rebind 双路+ask 失败 fail-closed 反转** |
| M3 | egress_grants.py | 23/23 | 100% | TTL 三界/revoke 未命中 |
| M4 | session.py | 13/20 | 65% | compact 摘要链/todos 重放形状/usage 索引 |
| M4 | hooks.py | 17/21 | 81% | 非对象条目炸穿/**失败钩子越权改写** |
| M4 | daemon.py | 14/19 | 74% | 断连摘除保他人/socket 0600/init 真模型名 |

（每卡补测均经手动注入复验；低率文件=等价变异占比高，见白名单）

### 等价变异白名单（重扫对照基线——新增存活先对这张表再判盲区）

1. **耐久性位**：fsync=True/False、mkdir(exist_ok/parents)——崩溃窗口
   语义，测试面不可观测
2. **常数边界 ±1**：截断 [:2000]→2001、timeout 10→11、token 长度、
   1<<20 流缓冲、缓存容量——行为同型
3. **ensure_ascii=False**：\uXXXX 转义对 JSON 语义等价（可读性位除外，
   已按设计点补杀 _deny_body 一例）
4. **日志门**：告警条件反转只差 warning 行
5. **真值回退 or-链**：`.get(x) or ""` 在值域恒非空的实际路径
6. **死代码/死存储**（记 backlog 待清理）：sandbox._resolve_in、
   mark_compact L90（append 即被 replay 覆盖）、db.add_usage model 形参、
   schema default 文档位
7. **挂死即破坏**（保守计活）：daemon 入口守卫反转=导入即 serve
8. **DDL/驱动参数**：幂等位、sqlite timeout

### runner 基建坑实录（4 起，可信度本身需审计的实证）

1. 多行变异单行替换→语法错误假杀（虚高 30pp）→跳过多行片段
2. 同秒 mtime 假阳→存活复验；同尺寸变异+陈旧 pyc→-B+
   PYTHONDONTWRITEBYTECODE+restore 连 pyc 删
3. 并发段互相 git checkout 冲掉变异→全体假存活（杀伤率逐段递减即信号）
   →单命令串行；未提交 edit 会被扫描冲掉（**六起丢失**：runner 修复×2、
   补测文件、映射修复×2——multiedit/bash_policy 两卡映射修好后未提交
   蒸发，CI 冒烟 0% 才暴露）→先 commit 再扫
4. 窄测试集映射漏文件→假存活（vault/balance 两起）→新测试文件先进映射

### 复核重扫（M5，修复版 runner 对四个早期文件）

canary 57/policy 59/truststore 56/approve 51（总 55.5%）——与原值差异
主因=后续测试扩充真杀（canary -4 活/policy -10 活/approve -15 活），
**假杀实证仅 policy L184 一例**（已补测杀死）；pyc 污染风险基本未兑现
（restore 删 pyc 防御自 M1-vault 起生效）。

### CI 回归门

`.github/workflows/ci.yml` `mutation` job：周跑（周一 04:23 UTC）+
workflow_dispatch，代表性子集（canary/multiedit/turn_diff/net_policy）
逐文件杀伤率下限（55/60/65/75%，留 5-10pp 防抖动）；全套 600+ 变异
按台账队列本地跑。
