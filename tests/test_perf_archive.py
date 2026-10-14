"""PERF-2：messages 冷归档——冷判定否定路径 / 往返一致 / 头尾保留 / 幂等
增量 / owner 门 / purge 清理 / 坏分片 / dry_run 零副作用 / 审计留痕。

归档铁律（archive_session docstring）：文件写成功后才 DELETE——本文件
的往返一致用例即该不变量的行为级验证。零 token。
"""
from __future__ import annotations

import gzip
import json

import httpx
import pytest

from loadn_webui import archive as arch
from loadn_webui import db as db_mod
from loadn_webui.security import audit as audit_mod

from .test_perf_sessions import _mk_auth_sess, _mk_session, _mk_user

pytestmark = [pytest.mark.coverage("perf.archive")]


def _mk_messages(sid: str, n: int) -> list[int]:
    """插 n 条消息（user/assistant 交替），返回实际自增 id 列表（升序）。
    断言用返回值定位头/中/尾——messages.id 全表唯一，不显式指定。"""
    ids: list[int] = []
    with db_mod.conn() as c:
        for i in range(n):
            cur = c.execute(
                "INSERT INTO messages(session_id, role, content,"
                " blocks_json, created_at) VALUES(?,?,?,?,?)",
                (sid, "user" if i % 2 == 0 else "assistant", f"msg-{sid}-{i}",
                 None if i % 2 == 0 else json.dumps(
                     [{"name": "Bash", "brief": f"#{i}"}]),
                 f"2026-09-{(i % 28) + 1:02d}T00:00:00+00:00"))
            ids.append(cur.lastrowid)
    return ids


def _age(sid: str, days_ago_iso: str = "2000-01-01T00:00:00+00:00") -> None:
    with db_mod.conn() as c:
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (days_ago_iso, sid))


def _snapshot_msgs(sid: str) -> list[dict]:
    with db_mod.conn() as c:
        return [db_mod.to_dict(r) for r in c.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,))]


# ---------------------------------------------------------------- ① 冷判定守卫
def test_cold_gates():
    """四重否定路径：active turn / active job / 未到天数 / 消息不足——
    守卫任一反转都会被本用例杀死（不该归档的归了=数据被搬走）。"""
    base = "20261014_arch-gate"
    sids = {k: f"{base}-{k}" for k in ("plain", "turn", "job")}
    for sid in sids.values():
        _mk_session(sid, "2000-01-01T00:00:00+00:00")
    sids["warm"] = f"{base}-warm"
    from datetime import datetime, timedelta, timezone
    yesterday = (datetime.now(timezone.utc)
                 - timedelta(days=1)).isoformat(timespec="seconds")
    _mk_session(sids["warm"], yesterday)   # 活跃（未到冷判定天数）
    with db_mod.conn() as c:
        c.execute("INSERT INTO turns(session_id, status)"
                  " VALUES(?, 'running')", (sids["turn"],))
        db_mod.create_job(c, session_id=sids["job"], label="g", prompt="hi",
                          due_at="2030-01-01T00:00:00+00:00")
        cold = set(arch.cold_session_ids(c))
    assert sids["plain"] in cold
    assert sids["turn"] not in cold, "活跃 turn 的会话不得冷归档"
    assert sids["job"] not in cold, "有定时唤醒的会话不得冷归档"
    assert sids["warm"] not in cold, "未到冷判定天数的会话不得归档"

    # 消息不足两档：≤头尾保留 / 中间段 < MIN_FOLD
    for k, n in (("few", 23), ("thin", 23 + arch.MIN_FOLD - 1)):
        sid = f"{base}-{k}"
        _mk_session(sid, "2000-01-01T00:00:00+00:00")
        _mk_messages(sid, n)
    with db_mod.conn() as c:
        assert arch.archive_session(c, f"{base}-few")["reason"] == "below_keep"
        assert arch.archive_session(
            c, f"{base}-thin")["reason"] == "below_min_fold"
        assert arch.folded_count(c, f"{base}-few") == 0
    assert arch.read_archived(f"{base}-few") == []

    # MIN_FOLD 正边界：中间恰 8 条必须归档。**写死 8 不引用 arch.MIN_FOLD**
    # ——引用被测常量=变异同步漂移成永久等价类（突变实证教训）
    sid = f"{base}-exact"
    _mk_session(sid, "2000-01-01T00:00:00+00:00")
    ids = _mk_messages(sid, 23 + 8)
    with db_mod.conn() as c:
        assert arch.archive_session(c, sid)["archived"] == 8
        # kv 游标必须指向中间段最后一条（杀 mid[-1]→mid[-2]——游标偏 1
        # 会让下轮窗口残留旧消息）
        assert db_mod.kv_get(c, f"archived_upto:{sid}") == str(ids[3 + 8 - 1])

    # COLD_DAYS 正边界：30 天+1s 前的会话必须进冷集（写死 30 同理；
    # -1s 避开 seconds 截断的相等竞态）
    from datetime import datetime, timedelta, timezone
    edge = f"{base}-edge30"
    _mk_session(edge, (datetime.now(timezone.utc)
                       - timedelta(days=30, seconds=1)).isoformat(
                           timespec="seconds"))
    with db_mod.conn() as c:
        assert edge in set(arch.cold_session_ids(c))


