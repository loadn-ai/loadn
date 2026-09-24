"""Workspace 信任门 store（P0-2，zcode Z1 × pi-P3 同构）——顶层叶模块。

**为何在顶层包**：进程边界铁律（PROTOCOL.md §0）禁止 loadn_webui import
loadn.core。信任 store 的写入方在平台侧（workspace.write_settings 物化
项目 settings 后 admit），判定方在引擎侧（hooks/skills/agents/permissions
消费点）——两侧共享同一 store，逻辑落此；引擎侧经 loadn.core.trust 再
导出壳使用。

项目级资源是命令执行与提示注入的面：clone 一个带 .loadn/settings.json
的仓库，hooks 段就能让任意命令在每次工具调用时执行；.claude/skills 注入
指令；permissions.allow 自我放行。首次使用必须过信任门——store 记住
「信任时看到了什么」（内容摘要），资源一变即回到未信任。

- store：$LOADN_HOME/trust.json = {规范化 root: {trusted, digest, v}}
- root：最近祖先（含 cwd）中带资源标记的目录（pi 最近祖先解析同构）；
  子目录运行继承 root 的信任，digest 对 root 资源树递归计算——信任父
  目录即覆盖其子树
- 受信面：{.loadn,.agent,.claude}/settings.json（hooks+permissions 双
  消费）与 */skills/**、*/agents/**（subagent system_add 注入面）
- 用户全局（$LOADN_HOME 与 ~/.claude）永远不是项目资源（pi 同构：
  用户自己的东西不需要对自己的工作区过门）
- 未信任 → 项目级资源**降级跳过**（不崩、一条 warning；全局资源不受影响）
- gate() 从不问人：headless（-p）无处问 = fail-closed（ask→deny 哲学）；
  交互 REPL 在启动时问一次（repl preflight → admit/revoke）
- 原子写 tmp+rename；store 损坏视为空（fail-closed 到未信任，不炸启动）
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from loadn import loadn_home
from loadn.util import get_logger

log = get_logger(__name__)

DIGEST_SCHEMA_VERSION = 1     # 摘要算法/受信面变更时 bump（旧条目视为不匹配→重问）
MAX_DIGEST_FILES = 2000       # 资源树扫描上限（防恶意仓库撑爆哈希时间）
MAX_DIGEST_BYTES = 8 * 1024 * 1024

_MARKER_DIRS = (".loadn", ".agent", ".claude")
# settings.json=hooks/permissions；policy.json=P0-4b 审批回写的 bash 规则
# （未信任仓库不得自带回写规则自我放行）
_MARKER_FILES = ("settings.json", "policy.json")
# 信任摘要面=命令/权限/提示注入的结构性资源：settings.json（hooks+
# permissions）与 agents/**（subagent system_add）。**skills 不在摘要面**：
# 外部 skill 的内容钉在 P0-3 供应链锁（skills.lock.json）——两把锁管不同
# 的面，避免「改 skill 先触发信任摘要」把锁层挡在身后（分层，B6 实证）。
_SUBTREES = ("agents",)


# ---------------------------------------------------------------- 受信面
def _resource_files(root: Path) -> list[Path]:
    """root 下**内容进摘要**的文件：settings.json + agents/**（确定性排序）。

    用户全局排除：$LOADN_HOME 与 ~/.claude 下的条目不是项目资源。
    """
    global_roots = (loadn_home().resolve(), (Path.home() / ".claude").resolve())
    out: list[Path] = []
    for marker in _MARKER_DIRS:
        base = (root / marker).resolve()
        if any(g == base or g in base.parents for g in global_roots):
            continue          # 用户全局面（$LOADN_HOME / ~/.claude）无需过门
        for name in _MARKER_FILES:
            f = base / name
            if f.is_file():
                out.append(f)
        for sub in _SUBTREES:
            tree = base / sub
            if tree.is_dir():
                for p in tree.rglob("*"):
                    if p.is_file():
                        out.append(p)
                    if len(out) >= MAX_DIGEST_FILES:
                        log.warning("信任面文件数超上限 %d，截断摘要（root=%s）",
                                    MAX_DIGEST_FILES, root)
                        return sorted(set(out))
    return sorted(set(out))


def _skill_dirs(root: Path) -> list[str]:
    """root 下项目级 skill **目录名**集合（结构而非内容——新 skill 目录
    加入/消失改变信任摘要=重新询问；SKILL.md 内容编辑不触发，内容钉在
    P0-3 供应链锁）。"""
    global_roots = (loadn_home().resolve(), (Path.home() / ".claude").resolve())
    names: list[str] = []
    for marker in _MARKER_DIRS:
        base = (root / marker).resolve()
        if any(g == base or g in base.parents for g in global_roots):
            continue
        skills = base / "skills"
        try:
            names += sorted(d.name for d in skills.iterdir()
                            if d.is_dir() or d.is_symlink())
        except OSError:
            continue
    return sorted(set(names))


def project_root(cwd: Path) -> Path | None:
    """最近祖先（含 cwd）中带资源标记的目录；无资源 → None。"""
    cur = cwd.resolve()
    while True:
        if _resource_files(cur) or _skill_dirs(cur):
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def digest(root: Path) -> str:
    """受信资源摘要：内容面（settings/agents 相对路径+长度+内容）+
    结构面（skill 目录名集合），sha256。"""
    h = hashlib.sha256()
    h.update(f"v{DIGEST_SCHEMA_VERSION}".encode())
    for f in _resource_files(root):
        h.update(str(f.relative_to(root)).encode() + b"\0")
        try:
            data = f.read_bytes()
        except OSError:
            data = b"<unreadable>"
        h.update(str(min(len(data), MAX_DIGEST_BYTES)).encode() + b"\0")
        h.update(data[:MAX_DIGEST_BYTES])
    for name in _skill_dirs(root):
        h.update(b"skill-dir:" + name.encode() + b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------- store
def _store_path() -> Path:
    return loadn_home() / "trust.json"


def _load_store() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_store(data: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True,
                              ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)          # 原子（pi proper-lockfile 的单写方简化形态）


# ---------------------------------------------------------------- 判定与写入
def gate(cwd: Path) -> tuple[bool, str]:
    """项目级资源是否放行。返回 (ok, 原因文案)。

    不问人：交互询问在 REPL preflight（ask 一次 → admit/revoke）；
    headless 调用方拿到 (False, …) 即降级跳过——fail-closed。
    """
    root = project_root(cwd)
    if root is None:
        return True, "no-resources"
    entry = _load_store().get(str(root))
    if not isinstance(entry, dict) or "trusted" not in entry:
        return False, (f"工作区未信任：{root}（项目级 hooks/skills/权限规则"
                       "已降级跳过；交互模式启动时询问，或 loadn trust 命令）")
    if not entry.get("trusted"):
        return False, f"工作区已被标记不信任：{root}"
    if entry.get("v") != DIGEST_SCHEMA_VERSION or entry.get("digest") != digest(root):
        return False, (f"受信资源自信任后已变更（摘要不匹配）：{root}"
                       "——需重新确认（重新信任会记录新摘要）")
    return True, "trusted"


def admit(root: Path) -> None:
    """信任并记录当前资源摘要（REPL「信任」选项/CLI 显式操作）。"""
    data = _load_store()
    data[str(root.resolve())] = {"trusted": True, "v": DIGEST_SCHEMA_VERSION,
                                 "digest": digest(root)}
    _save_store(data)
    log.info("工作区已信任：%s", root)


def revoke(root: Path) -> None:
    """显式标记不信任（记住拒绝，REPL 不再重复问）。"""
    data = _load_store()
    data[str(root.resolve())] = {"trusted": False, "v": DIGEST_SCHEMA_VERSION,
                                 "digest": digest(root)}
    _save_store(data)
    log.info("工作区已标记不信任：%s", root)


def forget(root: Path) -> None:
    """删除条目（回到「未见过」，下次交互再问）。"""
    data = _load_store()
    data.pop(str(root.resolve()), None)
    _save_store(data)
