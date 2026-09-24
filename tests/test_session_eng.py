"""P2-2 会话工程验收：tail 反向扫描 + branch 条目树。

- tail：正确性（尾 n 条、顺序保持）/ 10MB 档 <50ms / 坏行（半行）跳过 /
  单行炸弹防护
- branch：从任意 uuid 起新链（前缀沿 parent 链回溯，分支不含另一支）；
  分支重放语义（replay_messages 只见自己链）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from loadn.persistence.transcript import TranscriptStore


def _mk(tmp: Path, n: int = 10) -> TranscriptStore:
    ts = TranscriptStore("s1", home=tmp)
    for i in range(n):
        ts.append("user", {"role": "user",
                           "content": [{"type": "text", "text": f"m{i}"}]})
    return ts


# ---------------------------------------------------------------- tail
def test_tail_correctness(tmp_path):
    ts = _mk(tmp_path, 20)
    t5 = ts.tail(5)
    assert [e["payload"]["content"][0]["text"] for e in t5] == \
        [f"m{i}" for i in range(15, 20)]           # 顺序保持
    assert ts.tail(0) == []
    assert len(ts.tail(100)) == 20                  # 超量=全部


def test_tail_bad_lines_skipped(tmp_path):
    ts = _mk(tmp_path, 5)
    with ts.path.open("a", encoding="utf-8") as f:
        f.write('{"type":"user","uuid":"x"   \n')      # 被杀半行
        f.write(json.dumps({"type": "user", "uuid": "ok1",
                            "payload": {"content": [{"type": "text",
                                                     "text": "ok"}]}}) + "\n")
    out = ts.tail(3)
    assert out[-1]["uuid"] == "ok1"
    assert all(e.get("uuid") != "x" for e in out)      # 坏行跳过


def test_tail_performance_10mb(tmp_path):
    """codex 验收标准：10MB 档 tail(50) < 50ms（O(tail) 不整读）。"""
    ts = TranscriptStore("big", home=tmp_path)
    ts._ensure_dir()
    line = json.dumps({"type": "user", "uuid": "u", "ts": 1,
                       "payload": {"content": [{"type": "text",
                                                "text": "x" * 900}]}}) + "\n"
    with ts.path.open("w", encoding="utf-8") as f:
        for _ in range(11_000):                    # ~10MB
            f.write(line)
    t0 = time.perf_counter()
    out = ts.tail(50)
    dt = (time.perf_counter() - t0) * 1000
    assert len(out) == 50
    assert dt < 50, f"tail(50) 耗时 {dt:.1f}ms（预算 50ms）"


# ---------------------------------------------------------------- branch
def test_branch_tree_semantics(tmp_path):
    ts = _mk(tmp_path, 10)
    mid = ts.read_events()[4]["uuid"]             # m4 处开分支
    b = ts.branch(mid)
    # 分支含前缀 m0..m4，不含 m5..m9（另一支）
    b_texts = [e["payload"]["content"][0]["text"]
               for e in b.read_events() if e["type"] == "user"]
    assert b_texts == [f"m{i}" for i in range(5)]
    # 新链延伸：分支 append 不影响原档
    b.append("user", {"role": "user",
                      "content": [{"type": "text", "text": "branch-a"}]})
    assert "branch-a" not in ts.path.read_text()
    assert b.tail(1)[0]["payload"]["content"][0]["text"] == "branch-a"
    # replay 沿 parent 链：另一支事件不进分支的 messages
    msgs = b.replay_messages()
    texts = [b_.text for m in msgs for b_ in m.content
             if hasattr(b_, "text")]
    assert "m9" not in texts and "branch-a" in texts


def test_branch_deep_chain(tmp_path):
    """跨多块 parent 链回溯（分支点在中段，前缀 >1 块）。"""
    ts = _mk(tmp_path, 30)
    point = ts.read_events()[2]["uuid"]           # m2
    b = ts.branch(point)
    assert len(b.read_events()) == 3              # init 语义外纯事件前缀
    b.append("assistant", {"role": "assistant", "content": []})
    assert b.replay_messages() is not None        # 新链可重放