# ---------------------------------------------------------------- ② 往返一致+头尾保留
async def test_roundtrip_and_keep(client):
    sid = "20261014_arch-rt"
    _mk_session(sid, "2000-01-01T00:00:00+00:00")
    ids = _mk_messages(sid, 40)
    mid_ids = ids[3:20]                            # 头3尾20 之外的中间段
    before = {m["id"]: m for m in _snapshot_msgs(sid)}

    with db_mod.conn() as c:
        r = arch.archive_session(c, sid)
    assert r["archived"] == 17 and r["sha256"]

    d = (await client.get(f"/api/sessions/{sid}")).json()
    kept_ids = [m["id"] for m in d["messages"]]
    assert kept_ids == ids[:3] + ids[20:], "头3+尾20 保留"
    assert d["messages_folded"] == 17

    # 回源：中间段逐字段一致（文件是原文唯一载体——事务铁律的行为验证）
    got = (await client.get(f"/api/sessions/{sid}/messages/archived")).json()
    assert got["count"] == 17
    for m in got["messages"]:
        b = before[m["id"]]
        assert m["content"] == b["content"]
        assert m["role"] == b["role"]
        assert m["blocks_json"] == b["blocks_json"]
        assert m["created_at"] == b["created_at"]
    assert [m["id"] for m in got["messages"]] == mid_ids


# ---------------------------------------------------------------- ③ 幂等+增量分片
async def test_idempotent_incremental(client):
    sid = "20261014_arch-inc"
    _mk_session(sid, "2000-01-01T00:00:00+00:00")
    ids1 = _mk_messages(sid, 40)
    with db_mod.conn() as c:
        first = arch.archive_session(c, sid)
    assert first["archived"] == 17
    shard0 = arch._archive_root(sid) / "0000.jsonl.gz"
    b0 = shard0.read_bytes()

    # 二次：未归档窗口只剩头尾 → 零动作，老分片不动
    with db_mod.conn() as c:
        assert arch.archive_session(c, sid)["archived"] == 0
    assert shard0.read_bytes() == b0, "幂等：已有分片不得被重写"

    # 会话复活再变冷：+40 条新消息 → 增量窗口=上轮尾20+新40=60 条，
    # 滚动折叠（上轮尾只再留头 3）→ 归档 37，写新分片，老分片不动
    ids2 = _mk_messages(sid, 40)
    _age(sid)
    with db_mod.conn() as c:
        third = arch.archive_session(c, sid)
    assert third["archived"] == 37
    assert shard0.read_bytes() == b0
    assert (arch._archive_root(sid) / "0001.jsonl.gz").exists()
    # 回源跨分片按 id 升序：首轮中间段 + 上轮尾的滚动段 + 新消息头段
    got = (await client.get(
        f"/api/sessions/{sid}/messages/archived")).json()
    got_ids = [m["id"] for m in got["messages"]]
    assert got_ids == ids1[3:20] + ids1[23:] + ids2[:20]
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["messages_folded"] == 54


