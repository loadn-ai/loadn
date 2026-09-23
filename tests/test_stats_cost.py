"""成本统计链路：modelUsage 落库 → /api/stats/cost 三口径聚合 → 回填可恢复且幂等。"""
import json

from tests.conftest import wait_turn


async def _create(client, **body):
    resp = await client.post("/api/sessions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]


async def _run_one_turn(client, ws_root, title: str, with_tools: bool = False):
    sess = await _create(client, title=title, first_message="你好")
    sid = sess["id"]
    if with_tools:                       # fake 的工具/产物触发开关（.fake/tools）
        ctrl = ws_root / sid / ".fake"
        ctrl.mkdir(parents=True, exist_ok=True)
        (ctrl / "tools").touch()
    tid = (await client.get(f"/api/sessions/{sid}")).json()["turns"][0]["id"]
    t = await wait_turn(client, sid, tid)
    assert t["status"] == "done", t
    return sid, tid


async def test_stats_cost_three_calibers(client, ws_root):
    await _run_one_turn(client, ws_root, "成本统计-带工具", with_tools=True)
    r = await client.get("/api/stats/cost")
    assert r.status_code == 200
    data = r.json()

    # fake usage：in 1000 / out 200 / cache_read 10000（bigusage 更大，只断言下界）
    tok = data["totals"]["tokens"]
    assert tok["cache_read"] >= 10000
    assert tok["total_all"] == tok["input"] + tok["output"] + tok["cache_read"] + tok["cache_write"]

    # 三口径都有值且 CLI > API（CLI 按 Opus 虚高）
    assert data["totals"]["cost_cli_usd"] > 0
    assert data["totals"]["cost_api_usd"] > 0
    assert data["totals"]["cost_cli_usd"] > data["totals"]["cost_api_usd"]
    assert data["totals"]["plan_credits"] > 0
    assert data["totals"]["turns"] >= 1
    assert data["totals"]["turns_without_model"] >= 0

    # 模型维度：glm-5.3[1m] 归一化到 glm-5.3；webSearchRequests 透出
    # （共享测试库里可能有其它用例的 turn，只做下界断言；精确计价在 test_pricing 覆盖）
    glm = [m for m in data["by_model"] if m["model"] == "glm-5.3[1m]"]
    assert glm, data["by_model"]
    assert glm[0]["norm"] == "glm-5.3"
    assert glm[0]["web_search_requests"] >= 2
    exp_one_turn = (1000 * 1.4 + 10000 * 0.26 + 200 * 4.4) / 1e6
    assert glm[0]["cost_api_usd"] >= exp_one_turn - 1e-9

    # 工具接口统计（fake 带 tools 时产出 Bash/Write 卡片）
    names = {x["name"]: x for x in data["tools"]}
    assert "Bash" in names and names["Bash"]["calls"] >= 1

    # 每日/角色/Top 会话都在
    assert data["daily"] and data["daily"][-1]["cost_api_usd"] > 0
    assert any(p["profile"] == "assistant" for p in data["by_profile"])
    assert data["top_sessions"]


async def test_pricing_block_in_stats(client):
    data = (await client.get("/api/stats/cost")).json()
    pr = data["pricing"]
    assert pr["api"]["glm-5.3"] == {"input": 1.4, "cache_read": 0.26, "output": 4.4}
    assert pr["plan"]["glm-5.3"]["output"] == 24
    assert pr["cli_fallback"]["input"] == 5.0
    assert pr["usd_cny"] == 7.1
    assert "note" in pr


async def test_backfill_restores_and_idempotent(client, ws_root):
    """engine 落的 models_json 清空后（模拟旧数据）能从 .out 日志恢复；重跑幂等。"""
    from loadn_webui import backfill
    from loadn_webui import db as db_mod
    from loadn_webui.config import PATHS

    sid, _tid = await _run_one_turn(client, ws_root, "回填测试")

    # 1) engine 正常路径已落 models_json；清空模拟升级前的旧行
    with db_mod.conn() as c:
        c.execute("UPDATE turns SET models_json=NULL WHERE session_id=?", (sid,))
    orphan = PATHS["call_logs"] / "turn999999_1234567890123.out"
    orphan.write_text(json.dumps({"type": "result",
                                  "modelUsage": {"ghost": {"inputTokens": 1}}}))
    try:
        stats = backfill.run()
        assert stats["backfilled"] >= 1
        assert stats["orphan_files"] >= 1
        with db_mod.conn() as c:
            row = c.execute(
                "SELECT models_json FROM turns WHERE session_id=? "
                "AND models_json IS NOT NULL", (sid,)).fetchone()
        assert row and "glm-5.3[1m]" in row["models_json"]
        # 2) 幂等
        stats2 = backfill.run()
        assert stats2["backfilled"] == 0
        assert stats2["skipped_existing"] >= stats["skipped_existing"]
    finally:
        orphan.unlink(missing_ok=True)
