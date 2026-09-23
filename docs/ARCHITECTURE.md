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
        │ spawn 子进程（stream-json 契约，docs/PROTOCOL.md v1）
┌───────▼────────────────── 执行域（引擎）──────────────────────────┐
│  claude CLI │ loadn CLI │ opencode CLI │ （第三方经 entry point） │
│  全部跑在 bwrap 沙箱：文件系统白名单挂载 + unshare-net 物理断网     │
│  唯一网络出口 = 出口代理（uds socket + 沙箱内 socat 桥）            │
└───────┬──────────────────────────────────────────────────────────┘
        │ 出口代理注入真凭证（控制域读 vault）
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
  api/          路由（routes.py 会话/turn/管理面；app.py 装配+W0 中间件）
  engine.py     turn 生命周期：spawn/消费/记账/中断恢复/收养
  sandbox.py    bwrap 挂载矩阵（每引擎一份同路径 bind）
  egress_proxy.py  出口代理 + LLM 凭证网关（虚拟域 MITM）
  policy.py     W1 确定性策略（L0 红线 / AST 出口域 / 审批门）
  vault.py      W3 凭证库（AES-GCM）
  audit.py      W6 哈希链账本（月分表 + 日锚点）
  canary.py     W5.5 蜜罐；approve.py W1-2 确认码门
  ops.py        R7 发布系统（release/upgrade/rollback）
  init_cmd.py   R9 安装向导（loadn init）
ui/             React SPA（管理中心：Skills/工具/定时/成本/流量/安全）
profiles/       角色档案（engine/skills/模型组合）
prompts/        工作区模板（CLAUDE.md 等）
skills/         公开 skill 包（私有 skill 走 $LOADN_SKILLS_EXTRA overlay）
docs/           PROTOCOL / RELEASE / ATTACK_SURFACE / 本文 / EXTENDING
tests/          单元+集成+e2e+契约+架构铁律+安全对抗用例（security/）
```

## 数据布局（代码与数据彻底分离）

```
源码仓（开发）          发布实例（生产）                数据根（用户）
/data/…/loadn    →    /opt/loadn/releases/vX.Y.Z/   ~/.loadn-data/
git tag 驱动           current -> vN（原子符号链）     config.yaml
release build 出       每 release 自带 venv           var/loadn.db（会话/turn）
                       wheelhouse 离线依赖             var/vault.enc + .vault_key
                                                      var/audit.db（哈希链）
                                                      workspace/<sid>/
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
| W2 | bwrap 文件系统沙箱 + unshare-net（唯一出口=代理 uds） | sandbox.py |
| W3 | 凭证 AES-GCM 库 + spawn env 白名单 | vault.py |
| W4 | 供应链扫描（skill 安装八类检查 + MCP 哈希锁） | skill_scan.py |
| W5 | 出口代理 enforce + LLM 凭证网关注入（真 token 不进沙箱）+ 蜜罐 | egress_proxy.py canary.py |
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

## 进一步阅读

- docs/PROTOCOL.md —— 引擎方言契约（argv/事件/退出码/心跳）
- docs/EXTENDING.md —— 加引擎/加 provider/写 skill/MCP/hooks/档案
- docs/RELEASE.md —— 发布/升级/回滚手册（L1-L5 恢复路径）
- docs/ATTACK_SURFACE.md —— 攻击面清单 + AI-BOM + known-gaps