# ---------------------------------------------------------------- ④ owner 门
async def test_archived_owner_gate(server_url):
    sid = "20261014_arch-own"
    _mk_session(sid, "2000-01-01T00:00:00+00:00", owner_id=None)
    _mk_messages(sid, 40)
    with db_mod.conn() as c:
        arch.archive_session(c, sid)
        uid = _mk_user(c, "arch-userb")
        asid = _mk_auth_sess(c, uid, 25 * 86400)
    async with httpx.AsyncClient(
            base_url=server_url, timeout=10,
            cookies={"loadn_session": asid}) as ub:
        r = await ub.get(f"/api/sessions/{sid}/messages/archived")
        assert r.status_code == 404, "他人会话回源=404（不暴露存在性）"


# ---------------------------------------------------------------- ⑤ purge 清理
async def test_purge_clears_archive(client):
    sid = "20261014_arch-purge"
    _mk_session(sid, "2000-01-01T00:00:00+00:00")
    _mk_messages(sid, 40)
    with db_mod.conn() as c:
        arch.archive_session(c, sid)
    assert arch._archive_root(sid).exists()
    assert arch.purge_archive(sid) is True, "有归档目录时返回 True"
    assert not arch._archive_root(sid).exists()   # 直调同语义
    # 重建后走 API purge（真实生命周期路径）
    _mk_messages(sid, 40)
    _age(sid)
    with db_mod.conn() as c:
        arch.archive_session(c, sid)
    r = await client.delete(f"/api/sessions/{sid}?purge=true")
    assert r.status_code == 200
    assert not arch._archive_root(sid).exists(), "purge 须连归档分片一起清"
    assert arch.purge_archive(sid) is False, "无归档目录时返回 False（幂等）"


# ---------------------------------------------------------------- ⑥ 坏分片
async def test_corrupt_shard_explicit_500(client):
    sid = "20261014_arch-bad"
    _mk_session(sid, "2000-01-01T00:00:00+00:00")
    _mk_messages(sid, 40)
    with db_mod.conn() as c:
        arch.archive_session(c, sid)
    shard = arch._archive_root(sid) / "0000.jsonl.gz"
    shard.write_bytes(b"\x1f\x8btruncated")      # 坏 gzip 头
    r = await client.get(f"/api/sessions/{sid}/messages/archived")
    assert r.status_code == 500
    assert "0000.jsonl.gz" in r.json()["detail"], "错误必须带分片文件名"
    # 修复后恢复
    with gzip.open(shard, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"id": 4, "role": "user", "content": "recovered"}) + "\n")
    r2 = await client.get(f"/api/sessions/{sid}/messages/archived")
    assert r2.status_code == 200 and r2.json()["count"] == 1


# ---------------------------------------------------------------- ⑦ dry_run 零副作用
async def test_dry_run_zero_side_effects(client):
    sid = "20261014_arch-dry"
    _mk_session(sid, "2000-01-01T00:00:00+00:00")
    _mk_messages(sid, 40)
    n_before = len(_snapshot_msgs(sid))
    r = await client.post("/api/admin/archive/cold", json={"dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    entry = next(e for e in body["sessions"] if e["sid"] == sid)
    assert entry["pending_messages"] == 40
    assert len(_snapshot_msgs(sid)) == n_before, "dry_run 不得动库"
    assert not arch._archive_root(sid).exists(), "dry_run 不得落文件"
    # 同会话真跑（API 级）：归档生效（admin 面双头由 client fixture 承担）
    r2 = await client.post("/api/admin/archive/cold", json={})
    assert r2.status_code == 200
    out = next(x for x in r2.json()["results"] if x["sid"] == sid)
    assert out["archived"] == 17


# ---------------------------------------------------------------- ⑧ 审计留痕
async def test_audit_event_written(client):
    sid = "20261014_arch-aud"
    _mk_session(sid, "2000-01-01T00:00:00+00:00")
    _mk_messages(sid, 40)
    # 直调不传 dry_run（杀默认参 False→True——反转后这里会只报数不归档）
    with db_mod.conn() as c:
        out = next(x for x in arch.archive_cold(c)["results"]
                   if x["sid"] == sid)
    assert out["archived"] == 17
    tail = audit_mod.tail(30, "archive")
    ev = next(e for e in tail
              if json.loads(e["detail_json"]).get("sid") == sid)
    d = json.loads(ev["detail_json"])
    assert d["rows"] == 17 and d["bytes"] > 0 and len(d["sha256"]) == 16
    assert d["file"].endswith("0000.jsonl.gz")
