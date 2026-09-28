// 引导阶段模型：Rust 侧唯一对前端的暴露面（splash 页监听事件渲染；就绪后
// 整窗导航去 VM UI，splash 使命结束）。
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

#[derive(Serialize, Deserialize, Clone, Debug)]
pub enum Phase {
    /// 定位/校验 rootfs 资产
    DetectingAssets,
    /// 首装（wsl --import / ext4 工作副本）
    Provisioning,
    /// 启动 VM（WSL 发行区 / vfkit）
    Starting,
    /// 轮询 /api/health
    WaitingHealth { elapsed_s: u16 },
    Ready,
    Error { msg: String },
}

pub fn phase(app: &AppHandle, p: &Phase) {
    let _ = app.emit("shell://phase", p);
}
