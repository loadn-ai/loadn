"""M7b：routes.py 存活变异对赌——scheduler PATCH 局部键语义/触发矩阵/done 门。

573 变异首轮 76%；存活集中在 schedules PATCH（label/prompt/max_fires
键检测、new_session 专属键、触发改期矩阵、done 状态门）——局部 PUT
语义与生命周期门从未有否定路径对赌。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("platform.routes")]


async def _mk_session(client):
    r = await client.post("/api/sessions", json={"title": "m7b 调度"})
    return r.json()["session"]["id"]


async def _mk_cron_job(client):
    sid = await _mk_session(client)
    r = await client.post(f"/api/sessions/{sid}/schedules", json={
        "kind": "message", "label": "旧标签",
        "prompt": "旧提示词", "cron": "0 3 * * *", "max_fires": 10})
    assert r.status_code == 200, r.text
    return r.json()["job"]


async def test_schedule_patch_partial_keys(client):
    """键在=更新、键缺=保持（in→not in 反转=缺键清空）。"""
    job = await _mk_cron_job(client)
    r = await client.patch(f"/api/schedules/{job['id']}",
                           json={"label": "新标签"})     # 只带 label
    assert r.status_code == 200
    d = r.json()["job"]
    assert d["label"] == "新标签"
    assert d["prompt"] == "旧提示词"                       # 缺键不动
    r2 = await client.patch(f"/api/schedules/{job['id']}",
                            json={"prompt": "新提示词"})
    d2 = r2.json()["job"]
    assert d2["prompt"] == "新提示词" and d2["label"] == "新标签"
    # prompt 空串 → 400；max_fires 越界 → 400
    assert (await client.patch(
        f"/api/schedules/{job['id']}", json={"prompt": " "})).status_code == 400
    assert (await client.patch(
        f"/api/schedules/{job['id']}",
        json={"max_fires": 0})).status_code == 400
    assert (await client.patch(
        f"/api/schedules/{job['id']}",
        json={"max_fires": 100001})).status_code == 400
    r3 = await client.patch(f"/api/schedules/{job['id']}",
                            json={"max_fires": 500})
    assert r3.json()["job"]["max_fires"] == 500
    assert (await client.patch(
        "/api/schedules/999999", json={"label": "x"})).status_code == 404


async def test_schedule_patch_trigger_matrix_and_done_gate(client):
    """cron→清 every / every_s 换间隔 / done 后改触发=400 拒。"""
    job = await _mk_cron_job(client)
    # 触发改期：at → 清 cron（一次性任务不再循环）
    r = await client.patch(f"/api/schedules/{job['id']}",
                           json={"at": "2030-01-01 00:00"})
    assert r.status_code == 200, r.text
    d = r.json()["job"]
    assert d.get("cron") in (None, "")                    # cron 被清
    assert d["status"] == "active"
    # every_s 换间隔
    r2 = await client.patch(f"/api/schedules/{job['id']}",
                            json={"every_s": 3600})
    d2 = r2.json()["job"]
    assert d2["every_s"] == 3600 and not d2.get("cron")
    # 坏 cron → 400；触发行 in 判定：单键触发也要走改期路径（at 与 cron
    # 同给时 cron 生效；or→and 反转=单键触发跳过改期，every 不清）
    assert (await client.patch(
        f"/api/schedules/{job['id']}",
        json={"cron": "not-a-cron"})).status_code == 400
    r_one = await client.patch(f"/api/schedules/{job['id']}",
                               json={"cron": "0 6 * * *"})
    d_one = r_one.json()["job"]
    assert d_one["cron"] == "0 6 * * *" and not d_one.get("every_s")
    # done 门（终态只能由 firing 进，DB 直改模拟）：改触发=400 须删除重建
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        c.execute("UPDATE scheduled_jobs SET status='done' WHERE id=?",
                  (job["id"],))
    assert (await client.patch(
        f"/api/schedules/{job['id']}",
        json={"cron": "0 4 * * *"})).status_code == 400
    # done 门：status 只有 active|paused（done 是终态不可直写），触发改期
    # 只挡 done——用 max_fires 耗尽路径进 done 需真 firing，这里锁 status
    # 校验门 + 触发矩阵的本位语义
    assert (await client.patch(
        f"/api/schedules/{job['id']}",
        json={"status": "bogus"})).status_code == 400
    assert (await client.patch(
        f"/api/schedules/{job['id']}",
        json={"every_s": 30})).status_code == 400        # every_s 下界 60


async def test_schedule_new_session_kind_keys(client):
    """new_session 型：title/profile/engine 键检测 + 未知 profile/engine 拒。"""
    r = await client.post("/api/schedules", json={
        "kind": "new_session", "title": "旧标题", "prompt": "开工",
        "profile": "auto", "cron": "0 5 * * *"})
    assert r.status_code == 200, r.text
    jid = r.json()["job"]["id"]
    r2 = await client.patch(f"/api/schedules/{jid}",
                            json={"title": "新标题"})     # 只改 title
    d = r2.json()["job"]
    assert d["title"] == "新标题" and d.get("profile") in ("auto", None)
    assert (await client.patch(
        f"/api/schedules/{jid}", json={"profile": "不存在"})).status_code == 400
    assert (await client.patch(
        f"/api/schedules/{jid}", json={"engine": "不存在"})).status_code == 400
