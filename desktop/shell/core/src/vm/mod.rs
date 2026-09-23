// VM 执行域 provider 抽象：win=WSL2 / mac=VZ(vfkit)。trait 方法刻意窄——
// 壳只做生命周期编排，不碰任何安全语义（那些全在 VM 内的 loadn 栈里）。
#[cfg(target_os = "macos")]
pub mod vz;
#[cfg(target_os = "windows")]
pub mod wsl;

use crate::assets::Assets;
use anyhow::Result;
use std::path::Path;

pub const APP_PORT: u16 = 8792;
/// VM 内 exec 通道端口（rootfs 的 loadn-vm-exec.service：socat vsock→sh；仅 mac 用）
#[cfg(target_os = "macos")]
pub const EXEC_PORT: u16 = 2222;

pub trait VmProvider: Send + Sync {
    fn name(&self) -> &'static str;

    /// 已 provision？（决定首启是否走 import/建盘）
    fn installed(&self, data_dir: &Path) -> Result<bool>;

    /// 首装：win=wsl --import；mac=克隆 rootfs 工作副本
    fn provision(&self, assets: &Assets, data_dir: &Path) -> Result<()>;

    /// 启动（幂等）：win=start systemd 服务；mac=spawn vfkit + 中继
    fn start(&self, assets: &Assets, data_dir: &Path) -> Result<()>;

    fn stop(&self) -> Result<()>;

    /// VM 内以 loadn 用户跑命令取 stdout（token 读回 / 诊断）
    fn exec_stdout(&self, cmd: &str) -> Result<String>;

    /// WebView/健康探测入口（回环）
    fn endpoint(&self) -> String {
        format!("http://127.0.0.1:{APP_PORT}")
    }

    /// 就绪判定（/api/health 带 token 200 且 release 版本非空——W0 之下健康
    /// 端点同样要鉴权，token 由 bootstrap 先经 exec 通道取得）
    fn healthy(&self, token: &str) -> Result<bool> {
        let v = crate::httpx::get_json(
            "127.0.0.1",
            APP_PORT,
            "/api/health",
            std::time::Duration::from_secs(3),
            Some(token),
        )?;
        Ok(v.get("release").and_then(|r| r.get("version")).is_some())
    }
}

pub fn provider() -> Result<Box<dyn VmProvider>> {
    #[cfg(target_os = "windows")]
    return Ok(Box::new(wsl::Wsl::new()));
    #[cfg(target_os = "macos")]
    return Ok(Box::new(vz::Vz::new()));
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    anyhow::bail!(
        "不支持的平台：{}（桌面壳面向 mac/win；Linux 用服务器形态直接跑）",
        std::env::consts::OS
    );
}
