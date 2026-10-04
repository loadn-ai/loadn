"""B6 对抗组（P0-3）：第三方 Skill 供应链锁。

B6-1  无锁外部 skill（frontmatter source）→ 拒索引；本地 skill 不受影响
B6-2  锁内且哈希一致 → 索引；改一字节 → 拒索引 + verify 退出 1
B6-3  `skills lock` 幂等（两次产出字节级相同）；变更已锁条目不加
      --update → 退出 1 + rug-pull 文案；--update → 覆盖并恢复放行
B6-4  与 P0-2 顺序：未信任工作区的外部 skill 连锁都不看（信任门先行）
B6-5  P1 打通：webui 安装远程 skill 即盖 source 戳+写锁；out-of-band
      篡改 → 引擎拒索引；删 skill 连锁条目一起清
B6-6  P1 打通：管理面编辑 SKILL.md = 有意变更 → 锁随存刷新（不误报 rug-pull）
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("sec.b6")]

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from loadn.cli.skills_lock import main as skills_main
from loadn.core import trust
from loadn.core.skills import discover_skills


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("LOADN_HOME", str(h))
    return h


def _ext_skill(root: Path, name: str, body: str = "正文") -> Path:
    d = root / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 外部\ndescription2: x\n"
        f"source: github:acme/{name}-skills/skills/{name}\n---\n\n{body}",
        encoding="utf-8")
    return d / "SKILL.md"


def _local_skill(root: Path, name: str) -> None:
    d = root / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 本地\n---\n\n本地正文", encoding="utf-8")


# ---------------------------------------------------------------- B6-1
def test_b6_1_unlocked_external_rejected(tmp_path: Path, home: Path):
    repo = tmp_path / "repo"
    _ext_skill(repo, "ext-tool")
    _local_skill(repo, "local-tool")
    trust.admit(repo)                                  # 信任先行（B6 测锁不测门）
    skills = discover_skills(repo)
    assert "ext-tool" not in skills, "无锁外部 skill 进入了索引"
    assert "local-tool" in skills                       # 本地不受影响


# ---------------------------------------------------------------- B6-2
def test_b6_2_hash_mismatch_rejected_and_verify_fails(
        tmp_path: Path, home: Path, capsys, monkeypatch):
    repo = tmp_path / "repo2"
    _ext_skill(repo, "ext-a")
    trust.admit(repo)
    monkeypatch.chdir(repo)
    assert skills_main(["lock"]) == 0                   # 生成锁（用户层）
    assert "ext-a" in discover_skills(repo)             # 锁内一致 → 放行
    # 改一字节（rug-pull 形态）→ 拒索引 + verify 退出 1
    md = repo / ".claude" / "skills" / "ext-a" / "SKILL.md"
    md.write_text(md.read_text() + "恶意追加", encoding="utf-8")
    assert "ext-a" not in discover_skills(repo)
    capsys.readouterr()
    assert skills_main(["verify"]) == 1


# ---------------------------------------------------------------- B6-3
def test_b6_3_lock_idempotent_and_update_gate(
        tmp_path: Path, home: Path, capsys, monkeypatch):
    repo = tmp_path / "repo3"
    _ext_skill(repo, "ext-b")
    trust.admit(repo)
    monkeypatch.chdir(repo)
    assert skills_main(["lock"]) == 0
    lock_file = home / "skills.lock.json"
    first = lock_file.read_text()
    assert skills_main(["lock"]) == 0
    assert lock_file.read_text() == first               # 幂等：字节级相同
    # 内容变更后 lock（无 --update）→ 拒绝覆盖 + rug-pull 文案
    md = repo / ".claude" / "skills" / "ext-b" / "SKILL.md"
    md.write_text(md.read_text().replace("正文", "新正文"), encoding="utf-8")
    capsys.readouterr()
    assert skills_main(["lock"]) == 1
    assert "rug-pull" in capsys.readouterr().err
    assert json.loads(lock_file.read_text()) == json.loads(first)   # 未被改
    assert "ext-b" not in discover_skills(repo)         # 变更内容仍被拒
    # --update 显式确认 → 锁更新 → 恢复放行
    assert skills_main(["lock", "--update"]) == 0
    assert "ext-b" in discover_skills(repo)
    assert skills_main(["verify"]) == 0


# ---------------------------------------------------------------- B6-4
def test_b6_4_trust_gate_precedes_lock(tmp_path: Path, home: Path, monkeypatch):
    """未信任工作区：外部 skill 连锁校验都到不了（P0-2 先行）。"""
    repo = tmp_path / "repo4"
    _ext_skill(repo, "ext-c")
    # 先 admit+lock（全放行基线）
    trust.admit(repo)
    monkeypatch.chdir(repo)
    assert skills_main(["lock"]) == 0
    assert "ext-c" in discover_skills(repo)
    # revoke → 信任门先把整个项目 skills 面关掉
    trust.revoke(repo)
    assert "ext-c" not in discover_skills(repo)
    trust.admit(repo)                                   # 恢复
    assert "ext-c" in discover_skills(repo)


def test_source_field_variants(tmp_path: Path, home: Path, monkeypatch):
    """source 形态：github:owner/repo/path 带类型；裸串按 local 类型入锁。"""
    repo = tmp_path / "repo5"
    d = repo / ".claude" / "skills" / "plain"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: plain\ndescription: t\nsource: someref\n---\nbody",
        encoding="utf-8")
    trust.admit(repo)
    monkeypatch.chdir(repo)
    assert skills_main(["lock"]) == 0
    entry = json.loads((home / "skills.lock.json").read_text())["skills"]["plain"]
    assert entry["sourceType"] == "local" and entry["source"] == "someref"
    assert "plain" in discover_skills(repo)


def test_layering_skill_content_vs_dirs(tmp_path: Path, home: Path, monkeypatch):
    """分层契约：SKILL.md 内容编辑不触发信任摘要（锁的领域）；
    新增 skill 目录触发（结构面）。"""
    repo = tmp_path / "repo6"
    _ext_skill(repo, "ext-d")
    trust.admit(repo)
    md = repo / ".claude" / "skills" / "ext-d" / "SKILL.md"
    md.write_text(md.read_text() + "内容编辑", encoding="utf-8")
    ok, why = trust.gate(repo)
    assert ok, why                                 # 内容不动信任摘要
    _local_skill(repo, "brand-new")                # 新目录 → 结构变化
    ok, why = trust.gate(repo)
    assert not ok and "摘要不匹配" in why


# ---------------------------------------------------------------- B6-5（P1）
def _pin_tarball() -> bytes:
    """单 skill 的 GitHub tarball 形状（顶层 repo 目录包裹）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for fname, data in [
            ("x-main/skills/pinned-skill/SKILL.md",
             "---\nname: pinned-skill\ndescription: 外部 pin\n---\n# pinned\n"),
            ("x-main/skills/pinned-skill/run.sh", "echo ok\n"),
        ]:
            b = data.encode()
            ti = tarfile.TarInfo(fname)
            ti.size = len(b)
            tf.addfile(ti, io.BytesIO(b))
    return buf.getvalue()


