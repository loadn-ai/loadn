// 引导编排：资产→provision→启动→健康→token→导航。任何一步失败落
// Error 态（splash 显示 + 重试按钮重入本函数）。
use crate::splash::{self, Phase};
use anyhow::{Context, Result};
use loadn_shell_core::{assets, vm};
use std::path::PathBuf;
use std::time::Duration;
use tauri::{AppHandle, Manager, Url};

pub fn run(app: AppHandle) {
    if let Err(e) = try_run(&app) {
        splash::phase(&app, &Phase::Error { msg: format!("{e:#}") });
    }
}

fn try_run(app: &AppHandle) -> Result<()> {
    // 首跑装配全局句柄（重试复用——Box 不可克隆；OnceLock set 失败即已装配）
    let _ = crate::PROVIDER.set(vm::provider()?);
    let provider = crate::PROVIDER.get().expect("PROVIDER 刚 set，不可能空");

    splash::phase(app, &Phase::DetectingAssets);
    let resource = app.path().resource_dir().ok();
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("."));
    let a = assets::locate(&assets::default_roots(resource.as_deref(), &exe))
        .context("定位 VM 资产失败")?;
    let data_dir: PathBuf = app
        .path()
        .app_local_data_dir()
        .context("解析本地数据目录失败")?
        .join("vm");
    std::fs::create_dir_all(&data_dir)?;

    if !provider.installed(&data_dir)? {
        splash::phase(app, &Phase::Provisioning);
        provider.provision(&a, &data_dir)?;
    }
    splash::phase(app, &Phase::Starting);
    provider.start(&a, &data_dir)?;

    splash::phase(app, &Phase::WaitingHealth { elapsed_s: 0 });
    let mut ok = false;
    for i in 0..90u16 {
        if provider.healthy().unwrap_or(false) {
            ok = true;
            break;
        }
        std::thread::sleep(Duration::from_secs(2));
        splash::phase(app, &Phase::WaitingHealth { elapsed_s: (i + 1) * 2 });
    }
    if !ok {
        anyhow::bail!("执行域 180 秒未就绪（/api/health 不通）——重试或查看 VM 日志");
    }

    // token 读回 → 一次性 URL 注入（前端 client.ts 已支持 ?token= 引导）
    let token = provider
        .exec_stdout("sed -n 's/^  token: \"\\(.*\\)\"/\\1/p' /home/loadn/.loadn-data/config.yaml")
        .context("读取首启 token 失败")?;
    if token.is_empty() {
        anyhow::bail!("token 读回为空（config.yaml 未按预期生成）");
    }
    let url = Url::parse(&format!("{}/?token={}", provider.endpoint(), token))?;
    app.get_webview_window("main")
        .context("主窗口缺失")?
        .navigate(url)?;
    splash::phase(app, &Phase::Ready);
    Ok(())
}
