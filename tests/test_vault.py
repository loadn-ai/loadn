"""账号保险库（P0-2）：schema 白名单 / 0600 / 打码视图 / CLI 语义。"""
import json
import os
import stat

from loadn_webui import vault
from loadn_webui.cli import main


def test_put_get_mask_delete():
    vault.put("Google", username="user@example.com", password="s3cret",
              email="alias@example.com", status="ok", notes="别名 king 同一账号")
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


def test_cli_roundtrip(capsys):
    # 写入 → 单字段（脚本取值模式，stdout 只有值）→ 概览
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
