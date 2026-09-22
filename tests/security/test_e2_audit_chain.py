"""E2 对抗用例（§8）：审计账本防篡改——断链定位 + 整库重算被锚点暴露。

三条攻击路径全覆盖：
1. 单行篡改内容 → 逐行重算定位行号
2. 删行 → prev_hash 断链
3. 整库重算重写（攻击者把链算得天衣无缝）→ 外置锚点比对暴露
"""
from __future__ import annotations

import sqlite3
import stat

import pytest

from loadn_webui import audit as audit_mod


def _t() -> str:
    """当前月分表名（测试写库的真实表）。"""
    from datetime import datetime, timezone
    return "audit_events_" + datetime.now(timezone.utc).strftime("%Y%m")


@pytest.fixture()
def fresh_audit(tmp_path, monkeypatch):
    """独立账本（临时 var）+ 重置模块级缓存态。"""
    monkeypatch.setenv("LOADN_WEBUI_HOME", str(tmp_path))
    monkeypatch.setattr(audit_mod, "_initialized", False)
    monkeypatch.setattr(audit_mod, "_last_anchor_day", "")
    from loadn_webui.config import PATHS
    monkeypatch.setitem(PATHS, "var", tmp_path / "var")
    for i in range(5):
        audit_mod.audit("permission_decision",
                        {"action": "allow", "i": i, "subject": f"cmd{i}"})
    yield tmp_path


def test_e2_chain_healthy_and_anchored(fresh_audit):
    assert audit_mod.verify() == []
    assert len(audit_mod.anchors()) >= 1            # 写入即锚点（当日首写）
    f = fresh_audit / "var" / "audit_heads"
    for p in f.glob("*.txt"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_e2_single_row_tamper_located(fresh_audit):
    db = fresh_audit / "var" / "audit.db"
    c = sqlite3.connect(db)
    c.execute(f"UPDATE {_t()} SET detail_json='{{\"i\":99,\"hacked\":true}}' "
              "WHERE id=3")
    c.commit(); c.close()
    problems = audit_mod.verify()
    assert any("行 3" in p and "hash 不符" in p for p in problems)


def test_e2_row_deletion_breaks_chain(fresh_audit):
    db = fresh_audit / "var" / "audit.db"
    c = sqlite3.connect(db)
    c.execute(f"DELETE FROM {_t()} WHERE id=3")
    c.commit(); c.close()
    problems = audit_mod.verify()
    assert any("断链" in p for p in problems)


def test_e2_full_rechain_exposed_by_anchor(fresh_audit):
    """整库重算攻击：链内部完美，但锚点时刻的链头从账本消失。"""
    db = fresh_audit / "var" / "audit.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = list(c.execute(f"SELECT * FROM {_t()} ORDER BY id"))
    # 攻击：篡改行 2 内容并把整链重算得天衣无缝
    rows[1] = dict(rows[1])
    rows[1]["detail_json"] = '{"i": 2, "evil": true}'
    prev = audit_mod.GENESIS
    for r in rows:
        h = audit_mod._row_hash(prev, audit_mod._canonical(
            r["ts"], r["sid"], r["turn_id"], r["type"], r["detail_json"]))
        c.execute(f"UPDATE {_t()} SET prev_hash=?, hash=? WHERE id=?",
                  (prev, h, r["id"]))
        prev = h
    c.commit(); c.close()
    problems = audit_mod.verify()
    assert any("锚点" in p for p in problems), (
        "整库重算必须被外置锚点暴露——这是纯哈希链防不住的攻击面")
