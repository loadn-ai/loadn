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
