// 回环 http GET（健康探测）。endpoint 恒为 127.0.0.1 明文——不需要 TLS，
// 也就不需要 rustls/ring（保住三平台 cargo check 零交叉 C 工具链）。
use anyhow::{Context, Result};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

pub fn get_json(host: &str, port: u16, path: &str, timeout: Duration) -> Result<serde_json::Value> {
    let mut s = TcpStream::connect((host, port)).context("连接执行域")?;
    s.set_read_timeout(Some(timeout))?;
    s.set_write_timeout(Some(timeout))?;
    write!(s, "GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n")?;
    let mut buf = Vec::new();
    s.read_to_end(&mut buf)?;
    let text = String::from_utf8_lossy(&buf);
    let body = text
        .split_once("\r\n\r\n")
        .context("健康响应畸形（无头体分隔）")?
        .1;
    // 处理 Content-Length 截断与 chunked 之外的常见形态：直接 parse，失败再截到 } 收尾
    serde_json::from_str(body.trim())
        .or_else(|_| {
            let cut = body
                .rfind('}')
                .with_context(|| "健康响应不是 JSON（无对象收尾）")?;
            serde_json::from_str(&body[..=cut]).with_context(|| "健康响应尾部 JSON 解析失败")
        })
        .with_context(|| format!("健康响应不是 JSON：{}", &body[..body.len().min(200)]))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;

    #[test]
    fn get_json_parses_and_tolerates_trailing_noise() {
        let l = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = l.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let (mut s, _) = l.accept().unwrap();
            let mut buf = [0u8; 512];
            let mut seen = Vec::new();
            // 读完整个请求头再响应——否则关 socket 时残留字节触发 RST
            while !seen.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = std::io::Read::read(&mut s, &mut buf).unwrap();
                if n == 0 { break; }
                seen.extend_from_slice(&buf[..n]);
            }
            let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Length: 29\r\n\r\n{{\"release\":{{\"version\":\"v1\"}}}}X");
            let _ = s.shutdown(std::net::Shutdown::Both);
        });
        let v = get_json("127.0.0.1", port, "/", Duration::from_secs(3)).unwrap();
        assert_eq!(v["release"]["version"], "v1");
    }
}
