"""M4 突变补测：session/hooks/daemon 存活变异对赌。

session 首轮 20%（16 活）暴露：compact 摘要/时间线点位、todos 重放形状、
usage 模型索引——重建面全靠 t9 的 resume/fork/rotate 间接覆盖，直接形状
从未断言。hooks：settings 非对象条目健壮性（and→or 反转=AttributeError
炸穿 load）+ 失败钩子（rc=1）不得改写入参。daemon：断开连接只摘自己
（w is not writer 反转=保自己踢他人，多连接镜像全断）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from loadn.core.hooks import HookRunner
from loadn.core.session import SessionManager


async def _mk(tmp_path) -> SessionManager:
    return SessionManager.create(tmp_path, home=tmp_path / "home")


# ================================================================ session
async def test_compact_summary_and_point_rebuilt(tmp_path):
    """compact 摘要链 + 时间线点位重建（compactor UPDATE 模式的消费契约）。"""
    s1 = await _mk(tmp_path)
    s1.mark_compact("压缩摘要 A", tokens_cropped=1200)
    uuid_at_mark = s1.transcript.last_uuid()
    assert s1.state.compact_points == [uuid_at_mark]  # 内存点位=事件 uuid 非 ""
    s1.append_user("压缩后的新消息")                 # 摘要取「最后一个」compact
    s2 = SessionManager.resume(s1.session_id, tmp_path,
                               home=tmp_path / "home")
    assert s2.last_compact_summary() == "压缩摘要 A"  # 类型门+payload/summary 链
    pts = s2.state.compact_points                     # 点位经 _replay_state 重建
    assert pts == [uuid_at_mark]                      # =compact 事件 uuid 非 ""


async def test_todos_replay_shape(tmp_path):
    """todos 重放：t1 起序号 + content/description 各自优先链（TodoWrite 重建）。"""
    s1 = await _mk(tmp_path)
    s1.append_event("assistant", {
        "role": "assistant",
        "content": [{"type": "tool_use", "name": "TodoWrite", "input": {
            "todos": [{"content": "内容甲", "subject": "主题甲",
                       "description": "描述甲", "status": "in_progress"}]}}]})
    s2 = SessionManager.resume(s1.session_id, tmp_path,
                               home=tmp_path / "home")
    assert len(s2.state.todos) == 1                   # 空 and-链不得清空
    t = s2.state.todos[0]
    assert t.id == "t1"                               # 序号从 t1 起
    assert t.subject == "内容甲"                       # content 优先于 subject
    assert t.description == "描述甲"                   # description 优先于 content
    assert t.status == "in_progress"


def test_record_usage_model_index(tmp_path):
    """turn 记账的 per-model 索引落库（models_json——成本面板数据源）。"""
    from loadn.persistence import db as db_mod
    s1 = SessionManager.create(tmp_path, home=tmp_path / "home")
    s1.record_usage(SimpleNamespace(
        usage={"input_tokens": 100, "output_tokens": 5},
        model_usage={"glm-5.3": {"inputTokens": 100, "outputTokens": 5,
                                 "costUSD": 0.01}}))
    with db_mod.conn(tmp_path / "home") as c:
        row = c.execute("SELECT usage_json, models_json FROM sessions "
                        "WHERE id=?", (s1.session_id,)).fetchone()
    assert row and json.loads(row["models_json"]).get("glm-5.3", {}).get(
        "inputTokens") == 100
    assert json.loads(row["usage_json"])["input_tokens"] == 100


# ================================================================ hooks
async def test_hook_load_skips_nonobject_entries(tmp_path, monkeypatch):
    """settings 钩子条目非对象/无 command → 跳过且 load 不炸（and→or=炸穿）。"""
    import loadn.core.hooks as hooks_mod
    ghome = tmp_path / "ghome"
    ghome.mkdir()
    monkeypatch.setattr(hooks_mod, "loadn_home", lambda: ghome)
    d = tmp_path / ".loadn"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps(
        {"hooks": {"PreToolUse": ["bare-string", {"command": "echo hi"},
                                  {"no": "command"}]}}), encoding="utf-8")
    r = HookRunner.load(tmp_path)
    assert r.hooks.get("PreToolUse") == ["echo hi"]


async def test_failed_hook_cannot_override(tmp_path):
    """rc=1 的失败钩子即使 stdout 是 JSON 也不得改写入参。"""
    script = tmp_path / "failhook.sh"
    script.write_text(
        '#!/bin/sh\nprintf \'{"input": {"file_path": "/etc/pwned"}}\'\nexit 1\n')
    script.chmod(0o755)
    r = HookRunner({"PreToolUse": [str(script)]})
    out = await r.fire("PreToolUse", {"tool": "Write"})
    assert not out.blocked
    assert out.input_override is None


# ================================================================ daemon
async def test_daemon_disconnect_removes_only_self():
    """连接断开只摘自己的 writer（is not 反转=保自己踢他人，镜像连坐全断）。"""
    from loadn.transport.daemon import EngineDaemon

    class _W:
        closed = False

        def write(self, b):
            pass

        async def drain(self):
            pass

        def close(self):
            self.closed = True

    class _R:
        def __init__(self):
            self.n = 0

        async def readline(self):
            self.n += 1
            if self.n == 1:
                return json.dumps(
                    {"auth": "T", "session_id": "s-peer"}).encode() + b"\n"
            return b""                              # _pump 立即 EOF

    d = EngineDaemon(Path("/tmp"))
    d.token = "T"
    mine, other = _W(), _W()
    d.peers.setdefault("s-peer", []).extend([other, mine])
    await d._handle(_R(), mine)                     # 鉴权过 → _pump EOF → 摘除
    assert d.peers["s-peer"] == [other]             # 他人连接必须保留
    assert mine.closed and not other.closed
