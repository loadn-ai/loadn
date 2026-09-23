// VM 资产定位与完整性校验。rootfs 资产是安全边界的交付物（VM 里的整个
// 执行域），SHA256SUMS 由 build-rootfs.sh 在 CI 侧生成，壳首启必验。
// 与 Tauri 解耦：调用方给候选目录列表（bin 侧从 resource_dir/env/exe 推导）。
use anyhow::{bail, Context, Result};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

pub struct Assets {
    /// 资产目录（dev=仓库 desktop/image/dist，安装包=resource_dir/vm-assets）
    pub dir: PathBuf,
    /// win：`wsl --import` 的 rootfs tarball
    pub wsl_tar: Option<PathBuf>,
    /// mac：ext4 root 盘 + 直启内核件
    pub ext4: Option<PathBuf>,
    pub vmlinuz: Option<PathBuf>,
    pub initrd: Option<PathBuf>,
}

impl Assets {
    /// 当前平台所需的一套是否齐
    pub fn complete_for_current_os(&self) -> bool {
        match std::env::consts::OS {
            "windows" => self.wsl_tar.is_some(),
            "macos" => self.ext4.is_some() && self.vmlinuz.is_some() && self.initrd.is_some(),
            _ => false,
        }
    }
}

/// 候选资产目录（优先级序）：给定 resource 根 + LOADN_VM_ASSETS 覆盖 +
/// 从 exe 向上找仓库 desktop/image/dist（dev 直跑形态）。
pub fn default_roots(resource_dir: Option<&Path>, exe: &Path) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(p) = resource_dir {
        roots.push(p.join("vm-assets"));
    }
    if let Ok(p) = std::env::var("LOADN_VM_ASSETS") {
        roots.push(PathBuf::from(p));
    }
    let mut cur = exe.to_path_buf();
    for _ in 0..6 {
        if let Some(parent) = cur.parent() {
            cur = parent.to_path_buf();
            roots.push(cur.join("image").join("dist"));
        }
    }
    roots
}

pub fn locate(roots: &[PathBuf]) -> Result<Assets> {
    for root in roots {
        let Ok(rd) = fs::read_dir(root) else { continue };
        let mut a = Assets {
            dir: root.clone(),
            wsl_tar: None,
            ext4: None,
            vmlinuz: None,
            initrd: None,
        };
        for e in rd.flatten() {
            let name = e.file_name();
            let n = name.to_string_lossy();
            if n.starts_with("rootfs-wsl-") && n.ends_with(".tar.gz") {
                a.wsl_tar = Some(e.path());
            } else if n.starts_with("rootfs-ext4-") && n.ends_with(".raw.gz") {
                a.ext4 = Some(e.path());
            } else if n.starts_with("vmlinuz-") {
                a.vmlinuz = Some(e.path());
            } else if n.starts_with("initrd-") {
                a.initrd = Some(e.path());
            }
        }
        if a.complete_for_current_os() {
            verify(&a)?;
            return Ok(a);
        }
    }
    bail!(
        "未找到当前平台（{}）的 VM rootfs 资产：期望目录含 rootfs-wsl-*.tar.gz（win）\
         或 rootfs-ext4-*.tar.gz + vmlinuz-* + initrd-*（mac）",
        std::env::consts::OS
    );
}

/// SHA256SUMS-*.txt 存在则逐文件校验（缺失仅跳过——dev 直跑 dist 无清单）
fn verify(a: &Assets) -> Result<()> {
    let Some(manifest) = fs::read_dir(&a.dir)?.flatten().find(|e| {
        let name = e.file_name();
        name.to_string_lossy().starts_with("SHA256SUMS-")
    }) else {
        return Ok(());
    };
    let text = fs::read_to_string(manifest.path()).context("读 SHA256SUMS")?;
    for line in text.lines() {
        let Some((sum, name)) = line.split_once(char::is_whitespace) else { continue };
        let path = a.dir.join(name.trim());
        if !path.exists() {
            continue; // 清单覆盖双平台，本平台没有的文件跳过
        }
        let actual = hex::encode(Sha256::digest(fs::read(&path)?));
        if actual != sum.trim() {
            bail!("资产完整性校验失败：{name} 期望 {sum} 实得 {actual}");
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn locate_rejects_empty_roots() {
        assert!(locate(&[]).is_err());
    }

    #[test]
    fn default_roots_includes_env_override() {
        std::env::set_var("LOADN_VM_ASSETS", "/tmp/loadn-test-assets");
        let roots = default_roots(None, Path::new("/opt/app/shell"));
        assert!(roots.contains(&PathBuf::from("/tmp/loadn-test-assets")));
        std::env::remove_var("LOADN_VM_ASSETS");
    }
}
