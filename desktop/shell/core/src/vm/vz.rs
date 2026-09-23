// macOS 侧 provider：Virtualization.framework 经 vfkit（随包资源分发）。
// 直启内核（--kernel/--initrd，无 EFI 层），root=ext4 裸盘 /dev/vda；网络
// virtio-net NAT（guest 出站），宿主入站走 virtio-vsock 两个端口：
//   8792 = HTTP（relay：127.0.0.1:8792 ↔ vsock，WebView 就绪后载入）
//   2222 = exec（rootfs 的 loadn-vm-exec.service：socat vsock→sh，token 读回）
// 联调注记：vfkit 的 vsock 设备参数与 socketURL 语义以真机验证为准——
// 全部收敛在 vfkit_args()，现场只改一处。
use super::{VmProvider, APP_PORT, EXEC_PORT};
use crate::assets::Assets;
use anyhow::{Context, Result};
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

pub struct Vz {
    vfkit: Mutex<Option<Child>>,
    sock_dir: PathBuf,
}

impl Vz {
    pub fn new() -> Self {
        Self {
            vfkit: Mutex::new(None),
            sock_dir: std::env::temp_dir().join("loadn-desktop-vm"),
        }
    }

    fn vfkit_bin(assets: &Assets) -> PathBuf {
        // 资产目录内优先（CI 把 vfkit 一起打进 vm-assets/），否则 PATH
        let bundled = assets.dir.join("vfkit");
        if bundled.exists() {
            bundled
        } else {
            PathBuf::from("vfkit")
        }
    }

    /// 单点收敛 vfkit 参数（真机联调唯一可能要动的地方）
    fn vfkit_args(&self, data_dir: &Path) -> Vec<String> {
        let vm = data_dir.join("vm");
        let mut args: Vec<String> = [
            "--cpus", "4",
            "--memory", "6144",
            "--kernel", "vmlinuz",
            "--initrd", "initrd.img",
            // ext4 裸盘（无分区表）→ virtio-blk 即 /dev/vda；console=hvc0 便于现场诊断
            "--kernel-args", "root=/dev/vda rw console=hvc0",
            "--device", "virtio-blk,path=rootfs.ext4",
            "--device", "virtio-net,nat",
        ]
        .iter()
        .map(|s| {
            s.replace("vmlinuz", &vm.join("vmlinuz").to_string_lossy())
                .replace("initrd.img", &vm.join("initrd.img").to_string_lossy())
                .replace("rootfs.ext4", &vm.join("rootfs.ext4").to_string_lossy())
        })
        .collect();
        args.push("--device".into());
        args.push(format!(
            "virtio-vsock,port={APP_PORT},socketURL={}",
            self.sock_dir.join("http.sock").to_string_lossy()
        ));
        args.push("--device".into());
        args.push(format!(
            "virtio-vsock,port={EXEC_PORT},socketURL={}",
            self.sock_dir.join("exec.sock").to_string_lossy()
        ));
        args
    }
}

