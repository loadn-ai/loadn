"""守卫否定路径对赌（突变抽查暴露的两个存活变异的补测）。

突变测试实录（2026-09-25，4 变异 2 存活——都死在「守卫否定路径」无
单元级对赌，对赌用例只在 bwrap-gated 的 e2e full_stack 里，本机全量
恒 skip 等于从未验证）：

- MUTANT-2 存活：approve.consume 的确认码校验跳过 → 55 测试全绿
- MUTANT-3 存活：edit._write_guarded 的 mtime 守卫跳过 → 28 测试全绿

本文件补齐这两面的否定路径（不需要 bwrap——单元级直接打）：
A. 确认码门：错码拒/空码拒/码 hash 不符拒/重复消费拒/TTL 过期拒/
   批准 A 不能执行 B（params_hash 绑定）
B. mtime 守卫：外部改动后写入必须被拒（文件保持外部版本）
"""
from __future__ import annotations

import os
import time

import pytest


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOADN_WEBUI_HOME", str(home))
    import loadn_webui.security.approve as ap
    ap._ensured = False
    return home


# ================================================================ A. 确认码门
def _mk_approved(ap, sid="s-gate", params=None):
    params = params or {"to": "a@b.c", "subject": "测试"}
    r = ap.create(sid, "mail_send", params)
    d = ap.decide(r["id"], True)
    assert d["ok"] and d.get("code")
    return r["id"], params, d["code"]


def test_consume_wrong_code_rejected(tmp_path, _home):
    """错码 → 拒（不可逆动作不执行——MUTANT-2 对赌）。"""
    from loadn_webui.security import approve as ap
    aid, params, code = _mk_approved(ap)
    out = ap.consume("s-gate", "mail_send", params, confirm_code="000000")
    assert not out["ok"] and "确认码不符" in out["error"]


def test_consume_empty_code_rejected(tmp_path, _home):
    from loadn_webui.security import approve as ap
    aid, params, code = _mk_approved(ap)
    out = ap.consume("s-gate", "mail_send", params, confirm_code="")
    assert not out["ok"]


def test_consume_right_code_executes_then_single_use(tmp_path, _home):
    """对码 → 放行；同码二次消费 → 拒（single-use）。"""
    from loadn_webui.security import approve as ap
    aid, params, code = _mk_approved(ap)
    ok1 = ap.consume("s-gate", "mail_send", params, confirm_code=code)
    assert ok1["ok"]
    ok2 = ap.consume("s-gate", "mail_send", params, confirm_code=code)
    assert not ok2["ok"]                        # 一次性：重放拒


def test_consume_params_mismatch_rejected(tmp_path, _home):
    """批准 A 不能执行 B（params_hash 绑定——换收件人即拒）。"""
    from loadn_webui.security import approve as ap
    aid, params, code = _mk_approved(
        ap, params={"to": "a@b.c", "subject": "原目标"})
    swapped = {"to": "evil@x.y", "subject": "原目标"}   # 篡改收件人
    out = ap.consume("s-gate", "mail_send", swapped, confirm_code=code)
    assert not out["ok"] and "参数不符" in out["error"]


def test_consume_unapproved_rejected(tmp_path, _home):
    """未批准直接 consume → 拒（无码可验）。"""
    from loadn_webui.security import approve as ap
    ap.create("s-gate", "mail_send", {"to": "a@b.c"})
    out = ap.consume("s-gate", "mail_send", {"to": "a@b.c"}, confirm_code="1")
    assert not out["ok"]


def test_consume_expired_rejected(tmp_path, _home, monkeypatch):
    """批准超 TTL → expired 拒（fail-closed）。"""
    from loadn_webui.security import approve as ap
    aid, params, code = _mk_approved(ap)
    # 把 created_at 拨回 2 小时前（TTL 默认 600s）
    with ap._conn() as c:
        c.execute("UPDATE approvals SET created_at=? WHERE id=?",
                  ("2020-01-01T00:00:00+00:00", aid))
    out = ap.consume("s-gate", "mail_send", params, confirm_code=code)
    assert not out["ok"] and "超时" in out["error"]


