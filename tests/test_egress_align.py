"""放行策略放宽：egress 两道门对齐 + shared_readonly 跨项目只读共享。

用户痛点（2026-09-25）：
①「全局放行了域名，curl 还是被拦」——hook 门（policy-check）只认全局
  白名单一张表且沙箱内读不到数据根 config.yaml（出厂默认表误拦）、
  不消费 egress_mode/会话档；proxy 门语义完整。两道门割裂。
②「跨项目隔离后无法让新任务读其他任务数据」——bwrap 挂载矩阵是唯一
  硬边界，无任何共享机制。

验收：
- hook 门与 proxy 门同面：mode off/warn → curl 域检查不拦；enforce 才拦
- 会话快照 .loadn/egress.json：hook 回退链快照 > CONFIG（沙箱形态）；
  会话 params.egress 档并入快照（PATCH/rematerialize 两路）
- 白名单读路径归一化：带 scheme/端口/通配符的手编条目能命中
- UI 放行域名 → rematerialize 刷新活跃会话快照
- shared_readonly：bwrap argv 含同路径 ro-bind；session_env 指路
  LOADN_SHARED_RO（resolve 口径与挂载一致）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadn_webui.config import CONFIG


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_allow",
                        ["open.bigmodel.cn"])
    return monkeypatch


# ---------------------------------------------------------------- 归一化
def test_normalize_allow_entry():
    from loadn_webui.security.net_policy import normalized_allow
    assert normalized_allow([
        "https://api.example.com:8443/x",   # scheme+端口+路径
        "*.cdn.example.org",                # 通配符 → 后缀语义基名
        "GitHub.COM.",                      # 大写+尾点
        "api.example.com",                  # 正常形态
        "not a host",                       # 非法 → 丢弃
        "",                                 # 空 → 丢弃
    ]) == ["api.example.com", "cdn.example.org", "github.com"]


def test_proxy_allowed_uses_normalized(monkeypatch):
    from loadn_webui.security import egress_proxy as ep
    monkeypatch.setattr(CONFIG.security, "egress_allow",
                        ["https://git.example.com:443/"])
    assert ep._allowed("git.example.com") is True
    assert ep._allowed("www.git.example.com") is True      # 后缀语义
    assert ep._allowed("other.com") is False


# ---------------------------------------------------------------- hook 门对齐
def _decide(cmd: str, monkeypatch, chdir: Path | None = None):
    from loadn_webui.security import policy as pol
    if chdir is not None:
        monkeypatch.chdir(chdir)
    return pol.check_command(cmd, source="hook")


def test_hook_off_warn_not_blocked(tmp_path, _env):
    """off/warn 档：curl 非白名单域也不在命令级拦（proxy 层 warn 记事件）。"""
    for mode in ("off", "warn"):
        _env.setattr(CONFIG.security, "egress_mode", mode)
        d = _decide("curl https://evil.example/x", _env, tmp_path)
        assert d.ok, f"{mode} 档不该拦 curl"


def test_hook_enforce_blocks_unknown_domain(tmp_path, _env):
    d = _decide("curl https://evil.example/x", _env, tmp_path)
    assert not d.ok and "白名单" in d.reason


def test_hook_enforce_allows_whitelisted(tmp_path, _env):
    d = _decide("curl https://open.bigmodel.cn/v1", _env, tmp_path)
    assert d.ok


def test_hook_reads_session_snapshot(tmp_path, _env):
    """沙箱形态：cwd 有 .loadn/egress.json 快照 → hook 读快照不读 CONFIG。"""
    (tmp_path / ".loadn").mkdir()
    (tmp_path / ".loadn" / "egress.json").write_text(json.dumps({
        "mode": "off", "allow": ["anything.example"]}), encoding="utf-8")
    d = _decide("curl https://not-in-config.example/x", _env, tmp_path)
    assert d.ok                              # 快照 off 档覆盖 CONFIG enforce


def test_hook_snapshot_enforce_still_gates(tmp_path, _env):
    (tmp_path / ".loadn").mkdir()
    (tmp_path / ".loadn" / "egress.json").write_text(json.dumps({
        "mode": "enforce", "allow": ["good.example"]}), encoding="utf-8")
    assert _decide("curl https://good.example/x", _env, tmp_path).ok
    d = _decide("curl https://bad.example/x", _env, tmp_path)
    assert not d.ok and "good.example" not in d.reason


# ---------------------------------------------------------------- 快照物化
def test_write_egress_snapshot(tmp_path, _env):
    from loadn_webui import workspace as ws_mod
    ws_mod.write_egress_snapshot(tmp_path)                 # 全局档
    snap = json.loads((tmp_path / ".loadn" / "egress.json").read_text())
    assert snap["mode"] == "enforce"
    assert "open.bigmodel.cn" in snap["allow"]
    ws_mod.write_egress_snapshot(tmp_path, mode="warn")    # 会话档并入
    snap2 = json.loads((tmp_path / ".loadn" / "egress.json").read_text())
    assert snap2["mode"] == "warn"
    # 非法档位回退全局
    ws_mod.write_egress_snapshot(tmp_path, mode="bogus")
    snap3 = json.loads((tmp_path / ".loadn" / "egress.json").read_text())
    assert snap3["mode"] == "enforce"


def test_ui_allow_refreshes_snapshots(client, monkeypatch, tmp_path):
    """UI 放行域名 → rematerialize 刷新活跃会话快照（mock 不真跑全量）。

    隔离：_conf_path 重定向 tmp（共享测试 yaml 不落 mode=warn——否则
    proxy 热重载会吃掉后续测试的 monkeypatch 档位）；CONFIG 字段先经
    monkeypatch 注册（put_* 的直接赋值也能在 teardown 还原）。
    """
    from loadn_webui import settings_admin as sa
    calls = []
    monkeypatch.setattr(sa, "_refresh_session_snapshots",
                        lambda: calls.append(1))
    monkeypatch.setattr(sa, "_conf_path", lambda: tmp_path / "cfg.yaml")
    monkeypatch.setattr(CONFIG.security, "egress_allow",
                        list(CONFIG.security.egress_allow))
    monkeypatch.setattr(CONFIG.security, "egress_mode",
                        CONFIG.security.egress_mode)
    out = sa.put_egress_allow("add", "fresh.example.com")
    assert out["ok"] and calls                            # 快照刷新被触发
    out2 = sa.put_security_egress({"mode": "warn"})
    assert out2["ok"] and len(calls) == 2


async def test_patch_params_refreshes_snapshot(client, tmp_path):
    """PATCH params egress 档 → 会话快照即时更新（hook 门下一 turn 生效）。"""
    r = await client.post("/api/sessions", json={"title": "快照"})
    sid = r.json()["session"]["id"]
    from loadn_webui import workspace as ws_mod
    ws = ws_mod.ws_of(sid)
    r2 = await client.patch(f"/api/sessions/{sid}",
                            json={"params": {"egress": "off"}})
    assert r2.status_code == 200, r2.text
    snap = json.loads((ws / ".loadn" / "egress.json").read_text())
    assert snap["mode"] == "off"
    # 清除回全局
    r3 = await client.patch(f"/api/sessions/{sid}", json={"params": None})
    assert r3.status_code == 200
    snap2 = json.loads((ws / ".loadn" / "egress.json").read_text())
    assert snap2["mode"] == CONFIG.security.egress_mode


# ---------------------------------------------------------------- 共享挂载
def test_shared_binds_argv(tmp_path, _env):
    from loadn_webui.security import sandbox as sb
    shared = tmp_path / "data" / "papers"
    shared.mkdir(parents=True)
    (shared / "a.txt").write_text("x", encoding="utf-8")
    missing = tmp_path / "nope"
    _env.setattr(CONFIG.security, "shared_readonly",
                 [str(shared), str(missing)])
    argv: list[str] = []
    sb._shared_binds(argv)
    assert argv == ["--ro-bind", str(shared), str(shared)]   # 只挂存在的
    # 文件级也挂
    f = tmp_path / "single.bin"
    f.write_bytes(b"1")
    _env.setattr(CONFIG.security, "shared_readonly", [str(f)])
    argv2: list[str] = []
    sb._shared_binds(argv2)
    assert argv2 == ["--ro-bind", str(f), str(f)]


def test_wrap_loadn_includes_shared(tmp_path, _env, monkeypatch):
    """完整 wrap argv 含共享 ro-bind（不真跑 bwrap——只构造 argv）。"""
    from loadn_webui.security import sandbox as sb
    shared = tmp_path / "pool"
    shared.mkdir()
    _env.setattr(CONFIG.security, "shared_readonly", [str(shared)])
    monkeypatch.setattr(sb, "bwrap_available", lambda: True)
    monkeypatch.setattr(sb.shutil, "which", lambda n: "/usr/bin/bwrap")
    out = sb.wrap_loadn(["/bin/true"], {"PATH": "/usr/bin"},
                        sid_session="s-x", cwd=tmp_path)
    assert out is not None
    joined = " ".join(out)
    assert f"--ro-bind {shared} {shared}" in joined


def test_session_env_shared_ro(tmp_path, _env):
    from loadn_webui import workspace as ws_mod
    real = tmp_path / "real_dir"
    real.mkdir()
    link_dir = tmp_path / "link_dir"
    link_dir.symlink_to(real)                     # symlink 路径须 resolve 同口径
    _env.setattr(CONFIG.security, "shared_readonly",
                 [str(link_dir), str(tmp_path / "gone")])
    env = ws_mod.session_env(tmp_path, "sess-1")
    assert env["LOADN_SHARED_RO"] == str(real)    # resolve 后 + 不存在项剔除


def test_session_env_no_shared_empty(tmp_path, _env):
    from loadn_webui import workspace as ws_mod
    _env.setattr(CONFIG.security, "shared_readonly", [])
    env = ws_mod.session_env(tmp_path, "sess-2")
    assert "LOADN_SHARED_RO" not in env
