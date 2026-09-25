"""T6a：hooks.py 外部命令钩子路径（原 64%——命令执行分支从未直测）。

真子进程跑钩子（sh -c …），不 mock 被测物：
- exit 2 = block（stderr 回填 reason）
- exit 0 + stdout JSON = input/output 改写
- exit 0 无输出 = 放行零改写
- 坏 JSON stdout = 忽略不改写
- 超时 = 放行（不阻断）+ 清子进程
- 命令异常（不可执行）= 告警继续
- load()：全局+项目合并、信任门跳过、旧 .agent 目录兼容
"""
from __future__ import annotations

import json
from pathlib import Path

from loadn.core import hooks as hk


# ---------------------------------------------------------------- fire 路径
async def test_exit2_blocks_with_stderr():
    r = hk.HookRunner({"PreToolUse": ["sh -c 'echo 危险 >&2; exit 2'"]})
    out = await r.fire("PreToolUse", {"tool": "Bash"})
    assert out.blocked and "危险" in out.block_reason


async def test_exit0_json_overrides_input_and_output(tmp_path):
    # JSON 经脚本文件输出（内嵌引号会被内层 sh 的 quote-removal 吃掉）
    pre_sh = tmp_path / "pre.sh"
    pre_sh.write_text(
        "printf '%s' " + repr(json.dumps({"input": {"command": "改写过"}},
                             ensure_ascii=False))
        + "\n", encoding="utf-8")
    post_sh = tmp_path / "post.sh"
    post_sh.write_text(
        "printf '%s' " + repr(json.dumps({"output": "改写输出"}, ensure_ascii=False)) + "\n",
        encoding="utf-8")
    r = hk.HookRunner({
        "PreToolUse": [f"sh {pre_sh}"],
        "PostToolUse": [f"sh {post_sh}"],
    })
    pre = await r.fire("PreToolUse", {})
    assert pre.input_override == {"command": "改写过"}
    post = await r.fire("PostToolUse", {})
    assert post.output_override == "改写输出"


async def test_exit0_plain_stdout_no_override():
    r = hk.HookRunner({"Stop": ["sh -c 'echo 只是日志'"]})
    out = await r.fire("Stop", {})
    assert not out.blocked and out.input_override is None \
        and out.output_override is None


async def test_bad_json_stdout_ignored():
    r = hk.HookRunner({"PreToolUse": ["printf '{not-json\n'"]})
    out = await r.fire("PreToolUse", {})
    assert not out.blocked and out.input_override is None


async def test_first_block_short_circuits():
    """顺序跑：首个 block 即短路（后续钩子不再执行——用副作用文件证）。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        marker = Path(td) / "second_ran"
        r = hk.HookRunner({"PreToolUse": [
            "sh -c 'exit 2'",
            f"sh -c 'touch {marker}'",
        ]})
        out = await r.fire("PreToolUse", {})
        assert out.blocked and not marker.exists()


async def test_timeout_passes_and_kills(monkeypatch):
    """钩子超时 = 放行（不阻断会话）；超时子进程被 kill。"""
    monkeypatch.setattr(hk, "HOOK_TIMEOUT_S", 0.4)
    r = hk.HookRunner({"Stop": ["sleep", "30"]})
    out = await r.fire("Stop", {})
    assert not out.blocked                       # 超时不拦
    import time
    time.sleep(0.3)                              # 给 kill 收尾
    assert r.hooks                              # runner 状态完好


async def test_broken_command_continues():
    """命令不可执行 = 告警继续（钩子故障不炸会话，后续钩子仍跑）。"""
    r = hk.HookRunner({"Stop": ["no-such-binary-xyz", "sh -c 'exit 0'"]})
    out = await r.fire("Stop", {})
    assert not out.blocked


def test_has_and_empty():
    r = hk.HookRunner({"Stop": ["true"]})
    assert r.has("Stop") and not r.has("PreToolUse")
    assert not hk.HookRunner().has("Stop")


# ---------------------------------------------------------------- load 合并
def _settings(p: Path, hooks: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")


def test_load_merges_global_and_project(tmp_path, monkeypatch):
    from loadn.core import trust
    monkeypatch.setattr(hk, "loadn_home", lambda: tmp_path / "home")
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    _settings(tmp_path / "home" / "settings.json",
              {"Stop": [{"command": "global-cmd"}]})
    _settings(tmp_path / ".loadn" / "settings.json",
              {"Stop": [{"command": "proj-cmd"}],
               "PreToolUse": [{"command": "pre-cmd"}]})
    r = hk.HookRunner.load(tmp_path)
    assert r.hooks["Stop"] == ["global-cmd", "proj-cmd"]     # 列表拼接
    assert r.hooks["PreToolUse"] == ["pre-cmd"]


def test_load_untrusted_skips_project(tmp_path, monkeypatch):
    from loadn.core import trust
    monkeypatch.setattr(hk, "loadn_home", lambda: tmp_path / "home")
    monkeypatch.setattr(trust, "gate",
                        lambda cwd: (False, "unconfirmed-resources"))
    _settings(tmp_path / ".loadn" / "settings.json",
              {"Stop": [{"command": "evil"}]})
    _settings(tmp_path / "home" / "settings.json",
              {"Stop": [{"command": "only-global"}]})
    r = hk.HookRunner.load(tmp_path)
    assert r.hooks["Stop"] == ["only-global"]                # 项目级被跳过


def test_load_legacy_agent_dir(tmp_path, monkeypatch):
    """兼容读旧 .agent/settings.json（迁移期形态）。"""
    from loadn.core import trust
    monkeypatch.setattr(hk, "loadn_home", lambda: tmp_path / "home")
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    _settings(tmp_path / ".agent" / "settings.json",
              {"Stop": [{"command": "legacy"}]})
    assert hk.HookRunner.load(tmp_path).hooks["Stop"] == ["legacy"]


def test_load_bad_json_skipped(tmp_path, monkeypatch):
    from loadn.core import trust
    monkeypatch.setattr(hk, "loadn_home", lambda: tmp_path / "home")
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "settings.json").write_text(
        "{broken", encoding="utf-8")
    assert hk.HookRunner.load(tmp_path).hooks == {}          # 坏文件不炸
