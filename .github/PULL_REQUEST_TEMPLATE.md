<!-- 提交信息建议 conventional commits：type(scope): 摘要（feat/fix/docs/chore…，
  仓库历史即此风格）；正文说 why，不只说 what。
  PR 前自检（CI 会跑同样内容）：
  pytest -q          # 全量（引擎 0 token，webui 用 fake provider）
  ruff check .
  改了前端：cd ui && npm run build
-->

## 改了什么 / 为什么

## 关联 issue
<!-- Closes #123 / 属于 roadmap 某项 / 无 -->

## 影响面
- [ ] 引擎（loadn/）
- [ ] 平台（loadn_webui/）
- [ ] 前端（ui/）——如有 UI 变化请附截图或前后对比
- [ ] 协议契约（docs/PROTOCOL.md——改动需 bump 版本并过 tests/contract）
- [ ] 安全面（W0-W6——改动请同步 docs/ATTACK_SURFACE.md）
- [ ] 仅文档

## 怎么验证的
<!-- 测试/真机 turn/对抗用例编号；「手测过」不算契约变更的验证
  新守卫逻辑请附否定路径对赌（见 CONTRIBUTING §断言强度） -->

## 用户可见变更
<!-- 面向用户的行为/配置变化写一句（发版时汇总进 CHANGELOG）；无则留空 -->

## 安全自查（涉及密钥/凭证/个人路径时必填）
- [ ] 无硬编码密钥/个人域名/IP/邮箱进入代码或默认值
- [ ] 新增依赖已过 pip-audit
