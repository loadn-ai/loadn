"""API 面契约钉子（v0.6.12 routes/ 拆包沉淀）。

路由按域拆进 api/routes/ 后，端点在文件间挪动不再有「一个文件丢了 import
就红」的天然护栏——本测试把 /api 全量 (path, methods) 钉成快照：任何端点
新增/删除/方法变更都会显形。有意变更时同步更新 EXPECTED（regen 一行）：

    .venv/bin/python tests/contract/test_api_surface.py
"""
from __future__ import annotations

from loadn_webui.api.app import app

# (path, "逗号排序的 method 集")——regen 脚本打 stdout，贴回来即可
EXPECTED = {
    ("/api/admin/approvals", "get"),
    ("/api/admin/audit", "get"),
    ("/api/admin/audit/verify", "post"),
    ("/api/admin/egress", "get"),
    ("/api/admin/egress/allow", "delete,post"),
    ("/api/admin/egress/grant/revoke", "post"),
    ("/api/admin/egress/policy", "put"),
    ("/api/admin/kill-all", "post"),
    ("/api/admin/kill-all/clear", "post"),
    ("/api/admin/resources", "get"),
    ("/api/admin/resources/custom", "delete,post"),
    ("/api/admin/resources/secret", "post"),
    ("/api/admin/resources/service", "post"),
    ("/api/admin/resources/test", "post"),
    ("/api/admin/security", "get"),
    ("/api/admin/security/ops", "put"),
    ("/api/admin/vault", "get"),
    ("/api/admin/vault/entry", "post"),
    ("/api/admin/vault/entry/{platform}", "delete"),
    ("/api/admin/vault/{platform}", "delete,get,put"),
    ("/api/approvals/consume", "post"),
    ("/api/activity", "get"),
    ("/api/approvals/{aid}", "get"),
    ("/api/approvals/{aid}/decide", "post"),
    ("/api/artifacts/{aid}/download", "get"),
    ("/api/artifacts/{aid}/preview", "get"),
    ("/api/categories", "get,post"),
    ("/api/categories/{cid}", "delete,patch"),
    ("/api/health", "get"),
    ("/api/memory/domains", "get"),
    ("/api/memory/entries", "get"),
    ("/api/memory/entry", "delete"),
    ("/api/memory/file", "get,post,put"),
    ("/api/memory/history", "get"),
    ("/api/memory/restore", "post"),
    ("/api/memory/version", "get"),
    ("/api/hooks", "get,post"),
    ("/api/hooks/{hid}", "delete,patch"),
    ("/api/messages/{mid}", "delete,put"),
    ("/api/profiles", "get"),
    ("/api/projects", "get,post"),
    ("/api/projects/{pid}", "delete,patch"),
    ("/api/projects/{pid}/restore", "patch"),
    ("/api/schedules", "get,post"),
    ("/api/schedules/{jid}", "delete,patch"),
    ("/api/sessions", "get,post"),
    ("/api/sessions/{sid}", "delete,get,patch"),
    ("/api/sessions/{sid}/approvals", "get,post"),
    ("/api/sessions/{sid}/archive", "get"),
    ("/api/sessions/{sid}/artifacts", "get"),
    ("/api/sessions/{sid}/egress", "get"),
    ("/api/sessions/{sid}/events", "get"),
    ("/api/sessions/{sid}/export", "post"),
    ("/api/sessions/{sid}/file", "get"),
    ("/api/sessions/{sid}/ingest", "post"),
    ("/api/sessions/{sid}/kill", "post"),
    ("/api/sessions/{sid}/live", "get"),
    ("/api/sessions/{sid}/messages", "post"),
    ("/api/sessions/{sid}/promote", "post"),
    ("/api/sessions/{sid}/rollback", "post"),
    ("/api/sessions/{sid}/schedules", "post"),
    ("/api/sessions/{sid}/share", "post"),
    ("/api/sessions/{sid}/snapshots", "get"),
    ("/api/sessions/{sid}/steer", "post"),
    ("/api/sessions/{sid}/timeline", "get"),
    ("/api/sessions/{sid}/tree", "get"),
    ("/api/sessions/{sid}/unlock", "post"),
    ("/api/sessions/{sid}/upload", "post"),
    ("/api/settings", "get"),
    ("/api/settings/claude", "put"),
    ("/api/settings/convergence", "put"),
    ("/api/settings/engines", "put"),
    ("/api/settings/notify", "put"),
    ("/api/settings/notify/test", "post"),
    ("/api/settings/pricing", "put"),
    ("/api/settings/resources", "put"),
    ("/api/settings/resources/test", "post"),
    ("/api/settings/run", "put"),
    ("/api/settings/share", "put"),
    ("/api/settings/titlegen", "put"),
    ("/api/settings/titlegen/test", "post"),
    ("/api/skillhub/catalog", "get"),
    ("/api/skillhub/search", "get"),
    ("/api/skills", "get,post"),
    ("/api/skills/install", "post"),
    ("/api/skills/translate", "post"),
    ("/api/skills/upload", "post"),
    ("/api/skills/{name}", "delete,get"),
    ("/api/skills/{name}/export", "get"),
    ("/api/skills/{name}/file", "delete,get,post,put"),
    ("/api/skills/{name}/toggle", "post"),
    ("/api/sessions/{sid}/messages/{mid}/sources", "get"),
    ("/api/sse-ticket", "get"),
    ("/api/stats/cost", "get"),
    ("/api/stats/usage", "get"),
    ("/api/tools", "get"),
    ("/api/tools/mcp/{name}", "delete,put"),
    ("/api/tools/profile/{profile}", "put"),
    ("/api/turns/{tid}", "delete"),
    ("/api/turns/{tid}/stop", "post"),
}


def _surface() -> set[tuple[str, str]]:
    spec = app.openapi()
    return {(p, ",".join(sorted(m for m in d if m in ("get", "post", "put",
                    "delete", "patch"))))
            for p, d in spec["paths"].items() if p.startswith("/api")}


def test_api_surface_pinned():
    """全量 /api 端点集与快照逐项对赌（丢端点/偷加面都会红）。"""
    got = _surface()
    assert got == set(EXPECTED), (
        f"API 面与快照漂移：丢失={sorted(set(EXPECTED) - got)} "
        f"新增={sorted(got - set(EXPECTED))}——若是有意变更请 regen 更新快照")


if __name__ == "__main__":                      # regen：打印新快照贴回
    for p, m in sorted(_surface()):
        print(f'    ("{p}", "{m}"),')
