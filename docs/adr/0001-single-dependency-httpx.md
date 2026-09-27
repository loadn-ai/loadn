# ADR 0001：引擎运行时依赖保持 httpx 单依赖

状态：accepted（2026-09-24 起，仓库宪法纪律 3）

## 背景

`loadn/` 引擎要能被任意宿主零成本嵌入：pip install 一次到位、无依赖
地狱、供应链面最小。同类项目动辄拉进 pydantic/rich/tree-sitter 全家桶。

## 决策

运行时依赖只有 httpx 一个。唯二豁免走**可选 extras**：`loadn[ast]`
（bashlex，Bash AST 权限拆解）、`loadn[repomap]`（tree-sitter，仓库地图
精确提取）——核心功能在缺省安装下全量可用（AST 拆解降级文本拦截，
repomap 降级正则地图且诚实标注）。

## 后果

- 正面：`pip install loadn` 无冲突面；供应链审计（pip-audit）只盯一个
  传递树；fake provider 使测试零 token 零网络
- 代价：常见便利（yaml/富文本）手写或放平台侧 `loadn_webui`（它的
  依赖面宽松）
- 新增任何其他运行时依赖须先停手记录理由等人工确认（宪法红线）
