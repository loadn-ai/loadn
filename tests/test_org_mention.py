"""DW-12：@ 员工提及——选择计数与「次数 × 时间衰减」加权排序。"""
from __future__ import annotations

import time

from loadn_webui import db as db_mod
from loadn_webui.org import bump_mention, mention_scores


def _seed_emp(c, name: str, dept: str = "研发部") -> None:
    d = c.execute("SELECT id FROM org_departments WHERE name=?", (dept,)).fetchone()
    c.execute(
        "INSERT INTO org_employees(dept_id, name, title, duty, created_at, updated_at)"
        " VALUES(?,?, '工程师', '干活', datetime('now'), datetime('now'))",
        (d["id"] if d else None, name))


def _backdate(c, name: str, days: float) -> None:
    """把该员工的最近选择拨回 N 天前（测时间衰减）。"""
    c.execute("UPDATE org_mentions SET last_ts=? WHERE name=?",
              (time.time() - days * 86400, name))


async def test_bump_and_rank_weighting(client):
    with db_mod.conn() as c:
        _seed_emp(c, "甲"); _seed_emp(c, "乙"); _seed_emp(c, "丙")
        # 甲：选 5 次（最近）；乙：选 3 次（最近）；丙：选 40 次但 30 天前
        for _ in range(5):
            bump_mention(c, "甲")
        for _ in range(3):
            bump_mention(c, "乙")
        for _ in range(40):
            bump_mention(c, "丙")
        _backdate(c, "丙", 30)
        scores = mention_scores(c)
        # 频次主导：甲(5+近期) > 乙(3+近期)；丙 cnt 封顶 50 + 30 天衰减 ≈ 50.0x
        assert scores["甲"] > scores["乙"] > 0
        # 近期项两周半衰：30 天前 ≈ 8*e^(-30/14) ≈ 0.9 → 丙 ≈ 40.9 仍居首（次数多）
        assert scores["丙"] > scores["甲"]
        assert 40 < scores["丙"] < 43


async def test_bump_rejects_unknown_employee(client):
    with db_mod.conn() as c:
        try:
            bump_mention(c, "不存在的人")
        except ValueError as e:
            assert "不存在" in str(e)
        else:
            raise AssertionError("未知员工应抛 ValueError")


async def test_mention_endpoints(client):
    with db_mod.conn() as c:
        _seed_emp(c, "丁"); _seed_emp(c, "戊")
        for _ in range(2):
            bump_mention(c, "丁")
    # 浮层列表：在职平铺 + 加权预排序（丁 2 次 > 戊 0 次）
    d = (await client.get("/api/org/mention-list")).json()
    names = [e["name"] for e in d["employees"]]
    assert "丁" in names and "戊" in names
    assert names.index("丁") < names.index("戊")
    assert {"name", "dept", "title", "duty", "score"} <= set(d["employees"][0])
    # 上报：计数 +1 且返回新分
    r = await client.post("/api/org/mention", json={"name": "丁"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "丁" and r.json()["score"] > 0
    # 非法名 404；空名 400
    assert (await client.post("/api/org/mention",
                              json={"name": "路人"})).status_code == 404
    assert (await client.post("/api/org/mention", json={})).status_code == 400
