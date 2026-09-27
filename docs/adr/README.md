# ADR（架构决策记录）

> 记录「为什么这么定」——避免换维护者后决策理由丢失。一条一文件：
> `NNNN-<slug>.md`，状态 accepted / superseded（被 NNNN 取代）。
> 新决策从 0005 续排。

## 索引

| # | 决策 | 状态 |
|---|---|---|
| [0001](0001-single-dependency-httpx.md) | 引擎运行时依赖保持 httpx 单依赖 | accepted |
| [0002](0002-homegrown-mutation-runner.md) | 自研突变测试 runner 而非 mutmut | accepted |
| [0003](0003-bilingual-docs.md) | 文档双语策略：英文主文件 + 中文镜像 | accepted |
| [0004](0004-sandbox-egress-rather-than-prompt.md) | 安全靠确定性代码（沙箱/出口代理）不靠提示词 | accepted |
