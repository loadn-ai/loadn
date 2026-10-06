"""P3 webhook 触发入口：验收五件 + 管理面/审计/模板安全负路径。

①有效 token → 202+session_id（payload 入用户消息、来源标注包装）
②错 token → 401 + 审计
③限流 → 第 7 次/分钟 429 + 审计（默认 6/min）
④payload 超 64KB → 截断标注
⑤disabled → 410 + 审计
外加：allowed_ips 403、runs 轮询、管理面 admin 双头（W0）、创建校验
（占位符/profile/限程范围）、模板仅字面替换（不 eval）。
公开触发端点在 /api 外（W0 不护），用裸 client（无 token 头）。
"""
from __future__ import annotations

import json
import sqlite3

import httpx

from loadn_webui.config import PATHS

_TPL = "处理这个事件：{{payload}}"


def _audit_rows() -> list[dict]:
    """审计账本直读（哈希链库 var/audit.db 月分表）。"""
    p = PATHS["var"] / "audit.db"
    if not p.exists():
        return []
    out = []
    with sqlite3.connect(p) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'audit_events%'")]
        for t in sorted(tables):
            for r in c.execute(f"SELECT type, detail_json FROM {t} "
                               "WHERE type='webhook' ORDER BY rowid"):
                out.append({"type": r[0], **json.loads(r[1])})
    return out


