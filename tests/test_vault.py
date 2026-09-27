"""账号保险库（P0-2）：schema 白名单 / 0600 / 打码视图 / CLI 语义。"""
import json
import os
import stat

from loadn_webui import vault
from loadn_webui.cli import main


def test_put_get_mask_delete():
    vault.put("Google", username="user@example.com", password="s3cret",
              email="alias@example.com", status="ok", notes="别名同一账号")
    # platform 大小写/空格规范化
    e = vault.get("google")
    assert e["password"] == "s3cret" and e["username"] == "user@example.com"

    # 默认视图打码；reveal 全量
    v = vault.view("google")
    assert "s3cret" not in json.dumps(v)
    assert "••••" in v["password"]
    assert vault.view("google", reveal=True)["password"] == "s3cret"

    # 列表概览不泄密
    rows = vault.list_platforms()
    assert rows[0]["platform"] == "google" and rows[0]["has_password"] is True
    assert "s3cret" not in json.dumps(rows)

    # 单字段（登录脚本取用）
    assert vault.field("google", "password") == "s3cret"
    assert vault.field("google", "email") == "alias@example.com"
    assert vault.field("google", "twofa") is None

    # 白名单外字段拒绝
    try:
        vault.put("google", passwd="x")
        raise AssertionError("应当抛 ValueError")
    except ValueError as e:
        assert "passwd" in str(e)

    assert vault.delete("google") is True and vault.delete("google") is False


def test_file_permissions_atomic():
    vault.put("testplat", password="p1")
    p = vault.vault_path()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    # 增量 merge 不丢字段
    vault.put("testplat", status="suspended")
    e = vault.get("testplat")
    assert e["password"] == "p1" and e["status"] == "suspended"
    assert vault.delete("testplat")


def test_cli_roundtrip(capsys, monkeypatch):
    # 写入 → 单字段（脚本取值模式，stdout 只有值）→ 概览
    # （审批门单测另见 test_a3；此处测 vault 本体——终端直调形态）
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "approval_enforce", "warn")
    monkeypatch.delenv("LOADN_SESSION_ID", raising=False)
    monkeypatch.delenv("LOADN_SESSION_ID", raising=False)
    monkeypatch.delenv("LOADN_PROJECT_ID", raising=False)
    monkeypatch.delenv("LOADN_PROJECT_ID", raising=False)
    assert main(["r", "account", "--platform", "hubspot",
                 "--set", "password=Hs!2026", "--set", "username=me@x.com"]) == 0
    capsys.readouterr()
    assert main(["r", "account", "--platform", "hubspot",
                 "--field", "password"]) == 0
    assert capsys.readouterr().out.strip() == "Hs!2026"
    assert main(["r", "account", "--list"]) == 0
    out = capsys.readouterr().out
    assert "hubspot" in out and "Hs!2026" not in out
    # 未知条目取字段 → 非零退出（脚本 $(...) 取到空串即失败可见）
    assert main(["r", "account", "--platform", "nope", "--field", "password"]) == 1
    assert main(["r", "account", "--platform", "hubspot", "--del"]) == 0


# ---------------------------------------------------------------- 管理面编辑端点
async def test_admin_vault_editor_roundtrip(client):
    """安全中心可视化编辑：掩码视图 / merge 写（留空不改·空串清除）/ 删除。"""
    from loadn_webui import vault as v
    v.put("editor-test", username="old@x.com", password="S3cret!", notes="n1")

    d = (await client.get("/api/admin/vault/editor-test")).json()
    assert d["username"] == "old@x.com"
    assert "S3cret" not in json.dumps(d)                 # 掩码：明文不出 HTTP
    assert "已设置" in d["password"] or "位" in d["password"]

    # merge：改 username、设 recovery、不动 password（未送）
    r = await client.put("/api/admin/vault/editor-test",
                         json={"fields": {"username": "new@x.com", "recovery": "RC-1"}})
    assert r.status_code == 200 and set(r.json()["set"]) == {"username", "recovery"}
    e = v.view("editor-test", reveal=True)
    assert e["username"] == "new@x.com" and e["password"] == "S3cret!"
    assert e["recovery"] == "RC-1"

    # 空串=清除
    await client.put("/api/admin/vault/editor-test",
                     json={"fields": {"notes": ""}})
    assert v.view("editor-test", reveal=True)["notes"] == ""

    # 校验：未知字段/空 fields/非串值 → 400
    for bad in ({"fields": {"hack": 1}}, {"fields": {}}, {"fields": {"username": 9}},
                {"nofields": True}):
        r = await client.put("/api/admin/vault/editor-test", json=bad)
        assert r.status_code == 400, bad

    # 删除 → 404 重复删
    r = await client.delete("/api/admin/vault/editor-test")
    assert r.status_code == 200
    assert (await client.delete("/api/admin/vault/editor-test")).status_code == 404
    assert v.get("editor-test") is None


def test_decrypt_min_frame_boundary():
    """34B 恰好最小帧长：过格式门进 AESGCM（InvalidTag），不误报 LDV1
    （L89 常数+1 对赌——门槛边界精确性）。"""
    import pytest as _pytest
    from cryptography.exceptions import InvalidTag

    from loadn_webui.vault import _decrypt
    blob = b"LDV1" + b"\x00\x00" + b"\x00" * 12 + b"\x00" * 16   # =34B
    assert len(blob) == 34
    with _pytest.raises(InvalidTag):
        _decrypt(blob)                    # 过门后 GCM 校验失败（非 LDV1 错）
