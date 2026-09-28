# loadn 仓库宪法

引擎 `loadn/`（单依赖 httpx，argv 与 claude CLI 同构）+ 平台 `loadn_webui/`（FastAPI）。
测试：`.venv/bin/python -m pytest tests/ -q`（零 token）；Lint：`ruff check .`。

## 实现纪律（impl-specs 配套）

1. **一会话一卡**：只做 impl-specs/ 里指定编号的任务卡；卡没让你改的文件不改。
   发现相邻问题→记入 PROGRESS-impl.md 的 `## backlog` 节，禁止顺手改。
2. **借鉴源码**在 `vendor-refs/`（codex/pi/zcode/openclaw/opencode；符号链接到调研工作区）。用 Grep/按行 Read
   定位卡上给出的路径，禁止整仓通读。卡片没提的参考文件先看目录再抽查。
3. **依赖红线**：运行时依赖保持 httpx 单依赖。唯一豁免：bashlex 走可选 extras
   `loadn[ast]`（P0-4）。新增任何其他依赖必须先停下来在 backlog 记录理由等人工确认。
4. **测试纪律**：改代码必带测试；安全相关卡的新用例进 `tests/security/` 并按
   A*/B* 编号续排；全量 `pytest -q` 必须零 token 通过（不许在测试里发真请求）；
   `ruff check .` 干净。
5. **协议纪律**：涉及事件流/argv 契约的改动必须同步 `docs/PROTOCOL.md` 与
   `tests/contract/`（v2 事件只增量，不破坏 v1 方言兼容）。
6. **安全默认**：新能力默认 fail-closed；审批/确认码门不可绕过；任何修复/记忆/回放
   路径不得绕过权限引擎与审计（含 redact：token/凭证不落日志）。
7. **上下文纪律**：探索用 subagent（Task），只回传结论；上下文过半即收尾——更新
   PROGRESS-impl.md、commit、结束会话；续作开新会话读台账，不在旧会话硬撑。
8. **台账**：每卡完成在 PROGRESS-impl.md 勾一行：`- [x] [P0-1] 2026-09-24 改动
   <files> 测试 <n passed>`；commit 信息 `impl(卡号): 一句话`。
9. **诚实**：卡上验收过不了就说过不了，记 backlog 与原因；禁止删测试/放宽断言让
   自己过；禁止 mock 掉真实被测物。

## 测试同步纪律（新功能/改功能的覆盖面保障——M0-M6 突变战役沉淀）

1. **新文件必进映射**：新增 `loadn/`·`loadn_webui/` 模块或大改现有模块时，同步登记
   `scripts/mutate.py` 的 `TARGET_TESTS`（文件→窄测试集）——映射缺席=突变验证永远
   够不着（vault/balance/grind 三起假存活的教训）。
2. **守卫必配否定路径对赌**：新增/修改判定门（if 守卫、白名单、fail-closed、阈值）
   必须至少一个「不该通过」用例断言拒绝形态——2589 变异实证：正向路径测得再全，
   守卫反转照样全存活。断言两条方法论：`in str(e)` 子串会被整帧原文骗过（须排除
   回显噪声）；守卫 or→and 只有「真值非串」等区分形态能杀死。
3. **合入前跑该文件的突变窄集**：功能性新代码在卡收尾时对触及文件跑
   `scripts/mutate.py --files <文件>`（外部杀手环境用 /tmp 驱动+文本写回，见
   PROGRESS-impl.md 突变队列节），杀伤率 <50% 说明测试是摆设——先补断言再收卡。
   存活先对照 `tests/TEST-PLAN.md` §七白名单分类（等价 8 类）再判盲区。
4. **按改动类型配测试**：事件流/argv→`tests/contract/`（纪律 5）；新交互→evals
   加场景 yaml；新安全面→`tests/security/` 续排；提示词/上下文→挂 context 直测。
5. **覆盖率门只升不降**：CI fail-under 当前 83——功能批收官时按实值上调；新文件
   覆盖率没有理由低于 90%。