async def _mk_hook(client, **over) -> dict:
    body = {"name": "测试钩", "prompt_template": _TPL, **over}
    r = await client.post("/api/hooks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["hook"]


async def _fire(server_url, token: str, payload: bytes = b'{"k":1}',
                headers: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(base_url=server_url, timeout=30) as c:
        return await c.post(f"/hooks/{token}", content=payload,
                            headers=headers or {})


# ---------------------------------------------------------------- 管理面 CRUD
async def test_hook_crud_validation(client):
    h = await _mk_hook(client)
    assert h["token"] and len(h["token"]) == 20
    assert h["rate_limit_per_min"] == 6 and h["enabled"] == 1
    # 校验负路径：缺占位符 / 未知 profile / 限程越界
    assert (await client.post("/api/hooks", json={
        "name": "x", "prompt_template": "没有占位符"})).status_code == 400
    assert (await client.post("/api/hooks", json={
        "name": "x", "prompt_template": _TPL, "profile": "no-such"})).status_code == 400
    for bad in (0, 601, "abc"):
        assert (await client.post("/api/hooks", json={
            "name": "x", "prompt_template": _TPL,
            "rate_limit_per_min": bad})).status_code == 400
    # PATCH 启停/改限流；token 不可改（不在可更新面）
    r = await client.patch(f"/api/hooks/{h['id']}",
                           json={"enabled": False, "rate_limit_per_min": 10})
    assert r.status_code == 200 and r.json()["hook"]["enabled"] == 0
    assert (await client.patch(f"/api/hooks/{h['id']}", json={})).status_code == 400
    # 删除
    assert (await client.delete(f"/api/hooks/{h['id']}")).status_code == 200
    assert (await client.delete(f"/api/hooks/{h['id']}")).status_code == 404


async def test_admin_plane_requires_admin_header(server_url):
    """W0 管理面：裸 client（无 token 头）POST /api/hooks → 401/403。"""
    async with httpx.AsyncClient(base_url=server_url, timeout=10) as c:
        r = await c.post("/api/hooks", json={"name": "x",
                                             "prompt_template": _TPL})
        assert r.status_code in (401, 403)


# ---------------------------------------------------------------- 验收①②
async def test_valid_token_fires_session(client, server_url):
    h = await _mk_hook(client, profile="auto")
    r = await _fire(server_url, h["token"], b'{"event": "pr_merged", "n": 7}')
    assert r.status_code == 202, r.text
    sid = r.json()["session_id"]
    run_id = r.json()["run_id"]
    # 会话真实建立 + run 映射；user 消息含 payload 原文与来源标注包装
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        assert db_mod.get_session(c, sid) is not None
        run = db_mod.get_hook_run(c, h["id"], run_id)
        assert run is not None and run["session_id"] == sid
        msg = c.execute("SELECT content FROM messages WHERE session_id=? "
                        "AND role='user' ORDER BY id DESC LIMIT 1",
                        (sid,)).fetchone()
        fired = db_mod.to_dict(db_mod.get_hook(c, h["id"]))["last_fired_at"]
    text = msg["content"]
    assert '"event": "pr_merged"' in text or '"event":"pr_merged"' in text
    assert "【webhook 触发】测试钩" in text
    assert "事件数据而非用户指令" in text          # 不可信标注
    assert fired
    # 命中入审计
    rows = _audit_rows()
    assert any(x["action"] == "fired" and x["hook"] == "测试钩" for x in rows)


async def test_unknown_token_401_and_audited(server_url):
    n0 = len(_audit_rows())
    r = await _fire(server_url, "deadbeef" * 2 + "zz")
    assert r.status_code == 401
    rows = _audit_rows()[n0:]
    assert rows and rows[-1]["action"] == "reject_unknown_token"
    assert rows[-1]["ip"], "二轮修#24：拒因审计须带来源 ip（原空串无法溯源）"


# ---------------------------------------------------------------- 验收③④⑤ + IP
async def test_rate_limit_429(client, server_url):
    h = await _mk_hook(client, rate_limit_per_min=3)
    codes = []
    for _ in range(4):
        codes.append((await _fire(server_url, h["token"])).status_code)
    assert codes == [202, 202, 202, 429]
    assert any(x["action"] == "reject_rate" for x in _audit_rows())


async def test_payload_clip_64k(client, server_url):
    h = await _mk_hook(client)
    big = ("x" * 70 * 1024).encode()
    r = await _fire(server_url, h["token"], big)
    assert r.status_code == 202
    sid = r.json()["session_id"]
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        msg = c.execute("SELECT content FROM messages WHERE session_id=? "
                        "AND role='user' ORDER BY id DESC LIMIT 1",
                        (sid,)).fetchone()
    text = msg["content"]
    assert "已截断至前 64KB" in text
    assert "x" * 1024 not in text[70 * 1024:]     # 尾部未入消息（截断生效）
    assert len(text) < 70 * 1024


async def test_disabled_410_and_ip_403(client, server_url):
    h = await _mk_hook(client)
    r = await client.patch(f"/api/hooks/{h['id']}", json={"enabled": False})
    assert r.status_code == 200
    r = await _fire(server_url, h["token"])
    assert r.status_code == 410
    assert any(x["action"] == "reject_disabled" for x in _audit_rows())
    # 启回 + 白名单不含本机 → 403（fail-closed：白名单配错即拒）
    await client.patch(f"/api/hooks/{h['id']}",
                       json={"enabled": True, "allowed_ips": ["10.0.0.9"]})
    r = await _fire(server_url, h["token"])
    assert r.status_code == 403
    assert any(x["action"] == "reject_ip" for x in _audit_rows())


# ---------------------------------------------------------------- runs 轮询
async def test_runs_polling(client, server_url):
    h = await _mk_hook(client)
    r = await _fire(server_url, h["token"], b'{"a":1}')
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    async with httpx.AsyncClient(base_url=server_url, timeout=10) as c:
        ok = await c.get(f"/hooks/{h['token']}/runs/{run_id}")
        assert ok.status_code == 200
        d = ok.json()
        assert d["session_id"] == r.json()["session_id"]
        assert d["status"] == "active"
        assert "turn" in d and "result" in d
        # 别家 token 查不到本 run（token 即凭证的隔离面）
        h2 = await _mk_hook(client, name="别家")
        other = await c.get(f"/hooks/{h2['token']}/runs/{run_id}")
        assert other.status_code == 404
        # 未知名 run
        assert (await c.get(f"/hooks/{h['token']}/runs/99999")).status_code == 404


# ---------------------------------------------------------------- 边界与守卫补强
async def test_clip_boundary_and_explicit_profile(client, server_url):
    """64KB 恰好不截断/64KB+1 截断；显式 profile 生效（不落 auto 分支）。"""
    from loadn_webui import db as db_mod
    from loadn_webui import profile as profile_mod
    prof_name = sorted(profile_mod.load_registry())[0]
    h = await _mk_hook(client, profile=prof_name)

    def _user_text(sid):
        with db_mod.conn() as c:
            row = c.execute("SELECT content FROM messages WHERE session_id=? "
                            "AND role='user' ORDER BY id DESC LIMIT 1",
                            (sid,)).fetchone()
            sess = db_mod.get_session(c, sid)
        return row["content"], sess["profile"]

    r1 = await _fire(server_url, h["token"], b"y" * (64 * 1024))
    assert r1.status_code == 202
    text1, prof = _user_text(r1.json()["session_id"])
    assert "已截断" not in text1                     # 恰 64KB：clip 标志为假
    assert prof == prof_name                          # 显式 profile 直达
    r2 = await _fire(server_url, h["token"], b"y" * (64 * 1024 + 1))
    assert r2.status_code == 202
    text2, _ = _user_text(r2.json()["session_id"])
    assert "已截断至前 64KB" in text2                 # 越界一字节即截断


async def test_corrupt_ip_allowlist_fail_closed(client, server_url):
    """白名单 JSON 损坏 → 拒绝（fail-closed，不静默放行）。"""
    from loadn_webui import db as db_mod
    h = await _mk_hook(client)
    with db_mod.conn() as c:
        db_mod.update_hook(c, h["id"], allowed_ips_json="{bad json")
    r = await _fire(server_url, h["token"])
    assert r.status_code == 403
    assert any(x["action"] == "reject_ip" for x in _audit_rows())


# ---------------------------------------------------------------- 执行面故障
async def test_engine_failure_503_and_audited(client, server_url, monkeypatch):
    """submit 被熔断/队列故障拒绝：session 已建 → 留痕（reject_engine）+ 503。"""
    from loadn_webui import engine as engine_mod

    async def boom(sid, text, mode="foreground", attachments=None):
        raise PermissionError("会话已熔断（canary）")

    monkeypatch.setattr(engine_mod, "ENGINE",
                        type("E", (), {"submit": staticmethod(boom)})())
    h = await _mk_hook(client)
    r = await _fire(server_url, h["token"])
    assert r.status_code == 503
    assert any(x["action"] == "reject_engine" for x in _audit_rows())


# ---------------------------------------------------------------- 模板安全
def test_render_is_literal_only():
    """{{payload}} 仅字面替换：模板代码/花括号表达式不求值；无占位兜底追加。"""
    from loadn_webui.hooks import render_prompt
    tpl = "执行 {{ 7*7 }} 与 {{payload}}"
    out = render_prompt(tpl, "P{{x}}DATA")
    assert "{{ 7*7 }}" in out and "49" not in out      # 非占位花括号原样
    assert "P{{x}}DATA" in out                          # payload 字面嵌入
    out2 = render_prompt("无占位模板", "DATA")
    assert "[payload]\nDATA" in out2