impl VmProvider for Vz {
    fn name(&self) -> &'static str {
        "VZ"
    }

    fn installed(&self, _data_dir: &Path) -> Result<bool> {
        // 工作副本存在即已 provision（恢复出厂 = 删目录）
        Ok(_data_dir.join("vm").join("rootfs.ext4").exists())
    }

    fn provision(&self, assets: &Assets, data_dir: &Path) -> Result<()> {
        let vm = data_dir.join("vm");
        std::fs::create_dir_all(&vm)?;
        // 工作副本（保留资产原件 → 恢复出厂 = 删副本重来）
        let ext4 = assets
            .ext4
            .as_ref()
            .context("mac 平台资产缺 rootfs-ext4-*.tar.gz")?;
        copy_gunzip(ext4, &vm.join("rootfs.ext4"))?;
        std::fs::copy(
            assets.vmlinuz.as_ref().context("缺 vmlinuz-*")?,
            vm.join("vmlinuz"),
        )?;
        std::fs::copy(
            assets.initrd.as_ref().context("缺 initrd-*")?,
            vm.join("initrd.img"),
        )?;
        Ok(())
    }

    fn start(&self, assets: &Assets, data_dir: &Path) -> Result<()> {
        {
            let mut g = self.vfkit.lock().unwrap();
            if let Some(c) = g.as_mut() {
                if c.try_wait()?.is_none() {
                    return Ok(()); // 已在跑
                }
            }
            std::fs::create_dir_all(&self.sock_dir)?;
            // stdout/stderr 全 null——vfkit 日志走自身 -v 选项时再定向到文件；
            // piped 不读会塞管道导致 VM 静默冻结，这里从根上避免
            let child = Command::new(Self::vfkit_bin(assets))
                .args(self.vfkit_args(data_dir))
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .context("启动 vfkit 失败（资产未带 vfkit 且 PATH 无）")?;
            let pid = child.id();
            *g = Some(child);
            log_line(&format!("vfkit pid={pid} args={:?}", self.vfkit_args(data_dir)));
        }
        relay::spawn(self.sock_dir.join("http.sock"), APP_PORT);
        Ok(())
    }

    fn stop(&self) -> Result<()> {
        if let Some(mut c) = self.vfkit.lock().unwrap().take() {
            let _ = c.kill();
            let _ = c.wait();
        }
        Ok(())
    }

    fn exec_stdout(&self, cmd: &str) -> Result<String> {
        // 经 vsock exec 通道：guest 侧 socat 给每个连接一个新 sh（读 stdin），
        // 以哨兵行定界输出；命令里不许出现哨兵词（替换防注入定界歧义）
        let mut s = UnixStream::connect(self.sock_dir.join("exec.sock"))
            .context("exec 通道未就绪（loadn-vm-exec.service）")?;
        s.set_read_timeout(Some(std::time::Duration::from_secs(30)))?;
        let script = format!("{}; echo __LOADN_DONE__\nexit\n", cmd.replace("__", "_"));
        s.write_all(script.as_bytes())?;
        let mut out = Vec::new();
        s.read_to_end(&mut out)?;
        let text = String::from_utf8_lossy(&out);
        let end = text.find("__LOADN_DONE__").unwrap_or(text.len());
        Ok(text[..end].trim().to_string())
    }
}

/// gzip 流式解压落盘（8G 级，勿 read_to_end）
fn copy_gunzip(src: &Path, dst: &Path) -> Result<()> {
    use flate2::read::GzDecoder;
    let mut r = GzDecoder::new(std::fs::File::open(src)?);
    let mut w = std::fs::File::create(dst)?;
    std::io::copy(&mut r, &mut w)?;
    w.sync_all()?;
    Ok(())
}

/// 最小日志：写 /tmp/loadn-desktop-vm.log（真机联调的生命线）
fn log_line(msg: &str) {
    let _ = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(std::env::temp_dir().join("loadn-desktop-vm.log"))
        .and_then(|mut f| writeln!(f, "{msg}"));
}

mod relay {
    // 127.0.0.1:<port> ↔ vfkit vsock unix socket 的裸流中继（双向拷贝线程）。
    use std::net::TcpListener;
    use std::os::unix::net::UnixStream;
    use std::path::Path;
    use std::sync::Arc;

    pub fn spawn(sock: impl AsRef<Path>, port: u16) {
        let sock = Arc::new(sock.as_ref().to_path_buf());
        std::thread::spawn(move || {
            let listener = match TcpListener::bind(("127.0.0.1", port)) {
                Ok(l) => l,
                Err(_) => return, // 已有中继在跑（重复 start 幂等）
            };
            for stream in listener.incoming() {
                let Ok(tcp) = stream else { continue };
                let sock = sock.clone();
                std::thread::spawn(move || {
                    let Ok(unix) = UnixStream::connect(&*sock) else { return };
                    let _ = pump(tcp, unix);
                });
            }
        });
    }

    fn pump(mut a: std::net::TcpStream, mut b: UnixStream) -> std::io::Result<()> {
        let mut a2 = a.try_clone()?;
        let mut b2 = b.try_clone()?;
        let t = std::thread::spawn(move || std::io::copy(&mut b2, &mut a2).map(|_| ()));
        std::io::copy(&mut a, &mut b)?;
        let _ = t.join();
        Ok(())
    }
}
