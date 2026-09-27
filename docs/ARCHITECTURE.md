# 架构总览

> 面向首次进入仓库的贡献者。读完应能回答：代码在哪、进程怎么跑、数据在哪、
> 安全机制各自守哪一层。细节契约见文末索引。

## 三个平面

```
┌────────────────────────── 控制域（平台）──────────────────────────┐
│  loadn_webui（FastAPI, :8792）          ui/（React SPA）          │
│  · 会话/turn/调度/成本/分享 API          · TokenGate + 管理中心    │
│  · W0-W6 安全栈（见下）                 · 安全中心/流量/成本面板   │
└───────┬──────────────────────────────────────────────────────────┘
        │ spawn 子进程（stream-json 契约，docs/PROTOCOL.md v1；
        │   或 P2-1 daemon：UDS attach 挂起存活引擎——断连重连续作）
┌───────▼────────────────── 执行域（引擎）──────────────────────────┐
│  claude CLI │ loadn CLI │ opencode CLI │ （第三方经 entry point） │
│  全部跑在 bwrap 沙箱：文件系统白名单挂载 + unshare-net 物理断网     │
│  唯一网络出口 = 出口代理（uds socket + 沙箱内 socat 桥）            │
└───────┬──────────────────────────────────────────────────────────┘
        │ 出口代理注入真凭证（控制域读 vault）；三态 off/warn/enforce
        │   + 拦截弹卡确认（默认 ask）+ 会话级 params.egress 任务放开
┌───────▼────────────── 上游 ───────────────────────────────────────┐
│  LLM 网关（虚拟域 llm-gw.internal） / 白名单公网域（pypi/npm/…）   │
└──────────────────────────────────────────────────────────────────┘
```

**核心解耦**：平台与引擎只有一条缝——子进程 argv + NDJSON 事件流。任何
能说 stream-json 方言的可执行文件都能当一个引擎（挂载方式见
docs/EXTENDING.md）。平台不 import 引擎内部，引擎不知道平台存在
（tests/architecture 有铁律测试锁这条边界）。

## 仓库地图

```
loadn/          引擎（可独立 pip install，单依赖 httpx）
  core/         AgentCore 主循环、compaction、subagents、hooks
  tools/        Bash/Read/Write/Edit/… + InteractiveShell(pty) + MCP 动态
  providers/    Anthropic 形 SSE / OpenAI 兼容 / fake（零 token 测试）
loadn_webui/    平台（FastAPI）
  api/          app.py 装配+W0 中间件；sse.py 事件流；routes/ 按域拆包
                （skills/tools/settings/projects/sessions/turns/admin/
                approvals/schedules/files/artifacts/stats + _common 横切件，
                __init__ 聚合 router——加端点进对应域文件即可）
  engine.py     turn 生命周期：spawn/消费/记账/中断恢复/收养
  sandbox.py    bwrap 挂载矩阵（每引擎一份同路径 bind）
  egress_proxy.py  出口代理 + LLM 凭证网关（虚拟域 MITM）
  policy.py     W1 确定性策略（L0 红线 / AST 出口域 / 审批门）
  vault.py      W3 凭证库（AES-GCM）
  audit.py      W6 哈希链账本（月分表 + 日锚点）
  canary.py     W5.5 蜜罐；approve.py W1-2 确认码门
  ops.py        R7 发布系统（release/upgrade/rollback）
  init_cmd.py   R9 安装向导（loadn init）
desktop/        桌面产品形态（Tauri 壳 + Debian rootfs 双产物：
                WSL2 import tar / mac VZ ext4——整 Linux VM 内跑执行域）
ui/             React SPA（管理中心：Skills/工具/定时/成本/流量/安全）
profiles/       角色档案（engine/skills/模型组合）
prompts/        工作区模板（CLAUDE.md 等）
skills/         公开 skill 包（私有 skill 走 $LOADN_SKILLS_EXTRA overlay）
evals/          场景发布门（ScriptedProvider 零 token 全链，10 场景）
examples/       扩展示例 ×10（loadn.ext：工具替换/权限门/自定义 provider）
scripts/        mutate.py 突变 runner / maturity.py 记分卡 / check_models.py
docs/           PROTOCOL / RELEASE / ATTACK_SURFACE / 本文 / EXTENDING
tests/          单元+集成+e2e+契约+架构铁律+安全对抗用例（security/）
                结构与质量门见 tests/TEST-PLAN.md
```

## 数据布局（代码与数据彻底分离）

```
源码仓（开发）          发布实例（生产）                数据根（用户）
/data/…/loadn    →    /opt/loadn/releases/vX.Y.Z/   ~/.loadn-data/
git tag 驱动           current -> vN（原子符号链）     config.yaml
release build 出       每 release 自带 venv           var/loadn.db（会话/turn）
                       wheelhouse 离线依赖             var/vault.enc + .vault_key
                                                      var/audit.db（哈希链）
                                                      workspace/<sid>/tasks/<NN>-*/
                                                        （项目子任务私有目录：
                                                        进度/笔记/产物隔离，
                                                        项目根宪法祖先链继承，
                                                        共享 inputs/ 只读）
                                                      run/（锁/KILL_ALL/egress.sock）
```

