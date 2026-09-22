# loadn 默认配置攻击面清单 v2 + AI-BOM（W6.5 交付物，1.0.0 终版）

日期：2026-09-22 ｜ 基线：monorepo 1.0.0 ｜ 口径：默认配置（factory defaults）逐面盘点

## 一、攻击面清单（默认配置）

| # | 面 | 暴露 | 默认防线 | 残余风险（诚实） |
|---|---|---|---|---|
| 1 | Web API（127.0.0.1:8792） | 本机进程/反代 | token 强制（14 天宽限）+常量时间比较；Host 白名单；管理面 X-Loadn-Admin；SSE ticket；CSRF 免疫（自定义头） | 宽限期内无凭证放行（2026-10-06 到期 enforce）；?token= 兼容通道在日志/历史留痕 |
| 2 | /share/<token> 公开只读 | 公网（VPS 反代） | 80-bit URL token+CSP sandbox+nosniff | URL 即凭证（设计取舍，攻击面清单如实披露） |
| 3 | 执行面文件系统 | 引擎进程 | bwrap 全引擎（usr/etc ro+workspace rw+档案单会话+敏感路径物理不挂；claude/opencode 各自运行时+档案矩阵） | /tmp 与系统临时区可写（误删兜底=快照回滚） |
| 4 | 执行面网络 | 引擎进程 | **unshare-net 物理断网**+唯一出口 unix socket 代理（uds 桥）；CONNECT 按 SNI/明文按 Host 判定+全量审计 | 沙箱内 socat 桥为单点（进程级；die-with-parent 同灭）；DNS 由代理侧解析（沙箱内无 DNS 泄露面） |
| 5 | 引擎 LLM 凭证 | —— | **虚拟网关域 llm-gw.internal**：引擎只持 dummy token，真凭证由代理控制域注入转发；沙箱零挂载 settings.json（挂载面全扫零命中） | —（已关闭） |
| 6 | 平台资源凭证 | 控制域 | vault.enc AES-GCM+CLI 网关+审批确认码 | 密钥与密文同机（防误拷贝不防全控同用户） |
| 7 | 不可逆动作（邮件/支付/凭证写） | agent CLI | 确认码门 enforce（平台渲染 summary+params_hash+TTL fail-closed） | 终端直调（无会话上下文）=用户本人操作语义，放行 |
| 8 | skill 供应链 | 市场安装 | 八类静态扫描（红线拒装）+tar data filter+能力声明默认禁+MCP 哈希锁 | 黄牌（出网端点/安装器）人工确认制；试运行隔离（依赖 W2 档2）未上线 |
| 9 | 产物预览/导出 | 浏览器 | HTML 白名单消毒+外链占位+CSP 三头 | 占位符点击经代理确认（前端交互层） |
| 10 | 插话/定时唤醒/附件注入 | 提示流 | 来源标注+宪法 Rule of Two+审批门兜底 | 提示层注入无法根除（L1 兜底=不可逆动作全须确认码） |

## 二、AI-BOM v1（模型依赖清单）

| 组件 | 用途 | 供给方 | 凭证位置 | 出口 |
|---|---|---|---|---|
| GLM-5.3（glm-5.3[1m]） | 引擎主 LLM（Z.AI 网关 CC 伪装通道） | open.bigmodel.cn | ~/.claude/settings.json env（M1） | 代理 enforce 白名单 |
| doubao-seed-2-0-mini | titlegen/vlm | ark.cn-beijing.volces.com | config.yaml titlegen.api_key（控制域） | 控制域直连（待代理收编） |
| bocha/zhipu 搜索 | r search | api.bochaai.cn / open.bigmodel.cn | config.yaml resources（控制域） | 控制域直连 |
| 2captcha/capsolver | 打码 | 2captcha.com 等 | 同上 | 同上 |
| AIO 沙箱 VLM/OCR | 文档解析 | 本机 21111 | config resources | 本机回环 |

引擎侧唯一模型依赖=GLM-5.3；其余模型均在控制域（agent 不可达凭证）。

## 三、对抗用例战果（CI 门禁）

tests/security/ 共 **10 文件 100+ 用例**：A1（红线+误拦护栏+集成态 hook 拦截）、
A2/A3/A3b（审批防伪造）、A4（导出消毒）、B1/B2/B3（供应链）、C1（canary+kill）、
C2 前半（env/盘面）、D1（沙箱六面）、E1（回滚）、E2（审计防篡改四路）、
E3（谎报）、E4（毕业考三层）。全量 662 passed。

## 四、known-gap 清单（终版，按优先级）

1. skill 试运行隔离（依赖 W2 档2=AIO 沙箱整会话——外来代码审查场景；
   当前面由八类扫描红线+能力声明默认禁+黄牌人工确认管控）
2. canary 的全外渗通道覆盖（CLI/hook/代理三层已覆盖；文件系统侧信道
   ——如诱导用户人工外传——不在技术防线内，宪法 Rule of Two+审批门兜底）
3. Bash 工具写路径快照（写目标不可静态知——/tmp 系统临时区由沙箱
   tmpfs 隔离+workspace 写入有快照）
4. desktop 壳（M3+ 形态扩展）与认证路径（合规阶段）不在 1.0.0 范围
