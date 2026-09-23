// loadn 桌面壳入口：唯一职责 = 本机 Linux VM 执行域的生命周期编排
// （资产定位→provision→启动→等健康→读 token→WebView 载入 VM 内 Web UI）。
// 安全模型：VM 内整个 loadn 栈与 Linux 服务器逐字节同套（bwrap/unshare-net/
// egress enforce/审计链），壳不重实现任何安全逻辑；外部页面（VM UI）默认
// 拿不到任何 Tauri 原生 API。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod bootstrap;
mod splash;

use loadn_shell_core::vm::VmProvider;
use std::sync::OnceLock;

/// 全局 provider 句柄（bootstrap 装配；退出时收 VM）
static PROVIDER: OnceLock<Box<dyn VmProvider>> = OnceLock::new();

fn main() {
    use tauri::Listener;
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || bootstrap::run(handle));
            // splash「重试」→ 重入引导线程
            let retry = app.handle().clone();
            app.listen_any("shell://retry", move |_| {
                let value = retry.clone();
                std::thread::spawn(move || bootstrap::run(value));
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("loadn 桌面壳初始化失败")
        .run(|_app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(p) = PROVIDER.get() {
                    let _ = p.stop();
                }
            }
        });
}
