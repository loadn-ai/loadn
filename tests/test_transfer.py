"""文件直通道（P1-3）：ingest 端点白名单/重命名/事件 + CLI push/pull 回路。"""

import pytest

from loadn_webui.cli import main


@pytest.mark.asyncio()
async def test_ingest_endpoint(client, ws_root):
    r = await client.post("/api/sessions", json={"title": "直传测试"})
    sid = r.json()["session"]["id"]

    # 白名单外目录 → 400
    r = await client.post(f"/api/sessions/{sid}/ingest",
                          params={"to": "../etc/"},
                          files={"file": ("a.pdf", b"%PDF-fake", "application/pdf")})
    assert r.status_code == 400

    r = await client.post(f"/api/sessions/{sid}/ingest",
                          params={"to": "artifacts/"},
                          files={"file": ("cert.pdf", b"%PDF-fake-1", "application/pdf")})
    assert r.status_code == 200
    d = r.json()
    assert d["path"] == "artifacts/cert.pdf"
    assert (ws_root / sid / "artifacts" / "cert.pdf").read_bytes() == b"%PDF-fake-1"

    # 同名自动重命名（不覆盖既有产物）
    r = await client.post(f"/api/sessions/{sid}/ingest",
                          params={"to": "artifacts/"},
                          files={"file": ("cert.pdf", b"%PDF-fake-2", "application/pdf")})
    assert r.json()["path"] == "artifacts/cert-1.pdf"
    assert (ws_root / sid / "artifacts" / "cert.pdf").read_bytes() == b"%PDF-fake-1"


def test_cli_file_roundtrip(server_url, client, ws_root, tmp_path, monkeypatch, capsys):
    import httpx
    # 起一个真 server 的会话（client fixture 已确保 server 在跑）
    r = httpx.post(f"{server_url}/api/sessions", json={"title": "CLI 直传"})
    sid = r.json()["session"]["id"]
    monkeypatch.setenv("LOADN_SESSION_ID", sid)
    # CLI 默认打 127.0.0.1:<config.port>，测试 server 在随机端口——指过去
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.server, "port", int(server_url.rsplit(":", 1)[1]))

    src = tmp_path / "report.pdf"
    src.write_bytes(b"%PDF-cli-push")
    assert main(["r", "file", "push", str(src), "--to", "notes/"]) == 0
    assert "notes/report.pdf" in capsys.readouterr().out
    assert (ws_root / sid / "notes" / "report.pdf").read_bytes() == b"%PDF-cli-push"

    out = tmp_path / "pulled.pdf"
    assert main(["r", "file", "pull", "notes/report.pdf", "--out", str(out)]) == 0
    assert out.read_bytes() == b"%PDF-cli-push"
