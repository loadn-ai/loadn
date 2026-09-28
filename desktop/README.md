# loadn desktop（W2 统一方案 U1：桌面 = 壳 + Linux VM 执行域）

## 论证结论（为什么是 VM 而不是移植沙箱）

A（降级 off 档）/ B（mac seatbelt + win AppContainer）/ C（远程执行器）不是三套
系统，是**同一个枚举的三个值**（`config.yaml security.sandbox`，见
`loadn_webui/config.py SANDBOX_TIERS`）。档位框架已在平台内：占位档请求即
fail-closed 降级 off 并审计（`sandbox_tier` 账本行 + snapshot `direct-fallback`
遥测 + `/api/health sandbox.{requested,effective,reason}` 三态）。

「统一」的真路线是 **U1：桌面壳内嵌一个 Linux VM，整个执行域原样跑在里面**：

- mac：Virtualization.framework（经 vfkit），内核直启（vmlinuz+initrd），ext4 裸盘
- win：WSL2（宿主内核即 Linux 内核），rootfs tarball `wsl --import`

于是桌面满档 = **vm-bwrap**：VM 内 bwrap/unshare-net/uds/socat/EgressProxy/
哈希链审计与 Linux 生产服务器**逐字节同一套代码**——安全论证零新面。不移植
沙箱，把 Linux 搬进来。seatbelt/AppContainer 保持枚举占位（长期可选，不承诺）。

## 形态与目录

```
desktop/
├── image/                  # VM rootfs 构建（单镜像 → 双平台资产）
│   ├── Dockerfile.rootfs   #   Debian+node20+claude/opencode+systemd+bwrap 满装
│   ├── loadn-vm-install.sh #   幂等安装 payload（镜像 ops.py releases 布局）
│   ├── loadn-vm-start.sh   #   首启铸 token（config.yaml 0600）→ serve 0.0.0.0:8792
│   ├── loadn-vm.service    #   VM 内 systemd 单元（KillMode=process 保 turn 存活）
│   ├── loadn-vm-exec.service # vsock 2222 → sh（mac 壳 exec 通道；WSL 自动跳过）
│   ├── build-payload.sh    #   仓库 → payload/{release.tar.gz, wheelhouse/, VERSION}
│   ├── build-rootfs.sh     #   docker build/export → dist/{wsl-tar, ext4, vmlinuz, initrd} + SHA256SUMS
│   ├── build-ext4.sh       #   export tar → ext4 磁盘（sudo loop mount，mac 启动件）
│   └── verify-local.sh     #   docker --privileged 作 VM 替身做全链验收
└── shell/                  # Tauri 壳
    ├── core/               # 纯 std 核心：vm/{wsl,vz} + relay + assets 校验 + 回环 http
    │                       #   （三平台可本机 cargo check/test；无 TLS/异步/C 依赖）
    ├── src-tauri/          # 壳 bin：状态机 + splash + WebView 导航（平台胶水，CI 原生编译）
    └── splash/index.html   # 引导页（阶段事件渲染；就绪后整窗导航去 VM UI）
```

## 壳的引导状态机

`DetectingAssets → Provisioning（首装）→ Starting → WaitingHealth → Ready`

Ready 时经 exec 通道读回首启 token，WebView 导航
`http://127.0.0.1:8792/?token=…`（前端 `client.ts` 已支持 `?token=` 引导）。
任何失败落 Error（splash 显示 + 重试）。外部页面拿不到任何 Tauri 原生 API
（capabilities 只授予本地 splash；刻意的安全边界）。

平台通道：

| | 生命周期 | 数据/命令通道 | WebView 入口 |
|---|---|---|---|
| win | `wsl --import/--terminate` | `wsl -d loadn -u loadn --` exec | localhost:8792（WSL localhostForwarding） |
| mac | vfkit 直启内核 + vsock | vsock 2222（socat→sh） | 127.0.0.1:8792（壳内 TCP↔vsock 中继） |

VM 内网络姿态与生产一致：enforce 出口代理 + 白名单；VM 本身 NAT 出站仅用于
provider LLM 调用（经代理网关注入，真凭证不进沙箱）。

## 构建与验收（Linux 开发机即可全跑）

```bash
desktop/image/build-payload.sh      # git archive HEAD + ui dist + 离线 wheelhouse
desktop/image/build-rootfs.sh       # docker build/export → dist/ 双平台资产 + SHA256SUMS
desktop/image/verify-local.sh       # --privileged 容器作 VM 替身：健康/档位/引擎/doctor/token/鉴权建会话
```

壳编译矩阵（无需 mac/win 机器）：

```bash
cd desktop/shell
cargo test -p loadn-shell-core                                    # 核心单测
cargo check --workspace                                           # linux（壳全量）
cargo check -p loadn-shell-core --target aarch64-apple-darwin     # mac 交叉
cargo check -p loadn-shell-core --target x86_64-pc-windows-gnu    # win 交叉
```

CI（`.github/workflows/desktop.yml`）：linux 出资产 + 替身验收 → macos-15 出
dmg / windows 出 msi（资产按平台裁剪注入 resources）→ tag 时挂 Release。

## 真机联调备忘

- mac：vfkit vsock 设备参数与 socketURL 语义以真机为准——收敛在
  `core/src/vm/vz.rs vfkit_args()`，现场只改一处；VM 日志 `/tmp/loadn-desktop-vm.log`
- win：WSL ≥ 0.67.2（systemd）；`wsl --list` 输出 UTF-16 由 `Wsl::decode` 处理
- 通用：首装目录 `app_local_data_dir()/vm`；`LOADN_VM_ASSETS` 可覆盖资产目录
- 恢复出厂 = 删数据目录（win 另需 `wsl --unregister loadn`）
