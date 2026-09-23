"""数据备份系统（R8）：定时+手动备份，异盘存储，恢复验证。

备份目标（/mnt/sata1/loadn-backup/<date>/——异物理盘，NVMe 坏不丢）：
  config.yaml        配置（小，每次必备）
  var/loadn.db       主 DB（201M，sqlite3 backup API 保证一致性）
  var/vault.enc      凭证库（小，加密）
  var/.vault_key     vault 密钥（小，0600）
  var/server_token   API token（小）
  var/audit.db       审计账本（小）
  var/audit_heads/   审计锚点（小）
  workspace/         会话数据（选择性：排除 node_modules/.venv/.snapshots/
                                chrome*/.git 等可重建内容）

命令：
  loadn-web backup run [--full-workspace]   手动备份
  loadn-web backup verify                   验证最近备份完整性
  loadn-web backup list                     列出备份
  loadn-web backup restore <date>           恢复（谨慎——会覆盖数据根）
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import PATHS

BACKUP_ROOT = Path(os.environ.get(
    "LOADN_BACKUP_ROOT", "/mnt/sata1/loadn-backup"))

# 每次必备的小文件（快速，秒级）
CRITICAL_FILES = [
    "config.yaml",
    "var/server_token",
    "var/vault.enc",
    "var/.vault_key",
    "var/audit.db",
]

# DB 用 backup API（WAL 安全）
DB_PATH = "var/loadn.db"

# workspace 排除项（可重建/可再生/体积大不可替代价值低）
WORKSPACE_EXCLUDES = [
    "node_modules", ".venv", ".snapshots", ".git",
    "chrome_profile", ".cache", "__pycache__",
    ".benchmarks", "*.egg-info", ".pytest_cache",
]

# 保留天数
RETENTION_DAYS = 30


def _backup_dir(date: str | None = None) -> Path:
    d = date or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return BACKUP_ROOT / d


def cmd_backup_run(full_workspace: bool = False) -> int:
    """执行备份（手动或 cron 调用）。"""
    dst = _backup_dir()
    dst.mkdir(parents=True, exist_ok=True)
    data_root = PATHS["root"]
    errors = []

    # 1) 关键小文件（直接拷贝）
    print("[1/3] 关键文件（config/token/vault/audit）")
    for rel in CRITICAL_FILES:
        src = data_root / rel
        if not src.exists():
            continue
        dst_f = dst / rel
        dst_f.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst_f)
            os.chmod(dst_f, 0o600)
        except OSError as e:
            errors.append(f"{rel}: {e}")

    # 2) 主 DB（backup API 保证一致性）
    print("[2/3] 主 DB（sqlite3 backup API）")
    db_src = data_root / DB_PATH
    db_dst = dst / DB_PATH
    if db_src.exists():
        db_dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            src_conn = sqlite3.connect(str(db_src), timeout=30)
            dst_conn = sqlite3.connect(str(db_dst))
            with dst_conn:
                src_conn.backup(dst_conn)
            src_conn.close()
            dst_conn.close()
            os.chmod(db_dst, 0o600)
            size_mb = db_dst.stat().st_size >> 20
            print(f"  → {db_dst}（{size_mb}MB）")
        except sqlite3.Error as e:
            errors.append(f"db: {e}")

    # 3) 审计锚点目录
    heads_src = data_root / "var" / "audit_heads"
    if heads_src.exists():
        heads_dst = dst / "var" / "audit_heads"
        if not heads_dst.exists():
            shutil.copytree(heads_src, heads_dst)

    # 4) workspace（选择性）
    print("[3/3] workspace（排除可重建目录）")
    ws_src = data_root / "workspace"
    if ws_src.exists():
        ws_dst = dst / "workspace"
        excludes = WORKSPACE_EXCLUDES if not full_workspace else [
            "node_modules", ".venv", ".snapshots"]
        rsync_args = ["rsync", "-a", "--delete"]
        for exc in excludes:
            rsync_args += [f"--exclude={exc}"]
        rsync_args += [str(ws_src) + "/", str(ws_dst) + "/"]
        r = subprocess.run(rsync_args, capture_output=True, text=True,
                           timeout=3600)
        if r.returncode != 0:
            errors.append(f"workspace rsync: {r.stderr[-200:]}")

    # manifest
    manifest = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_root": str(data_root),
        "full_workspace": full_workspace,
        "errors": errors,
    }
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=2))

    if errors:
        print(f"✗ 备份完成但有问题：{errors}", file=sys.stderr)
        return 1
    print(f"✓ 备份完成 → {dst}")

    # 5) 清理过期备份
    _cleanup_old()
    return 0


def cmd_backup_list() -> int:
    """列出所有备份。"""
    if not BACKUP_ROOT.exists():
        print("（无备份目录）")
        return 0
    print(f"{'备份':24s} {'大小':8s} {'workspace':10s} {'错误':4s}")
    for d in sorted(BACKUP_ROOT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        manifest = d / "manifest.json"
        full = False
        errs = 0
        if manifest.exists():
            try:
                m = json.loads(manifest.read_text())
                full = m.get("full_workspace", False)
                errs = len(m.get("errors", []))
            except (OSError, json.JSONDecodeError):
                errs = -1
        size = sum(f.stat().st_size for f in d.rglob("*")
                   if f.is_file())
        size_str = f"{size >> 30}G" if size > (1 << 30) else f"{size >> 20}M"
        print(f"{d.name:24s} {size_str:8s} {'full' if full else 'select':10s}"
              f" {errs:4d}")
    return 0


def cmd_backup_verify(date: str | None = None) -> int:
    """验证备份完整性：关键文件存在+DB 可打开+manifest 可读。"""
    if date:
        dirs = [BACKUP_ROOT / date] if (BACKUP_ROOT / date).exists() else []
    else:
        dirs = sorted(
            [d for d in BACKUP_ROOT.iterdir() if d.is_dir()],
            reverse=True)[:1]

    if not dirs:
        print("✗ 无备份可验证", file=sys.stderr)
        return 1

    d = dirs[0]
    problems = []

    # 关键文件
    for rel in CRITICAL_FILES:
        f = d / rel
        if not f.exists():
            # vault_key 可选
            if rel == "var/.vault_key":
                continue
            problems.append(f"缺 {rel}")

    # DB 可打开
    db = d / DB_PATH
    if db.exists():
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
            conn.close()
        except sqlite3.Error as e:
            problems.append(f"DB 不可读: {e}")
    else:
        problems.append("缺主 DB")

    # manifest
    mf = d / "manifest.json"
    if not mf.exists():
        problems.append("缺 manifest")
    else:
        try:
            m = json.loads(mf.read_text())
            if m.get("errors"):
                problems.append(f"备份时有错误: {m['errors'][:2]}")
        except (OSError, json.JSONDecodeError):
            problems.append("manifest 损坏")

    if problems:
        print(f"✗ 备份 {d.name} 有问题：")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"✓ 备份 {d.name} 完整（关键文件+DB+manifest 均 OK）")
    return 0


def cmd_backup_restore(date: str) -> int:
    """恢复备份到数据根（危险操作——覆盖现有数据）。"""
    src = BACKUP_ROOT / date
    if not src.exists():
        print(f"✗ 备份 {date} 不存在", file=sys.stderr)
        return 1
    data_root = PATHS["root"]

    print(f"⚠️  即将从 {src} 恢复到 {data_root}——现有数据将被覆盖！")
    print("建议先手动备份当前状态。确认请输入 yes：")
    confirm = input().strip()
    if confirm != "yes":
        print("取消")
        return 1

    # 先备份当前状态
    print("  先备份当前状态…")
    cmd_backup_run()

    # 恢复关键文件
    for rel in CRITICAL_FILES:
        f = src / rel
        if f.exists():
            dst_f = data_root / rel
            dst_f.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst_f)
            os.chmod(dst_f, 0o600 if "vault" in rel or "token" in rel
                     else 0o644)

    # 恢复 DB
    db_src = src / DB_PATH
    if db_src.exists():
        shutil.copy2(db_src, data_root / DB_PATH)

    # 恢复 workspace
    ws_src = src / "workspace"
    if ws_src.exists():
        subprocess.run(
            ["rsync", "-a", "--delete",
             str(ws_src) + "/", str(data_root / "workspace") + "/"],
            timeout=3600)

    print(f"✓ 恢复完成（来源 {date}）——建议 systemctl restart loadn")
    return 0


def _cleanup_old() -> None:
    """清理超过保留天数的备份。"""
    if not BACKUP_ROOT.exists():
        return
    cutoff = time.time() - RETENTION_DAYS * 86400
    removed = 0
    for d in BACKUP_ROOT.iterdir():
        if not d.is_dir():
            continue
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d)
                removed += 1
        except OSError:
            pass
    if removed:
        print(f"  清理 {removed} 个过期备份（>{RETENTION_DAYS}天）")


import time  # noqa: E402 （_cleanup_old 用）