def test_b6_5_install_pins_lock_and_tamper_rejects(tmp_path: Path, home: Path,
                                                    monkeypatch):
    """webui 安装远程 skill → source 戳 + 引擎锁一体落地；此后 SKILL.md 被
    out-of-band 篡改（不经管理面）→ discover 拒索引。删 skill 连锁条目清。"""
    from loadn_webui import skills as wskills
    root = tmp_path / "webskills"
    root.mkdir()
    monkeypatch.setattr(wskills, "skills_dirs", lambda: [root])
    monkeypatch.setattr(wskills, "_download_tarball",
                        lambda o, n, b: _pin_tarball())
    assert wskills.install_from_github("acme/x-skill", "skills/pinned-skill") \
        == {"ok": True, "installed": ["pinned-skill"]}
    dst = root / "pinned-skill"
    md_text = (dst / "SKILL.md").read_text(encoding="utf-8")
    assert "source: github:acme/x-skill@main" in md_text
    assert (dst / "run.sh").exists()
    lock = json.loads((home / "skills.lock.json").read_text(encoding="utf-8"))
    entry = lock["skills"]["pinned-skill"]
    assert entry["sourceType"] == "github"
    assert entry["computedHash"] == hashlib.sha256(
        (dst / "SKILL.md").read_bytes()).hexdigest()

    # 引擎侧：挂进已信任工作区 → 过锁放行；篡改一字节 → 拒索引
    ws = tmp_path / "ws"
    (ws / ".claude" / "skills").mkdir(parents=True)
    (ws / ".claude" / "skills" / "pinned-skill").symlink_to(
        dst, target_is_directory=True)
    trust.admit(ws)
    assert "pinned-skill" in discover_skills(ws)
    (dst / "SKILL.md").write_text(md_text + "恶意追加", encoding="utf-8")
    assert "pinned-skill" not in discover_skills(ws), \
        "篡改已 pin 的 skill 仍被索引（rug-pull 失守）"

    # 删除（管理面）→ 锁条目同清，不留幽灵
    wskills.delete("pinned-skill")
    assert "pinned-skill" not in json.loads(
        (home / "skills.lock.json").read_text(encoding="utf-8"))["skills"]


# ---------------------------------------------------------------- B6-6（P1）
def test_b6_6_webui_edit_refreshes_lock(tmp_path: Path, home: Path, monkeypatch):
    """管理面编辑 SKILL.md（有意变更）→ 保存即刷新锁哈希：不误报 rug-pull、
    下一轮发现照常放行（编辑面与篡改面分离的信任语义）。"""
    from loadn_webui import skills as wskills
    root = tmp_path / "webskills"
    root.mkdir()
    monkeypatch.setattr(wskills, "skills_dirs", lambda: [root])
    monkeypatch.setattr(wskills, "_download_tarball",
                        lambda o, n, b: _pin_tarball())
    wskills.install_from_github("acme/x-skill", "skills/pinned-skill")
    dst = root / "pinned-skill"

    ws = tmp_path / "ws"
    (ws / ".claude" / "skills").mkdir(parents=True)
    (ws / ".claude" / "skills" / "pinned-skill").symlink_to(
        dst, target_is_directory=True)
    trust.admit(ws)

    # 经管理面编辑（保留 source 戳）→ 锁刷新 → 照常索引
    edited = (dst / "SKILL.md").read_text(encoding="utf-8") + "\n新增一段有意修改"
    wskills.write_file("pinned-skill", "SKILL.md", edited)
    entry = json.loads((home / "skills.lock.json").read_text(encoding="utf-8")
                       )["skills"]["pinned-skill"]
    assert entry["computedHash"] == hashlib.sha256(
        (dst / "SKILL.md").read_bytes()).hexdigest()
    assert "pinned-skill" in discover_skills(ws)