升级/回滚 = 换 current 指针（ops.py，含 preflight/DB 备份/等 idle/
自动回滚）。数据根零迁移——这是「升级不碰用户数据」的结构保证。
env：`LOADN_WEBUI_HOME`（平台数据根）、`LOADN_HOME`（引擎数据根）、
`LOADN_SKILLS_EXTRA`（skill overlay 目录）。

## 安全栈一览（哪个文件守哪层）

| 层 | 机制 | 锚点文件 |
|---|---|---|
| W0 | token 认证 + Host 白名单 + 管理面双头 + SSE ticket | api/app.py |
| W1 | 确定性策略：L0 红线 / bashlex AST 出口域 / 确认码审批门 | policy.py approve.py |
| W2 | bwrap 文件系统沙箱 + unshare-net（唯一出口=代理 uds）；跨平台为**统一档位枚举** `SANDBOX_TIERS = off / bwrap / vm-bwrap / seatbelt / appcontainer / remote`（config.yaml `security.sandbox`，非法值拒启）——`vm-bwrap` 执行语义=bwrap（桌面形态整 Linux VM 内跑完整执行域，见 desktop/）；seatbelt/appcontainer/remote 为占位档，**fail-closed 降级 off 并审计**（`sandbox_tier` 账本行 + snapshot `direct-fallback` 遥测 + /api/health `sandbox.{requested,effective,reason}` 三态） | sandbox.py config.py |
| W3 | 凭证 AES-GCM 库 + spawn env 白名单 | vault.py |
| W4 | 供应链扫描（skill 安装八类检查 + MCP 哈希锁） | skill_scan.py |
| W5 | 出口代理三态（off=直通审计/warn=放行告警/enforce=白名单；拦截默认弹卡确认，403 带 agent 可读指引）+ 会话级 params.egress 任务放开 + 白名单热重载 + DNS 重绑定防护 + 审批式临时授权（重启即清）+ LLM 凭证网关注入（真 token 不进沙箱）+ 蜜罐 | egress_proxy.py egress_grants.py canary.py |
| W6 | 哈希链审计账本 + 快照回滚 + kill switch | audit.py |

设计原则：**策略在模型之外**——所有红线是确定性代码，不依赖模型听话；
fail-closed（探测不到组件就降级到更严的一档并审计）。

## 进程与恢复模型

- turn = 平台 spawn 的引擎子进程；正常结束消费 `result` 事件记账。
- 服务重启：沙箱 turn 因 `--die-with-parent` 随之终止（不留无监管沙箱），
  重启后 `recover_after_restart` 按「pid 活→收养 / 有 result→补记账 /
  否则 interrupted（可续跑）」分派。
- 中断续跑：引擎 transcript 按完整交互落盘，会话行记录
  `claude_session_id`，下一条消息走 `--resume`。

## 测试体系（质量门与验证深度）

四层防线，全部零 token（fake/ScriptedProvider 驱动）：

| 层 | 内容 | 门 |
|---|---|---|
| 单元/集成 | ~1350 用例，行覆盖 **83.1%** | CI push 门 `--cov-fail-under=83` |
| 契约 | 引擎方言 v1/v2 对赌（`tests/contract/`）+ 架构铁律 | 同上 |
| 端到端 | e2e（真 uvicorn + fake 引擎全链）+ evals 10 场景 | evals 为 nightly 发布门 |
| **突变** | 自研 runner（`scripts/mutate.py`）对 **41 个安全与核心文件**注入 2589 变异，**杀伤率 76.1%**（安全批 66% / 功能批 80%）| CI 周跑 8 文件逐文件下限 |

突变层是本项目的差异化投入：行覆盖证明「代码被跑到」，突变杀伤证明
「断言真能杀死 bug」——41 文件里挖出 11 个产品真 bug 与 ~110 处
「正向路径全测、否定路径裸奔」的盲区，全部补了否定路径对赌。逐文件
终值、等价变异白名单（8 类）与 runner 基建坑实录见
`tests/TEST-PLAN.md` §七。

新功能的测试同步纪律（映射登记/守卫配否定对赌/合入前跑窄集突变）
见仓库宪法 `CLAUDE.md`「测试同步纪律」节。

## 进一步阅读

- docs/PROTOCOL.md —— 引擎方言契约（argv/事件/退出码/心跳）
- docs/EXTENDING.md —— 加引擎/加 provider/写 skill/MCP/hooks/档案
- docs/RELEASE.md —— 发布/升级/回滚手册（L1-L5 恢复路径）
- docs/ATTACK_SURFACE.md —— 攻击面清单 + AI-BOM + known-gaps
- tests/TEST-PLAN.md —— 测试结构、覆盖率收口实录、突变战役总表（§七）
- docs/CONFIG.md —— config.yaml 全键参考（安全段/环境变量/会话级覆盖链）
