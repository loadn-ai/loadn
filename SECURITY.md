# 安全策略

## 报告漏洞

请勿通过公开 issue 报告安全问题。通过 GitHub 私有安全通告
（Security → Report a vulnerability）提交，或联系维护者邮箱。

我们会在 7 天内确认收到，90 天内给出修复或缓解方案。

## 支持版本

| 版本 | 状态 |
|---|---|
| 0.1.x | ✅ 支持 |

## 设计边界（与安全相关）

- hahaness 以你声明的权限模式运行；`--dangerously-skip-permissions` /
  `--yolo` 会放行非 deny 名单内的全部工具——容器/沙箱里跑无头任务时
  请自行评估
- 密钥只经环境变量/`$HAHANESS_HOME/config.json` 进入，绝不写入 transcript
- 子进程治理只作用于自己登记的进程组，不扫全局进程表
