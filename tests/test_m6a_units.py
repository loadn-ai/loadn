"""M6a 续：memory/context/tool_repair 存活变异对赌。

memory 44%（38 活）暴露：git 根域键、蜜罐护栏在直写通道、抽取流过滤、
forget 空 keyword 删光、boundary 锚定——全部无对赌。context 52%（41 活）：
宪法链边界、@import 深度/去重/缺文件、clip 边界、env 树隐藏目录、
长期记忆注入块。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from loadn.core import context as ctx
from loadn.core import memory as mem
from loadn.core.session import SessionManager


# ================================================================ memory
def test_project_key_git_root_preferred(tmp_path):
    """git 根优先做记忆域键（子目录会话共享记忆，不按 cwd 割裂）。"""
    import subprocess
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    k_root = mem.project_key(root)
    assert mem.project_key(root / "sub") == k_root
    import hashlib
    assert k_root != hashlib.sha1(str(root / "sub").encode()).hexdigest()[:12]


def _mk_session(tmp_path) -> SessionManager:
    s = SessionManager.create(tmp_path, home=tmp_path / "home")
    s._mem_cwd = tmp_path
    return s


class _Prov:
    """抽取器桩：流式吐 JSON 数组（含坏 item 与非文本帧）。"""

    def __init__(self, payload: str):
        self.payload = payload

    def chat(self, messages, _tools, _system, model=None, use_cache=False):
        parts = [self.payload[i:i + 20] for i in range(0, len(self.payload), 20)]

        async def gen():
            for p in parts:
                yield SimpleNamespace(kind="text_delta", text=p)
            yield SimpleNamespace(kind="message_stop", text="")

        return gen()


async def test_extract_stream_filter_and_bad_items(tmp_path):
    """text_delta 流拼接 + 坏 item（空摘要）滤除——只有 1 条入库。"""
    s = _mk_session(tmp_path)
    s.append_user("我们的项目约定是测试优先，改动后要先跑完整测试再收工")
    payload = json.dumps([
        {"summary": "偏好 pytest", "content": "改动后先跑完整测试"},
        {"summary": "", "content": "空摘要应被滤除"}])
    n = await mem.extract_and_store(_Prov(payload), tmp_path, s,
                                    small_model="m")
    entries = mem.load_entries(tmp_path)
    assert n == 1 and len(entries) == 1
    assert entries[0]["summary"] == "偏好 pytest"
    assert mem.boundary_of(tmp_path) != ""          # 边界已推进


async def test_direct_write_canary_blocks(tmp_path):
    """直写通道蜜罐护栏：诱饵 token 形态不入库（and→or=护栏绕过）。"""
    s = _mk_session(tmp_path)
    s.append_user("记住 sk-abcdefghijklmnopqrst 是重要凭据")
    await mem.extract_and_store(_Prov("[]"), tmp_path, s, small_model="m")
    assert mem.load_entries(tmp_path) == []          # 熔断不存
    assert mem.boundary_of(tmp_path) != ""           # 但边界推进（不重抽）


def test_forget_guards(tmp_path):
    """空 keyword 不删光（守卫反转=全库清空）；summary-only 命中可删。"""
    mem.remember(tmp_path, "偏好 ruff", "代码风格相关约定",
                 origin_session="s1")
    mem.remember(tmp_path, "另一条", "无关内容", origin_session="s1")
    assert mem.forget(tmp_path, "   ， ") == 0        # 空词不连坐
    assert len(mem.load_entries(tmp_path)) == 2
    assert mem.forget(tmp_path, "偏好 ruff") == 1    # 只命中 summary 也删
    assert [e["summary"] for e in mem.load_entries(tmp_path)] == ["另一条"]


def test_new_segment_boundary_anchor(tmp_path):
    """boundary 只取其后新增段（锚错位=旧段重抽/记忆重复）。"""
    s = _mk_session(tmp_path)
    s.append_user("第一条旧消息内容")
    s.append_user("第二条旧消息内容")
    u2 = s.transcript.last_uuid()
    s.append_user("第三条新消息内容")
    seg, last = mem.new_segment_since_boundary(tmp_path, s)
    mem.advance_boundary(tmp_path, u2)
    seg2, last2 = mem.new_segment_since_boundary(tmp_path, s)
    assert len(seg2) == 1 and "第三条" in seg2[0]["content"]
    assert last2 == s.transcript.last_uuid()


# ================================================================ context
def test_chain_files_boundary_semantics(tmp_path):
    """宪法链：boundary→cwd 逐层收集；boundary 之上不越界；无关 boundary 空。"""
    parent = tmp_path / "host"
    boundary = parent / "proj"
    cwd = boundary / "a" / "b"
    cwd.mkdir(parents=True)
    for d in (parent, boundary, cwd):
        (d / "CLAUDE.md").write_text(f"宪法@{d.name}", encoding="utf-8")
    chain = ctx._chain_files(boundary, cwd)
    assert [p.parent for p in chain] == [boundary, cwd]   # parent 不越界
    assert ctx._chain_files(tmp_path / "elsewhere", cwd) == []


def test_expand_imports_depth_missing_diamond(tmp_path):
    (tmp_path / "d.md").write_text("DD", encoding="utf-8")
    (tmp_path / "b.md").write_text("@d.md", encoding="utf-8")
    (tmp_path / "c.md").write_text("@d.md", encoding="utf-8")
    (tmp_path / "a.md").write_text(
        "@b.md\n@c.md\n@nope.md", encoding="utf-8")
    out = ctx._expand_imports((tmp_path / "a.md").read_text(), tmp_path)
    assert "DD" in out                                    # 深度 0 正常展开
    assert out.count("DD") == 1                           # diamond 只展一次
    assert "@import 未解析：nope.md" in out               # 缺文件注释文案


def test_clip_exact_limit_unchanged():
    assert ctx._clip("abc", 3) == "abc"                   # 恰好等长不截断
    assert ctx._clip("abcd", 3) == "abc\n…[截断]"


def test_dir_tree_skips_hidden_and_heavy(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / ".git").mkdir()
    (tmp_path / "src" / "node_modules").mkdir()
    out = ctx._dir_tree(tmp_path)
    assert "src/" in out and "main.py" in out
    assert ".git" not in out and "node_modules" not in out


def test_memory_project_section_injects_entries(tmp_path):
    """长期记忆注入块进 context（or→and=记忆永不上桌）。"""
    e = mem.remember(tmp_path, "偏好 TDD", "先写测试再实现",
                     origin_session="sess-x")
    b = ctx.ContextAssembler(cwd=tmp_path)
    text = b._build_section("memory_project")
    assert f"[memory:{e['id']}|sess-x]" in text and "偏好 TDD" in text
    assert mem.MEMORY_NOTE in text


# ================================================================ tool_repair
def test_repair_standalone_whitespace_rejected():
    """standalone 语义：纯空白文本 None（or→and=空白也进解析）。"""
    from loadn.core.tool_repair import parse_standalone_blocks
    assert parse_standalone_blocks("   \n\t ") is None
