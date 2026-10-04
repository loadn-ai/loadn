"""skill 供应链锁存储（P0-3/P1）——引擎 core 与平台 webui 共用的顶层模块。

进程边界（PROTOCOL.md §0）禁 loadn_webui import loadn.core，而锁的读写在
两侧都有消费点（引擎 discover 校验、webui 安装/编辑面写入）——与
truststore.py 同款定位：存储与判定原语在顶层，core/skills.py 转为消费者。

锁语义：外部来源 skill（SKILL.md frontmatter 带 source 字段）必须命中锁
且 sha256 一致才可索引（rug-pull 防护，fail-closed）。写入只发生在可信
管理面（webui 安装/编辑、`loadn skills lock` CLI）；out-of-band 篡改
（不经管理面改文件）会被引擎侧拒索引。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from loadn import loadn_home

LOCK_VERSION = 1


def lock_paths() -> list[Path]:
    """锁文件搜索序（先到先得，按 name 覆盖合并）：包内（随发布分发的
    内置 skill 锁）< $LOADN_HOME（用户外部 skill 的可写锁，
    `loadn skills lock` 写这里——site-packages 可能只读，不动包内文件）。"""
    return [Path(__file__).resolve().parent / "skills.lock.json",
            loadn_home() / "skills.lock.json"]


def load_locks() -> dict[str, dict]:
    """合并后的锁视图：{name: {source, sourceType, skillPath, computedHash}}。"""
    merged: dict[str, dict] = {}
    for p in lock_paths():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        skills = data.get("skills") if isinstance(data, dict) else None
        if isinstance(skills, dict):
            merged.update({k: v for k, v in skills.items() if isinstance(v, dict)})
    return merged


def skill_hash(skill_md: Path) -> str:
    """SKILL.md 全文 sha256（zcode computedHash 同构）。"""
    return hashlib.sha256(skill_md.read_bytes()).hexdigest()


def write_user_lock(skills: dict) -> None:
    """整仓写用户层锁（键排序 + 固定序列化——CLI 与 webui 安装面共用的单一真相）。"""
    user_lock = lock_paths()[-1]
    user_lock.parent.mkdir(parents=True, exist_ok=True)
    user_lock.write_text(
        json.dumps({"version": LOCK_VERSION,
                    "skills": {k: skills[k] for k in sorted(skills)}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_lock_entry(name: str, skill_md: Path, source: str) -> None:
    """单条锁 upsert（webui 安装/编辑面用）：写 $LOADN_HOME 层。

    经可信管理面发生的变更属有意变更——直接覆盖该条（CLI 场景的
    --update 门管命令行）；out-of-band 篡改（不经管理面）才会被
    discover 侧锁校验拦下。条目形状与 `loadn skills lock` 一致。
    """
    source_type, _, ref = source.partition(":")
    entry = {"source": ref or source,
             "sourceType": source_type if ref else "local",
             "skillPath": str(skill_md),
             "computedHash": skill_hash(skill_md)}
    try:
        data = json.loads(lock_paths()[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict):
        skills = {}
    skills[name] = entry
    write_user_lock(skills)


def remove_lock_entry(name: str) -> None:
    """删用户层锁条目（skill 删除时清幽灵；没有该条/文件坏 → 静默返回）。"""
    try:
        data = json.loads(lock_paths()[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict) or name not in skills:
        return
    del skills[name]
    write_user_lock(skills)
