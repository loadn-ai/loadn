// 桌面壳核心库：与 Tauri 解耦的 VM 执行域生命周期层。刻意零 GUI 依赖、
// 零 TLS/异步依赖——win/mac 交叉 cargo check 与单测都能在 Linux 开发机上
// 跑（tauri 平台胶水由 CI 的原生 runner 编译）。
pub mod assets;
pub mod httpx;
pub mod vm;