# ================================================================ B. mtime 守卫
async def test_edit_rejects_external_change(tmp_path, monkeypatch):
    """Read 后文件被外部改 → Edit 必须拒（写入守卫——MUTANT-3 对赌）。

    拒绝后文件保持**外部版本**（不是 agent 的旧理解、也不是半合并）。
    """
    from loadn.tools.base import ToolContext
    from loadn.tools.edit import EditTool
    from loadn.tools.read import ReadTool

    f = tmp_path / "guarded.py"
    f.write_text("version = 1\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await ReadTool().execute({"file_path": str(f)}, ctx)
    # 外部改动（Read→Edit 窗口内）
    f.write_text("version = 2  # 外部新版本\n", encoding="utf-8")
    os.utime(f, (time.time() + 10, time.time() + 10))  # 强制 mtime 变化
    from loadn.tools.base import ToolError
    with pytest.raises(ToolError, match="外部变更"):
        await EditTool().execute(
            {"file_path": str(f), "old_string": "version = 1",
             "new_string": "version = 1  # agent 改"}, ctx)
    assert f.read_text() == "version = 2  # 外部新版本\n"   # 外部版保住


async def test_write_rejects_stale_read(tmp_path):
    """Write 路径同守卫（已存在文件须先 Read；外部改动后写拒）。"""
    from loadn.tools.base import ToolContext
    from loadn.tools.read import ReadTool
    from loadn.tools.write import WriteTool

    f = tmp_path / "w.txt"
    f.write_text("old\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await ReadTool().execute({"file_path": str(f)}, ctx)
    f.write_text("changed externally\n", encoding="utf-8")
    os.utime(f, (time.time() + 10, time.time() + 10))  # 强制 mtime 变化
    from loadn.tools.base import ToolError
    with pytest.raises(ToolError, match="外部变更"):
        await WriteTool().execute(
            {"file_path": str(f), "content": "agent overwrite\n"}, ctx)
    assert f.read_text() == "changed externally\n"     # 外部版保住


# ---------------------------------------------------------------- M1-approve 突变补测
@pytest.mark.parametrize("action,params,must_contain", [
    ("mail_send", {"to": "a@b.c", "subject": "标题"}, ["a@b.c", "标题"]),
    ("account_write", {"platform": "google", "user": "u"}, ["google", "user"]),
    ("sms_send", {"to": "138", "text": "内容"}, ["138", "内容"]),
    ("pay", {"amount": "99", "to": "merchant"}, ["99", "merchant"]),
    ("wechat_send", {"to": "wxid", "text": "hello"}, ["wxid", "hello"]),
    ("bash_allow", {"prefix": ["pip", "install"]}, ["pip install"]),
    ("browser_open", {"host": "x.example.com"}, ["x.example.com"]),
    ("egress", {"host": "api.example.com", "ttl_s": 7200},
     ["api.example.com", "2h00m"]),
])
def test_render_summary_per_action(action, params, must_contain):
    """每类动作的摘要必须含关键目标（反注入面——L60-86 分支对赌）。"""
    from loadn_webui.security import approve as ap
    s = ap._render_summary(action, params)
    for needle in must_contain:
        assert needle in s, (action, needle, s)


def test_render_summary_account_write_excludes_platform_key():
    from loadn_webui.security import approve as ap
    s = ap._render_summary("account_write", {"platform": "p", "password": "x"})
    assert "password" in s and "platform" not in s.split("（")[1]


def test_render_summary_bash_allow_none_prefix():
    """prefix=None 走 or [] 回退不崩（prefix or [] 对赌）。"""
    from loadn_webui.security import approve as ap
    s = ap._render_summary("bash_allow", {"prefix": None})
    assert "?" in s or "放行命令规则" in s


def test_render_summary_egress_default_ttl():
    from loadn_webui.security import approve as ap
    s = ap._render_summary("egress", {"host": "h.example"})   # 无 ttl_s
    assert "2h" in s                                        # 默认 7200=2h00m
