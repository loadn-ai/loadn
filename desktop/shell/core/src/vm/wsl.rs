// Windows 侧 provider：WSL2 发行区「loadn」。宿主内核即 Linux 内核——
// bwrap user namespace / unshare-net / vsock 天然可用，rootfs tarball 直接
// `wsl --import`。systemd 由 wsl.conf 拉起 loadn-vm.service。
use super::VmProvider;
use crate::assets::Assets;
use anyhow::{bail, Context, Result};
use std::path::Path;
use std::process::{Command, Stdio};

pub struct Wsl;

impl Wsl {
    pub fn new() -> Self {
        Self
    }

    /// wsl.exe 输出常为 UTF-16LE（--list 等管理命令）；按含 NUL 判定解码
    fn decode(bytes: &[u8]) -> String {
        if bytes.len() >= 2 && bytes.iter().filter(|b| **b == 0).count() > bytes.len() / 4 {
            let units: Vec<u16> = bytes
                .chunks_exact(2)
                .map(|c| u16::from_le_bytes([c[0], c[1]]))
                .collect();
            String::from_utf16_lossy(&units)
        } else {
            String::from_utf8_lossy(bytes).into_owned()
        }
    }

    fn run(args: &[&str]) -> Result<String> {
        let out = Command::new("wsl.exe")
            .args(args)
            .stdin(Stdio::null())
            .output()
            .map_err(|e| anyhow::anyhow!("调 wsl.exe 失败（需 Windows 10 2004+ 且 WSL 已装）：{e}"))?;
        if !out.status.success() {
            bail!(
                "wsl {} 失败（{}）：{}",
                args.join(" "),
                out.status,
                Self::decode(&out.stderr).trim()
            );
        }
        Ok(Self::decode(&out.stdout))
    }
}

const DISTRO: &str = "loadn";

impl VmProvider for Wsl {
    fn name(&self) -> &'static str {
        "WSL2"
    }

    fn installed(&self, _data_dir: &Path) -> Result<bool> {
        Ok(Self::run(&["--list", "--quiet"])?.to_lowercase().contains(DISTRO))
    }

    fn provision(&self, assets: &Assets, data_dir: &Path) -> Result<()> {
        let tarball = assets
            .wsl_tar
            .as_ref()
            .context("win 平台资产缺 rootfs-wsl-*.tar.gz")?;
        let dir = data_dir.join("wsl");
        std::fs::create_dir_all(&dir)?;
        Self::run(&[
            "--import",
            DISTRO,
            &dir.to_string_lossy(),
            &tarball.to_string_lossy(),
            "--version",
            "2",
        ])
        .context("wsl --import 失败")?;
        Ok(())
    }

    fn start(&self, _assets: &Assets, _data_dir: &Path) -> Result<()> {
        // wsl.conf: systemd=true → 发区启动即拉起 loadn-vm.service；这里显式
        // start 幂等兜底（旧 WSL 无 systemd 时报错并给出明确指引）
        let out = Command::new("wsl.exe")
            .args(["-d", DISTRO, "-u", "root", "--", "systemctl", "start", "loadn-vm"])
            .output()
            .context("systemctl start 失败")?;
        if !out.status.success() {
            bail!(
                "loadn-vm 服务启动失败（WSL 需 ≥0.67.2 支持 systemd）：{}",
                String::from_utf8_lossy(&out.stderr).trim()
            );
        }
        Ok(())
    }

    fn stop(&self) -> Result<()> {
        Self::run(&["--terminate", DISTRO])?;
        Ok(())
    }

    fn exec_stdout(&self, cmd: &str) -> Result<String> {
        Self::run(&["-d", DISTRO, "-u", "loadn", "--", "sh", "-c", cmd])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_handles_utf16_and_utf8() {
        let utf16: Vec<u8> = "loadn\r\n".encode_utf16().flat_map(|u| u.to_le_bytes()).collect();
        assert_eq!(Wsl::decode(&utf16), "loadn\r\n");
        assert_eq!(Wsl::decode(b"plain utf8\n"), "plain utf8\n");
    }
}
