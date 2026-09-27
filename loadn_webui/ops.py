"""发布-升级-回滚系统（R7）：运行环境与代码仓库彻底剥离。

架构（docs/RELEASE.md）：
  /opt/loadn/releases/vX.Y.Z/   每版自包含（代码+venv+前端+行为资产默认值）
  /opt/loadn/current -> vX.Y.Z  原子符号链接（ln -sfn + mv -T 双步换）
  /opt/loadn/wheelhouse/        离线 wheel 缓存（按 lock 哈希复用）
  数据根 LOADN_WEBUI_HOME        原地不动（零迁移）

流水线：tag → build（archive/前端/venv/冒烟）→ upgrade（preflight/DB 备份/
等 idle/换指针/restart/healthcheck）→ 失败自动回滚（L1）。

stdlib-only（argparse/shutil/sqlite3/urllib/fcntl/subprocess）——随每个
release 分发，不依赖 fastapi；loadn-ops wrapper 保证 current venv 坏了
也能自救（回落 previous）。
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------- 路径常量

DEPLOY_ROOT = Path(os.environ.get("LOADN_DEPLOY_ROOT", "/opt/loadn"))
RELEASES_DIR = DEPLOY_ROOT / "releases"
WHEELHOUSE_DIR = DEPLOY_ROOT / "wheelhouse"
BUILD_DIR = DEPLOY_ROOT / "build"
STATE_DIR = DEPLOY_ROOT / "state"
LOGS_DIR = DEPLOY_ROOT / "logs"
CURRENT_LINK = DEPLOY_ROOT / "current"
DEPLOY_JSON = STATE_DIR / "deploy.json"
OPS_LOCK = STATE_DIR / "ops.lock"
OPS_LOG = LOGS_DIR / "ops.log"

DATA_ROOT = Path(os.environ.get("LOADN_WEBUI_HOME")
                 or Path.home() / ".loadn-data")  # 开源默认
def _health_url() -> str:
    """健康检查地址：跟随 server 配置（改端口后 ops 不失效）。

    独立进程（release build）可能无 config.yaml——回落默认端口与
    load_config 行为一致；导入 config 失败不炸 ops 主流程。
    """
    try:
        from .config import CONFIG
        host = CONFIG.server.host or "127.0.0.1"
        port = CONFIG.server.port or 8792
    except Exception:                                   # noqa: BLE001
        host, port = "127.0.0.1", 8792
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"                              # 探测走回环
    return f"http://{host}:{port}/api/health"
SMOKE_PORT = 8799

# release 目录内必须存在的顶层（布局契约=CODE_ROOT 派生前提）
RELEASE_LAYOUT = ("loadn", "loadn_webui", "profiles", "prompts",
                  "pyproject.toml")

EXIT_OK = 0
EXIT_PRECONDITION = 2
EXIT_ROLLED_BACK = 3
EXIT_ROLLBACK_FAILED = 4


# ---------------------------------------------------------------- 基础设施

def _log(msg: str) -> None:
    """操作审计行（终端 + ops.log）。"""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{ts} {msg}"
    print(line)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with OPS_LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def verify_engine_version(build_dir: Path, tag: str) -> None:
    """版本单一真源门：仓库版本必须已 bump 到 tag（v0.6.12 起取代盖章）。

    历史设计是 release 时把 tag 盖进构建树（仓库留 0.3.0），导致仓库版本
    与发布版本长期漂移（health 双版本困惑的根源）。现为 git 单一真源：
    loadn/__init__.py 与 pyproject.toml 在打 tag 前随功能提交一起 bump，
    build 时校验一致——不一致直接拒发（防「忘了 bump」静默漂移）。
    """
    import re
    for rel, pat in (("loadn/__init__.py", r'__version__\s*=\s*"[^"]*"'),
                     ("pyproject.toml", r'^version\s*=\s*"[^"]*"')):
        f = build_dir / rel
        txt = f.read_text()              # 缺文件=布局变化，让它在下面统一抛
        m = re.search(pat, txt, flags=re.M)
        ver = m.group(0).split('"')[1] if m else None
        if ver != tag.lstrip("v"):
            raise SystemExit(
                f"✗ 版本未对齐：{rel} 是 {ver}，tag 是 {tag}——"
                f"先 bump 版本再打 tag（版本随功能提交进 git，单一真源）")


@contextmanager
def _ops_lock():
    """flock 互斥——同一时刻只允许一个 ops 命令操作指针。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fh = OPS_LOCK.open("w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("✗ 另一个 ops 命令正在运行（ops.lock 被持有）", file=sys.stderr)
        fh.close()
        raise SystemExit(EXIT_PRECONDITION)
    try:
        yield
    finally:
        fh.close()


def _read_deploy_json() -> dict:
    try:
        return json.loads(DEPLOY_JSON.read_text())
    except (OSError, json.JSONDecodeError):
        return {"current": None, "previous": None, "history": []}


def _write_deploy_json(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DEPLOY_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _current_version() -> str | None:
    """current 符号链接指向的版本名（vX.Y.Z）。"""
    try:
        return CURRENT_LINK.resolve().name
    except OSError:
        return None


def _list_releases() -> list[str]:
    if not RELEASES_DIR.exists():
        return []
    return sorted(
        d.name for d in RELEASES_DIR.iterdir()
        if d.is_dir() and d.name.startswith("v")
        and (d / "loadn_webui" / "ops.py").exists())


def _read_release_json(version: str) -> dict:
    p = RELEASES_DIR / version / "RELEASE.json"
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------- 指针切换

def _switch_current(target: str) -> None:
    """原子换 current 符号链接：ln -s 到 tmp → mv -T rename(2) 原子替换。"""
    tmp = DEPLOY_ROOT / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(RELEASES_DIR / target)
    os.rename(tmp, CURRENT_LINK)


# ---------------------------------------------------------------- 版本命令

def cmd_versions() -> int:
    rels = _list_releases()
    if not rels:
        print("（无 release——先在代码仓执行 release build）")
        return 0
    dep = _read_deploy_json()
    cur = _current_version()
    print(f"{'版本':12s} {'git_sha':10s} {'built_at':20s} {'标记':10s} venv")
    for v in rels:
        info = _read_release_json(v)
        sha = info.get("git_sha", "?")[:10]
        built = info.get("built_at", "?")[:19]
        marks = []
        if v == cur:
            marks.append("current")
        if v == dep.get("previous"):
            marks.append("previous")
        venv_ok = (RELEASES_DIR / v / ".venv" / "bin" / "python").exists()
        print(f"{v:12s} {sha:10s} {built:20s} {','.join(marks) or '-':10s}"
              f" {'✓' if venv_ok else '✗'}")
    return 0


def cmd_status() -> int:
    dep = _read_deploy_json()
    cur = _current_version()
    print(f"current:  {cur}")
    print(f"previous: {dep.get('previous')}")
    info = _read_release_json(cur) if cur else {}
    print(f"git_sha:  {info.get('git_sha', '?')[:16]}…")
    print(f"built_at: {info.get('built_at', '?')}")
    # 磁盘
    usage = shutil.disk_usage(DEPLOY_ROOT)
    print(f"deploy:   {DEPLOY_ROOT}（可用 {usage.free // (1<<30)}G）")
    # 引用旧 release 的活跃进程（turn 子进程可能还在跑旧 venv）
    if cur:
        for v in _list_releases():
            if v == cur:
                continue
            ref = _count_procs_referencing(v)
            if ref:
                print(f"⚠️  {v}: {ref} 个进程仍在引用（turn 收养中，不可 GC）")
    return 0


def _count_procs_referencing(version: str) -> int:
    """扫描 /proc 数有多少进程的 cmdline 或 cwd 含 release 路径。"""
    target = str(RELEASES_DIR / version)
    n = 0
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            cmdline = (pid_dir / "cmdline").read_bytes().decode(errors="replace")
            cwd = os.readlink(pid_dir / "cwd")
            if target in cmdline or target in cwd:
                n += 1
        except OSError:
            continue
    return n


# ---------------------------------------------------------------- build

def cmd_release_build(tag: str, *, skip_ui: bool = False,
                      skip_smoke: bool = False) -> int:
    """从代码仓构建一个 release 到 releases/vX.Y.Z/。

    仅限在代码仓内执行（有 .git 且有 tag）。产物：
    loadn/ loadn_webui/ profiles/ prompts/ scripts/ ui/dist/ pyproject.toml
    RELEASE.json .venv/
    """
    repo = Path.cwd()
    if not (repo / ".git").exists():
        print("✗ release build 必须在代码仓内执行（cwd 无 .git）", file=sys.stderr)
        return EXIT_PRECONDITION

    # 校验 tag
    r = subprocess.run(["git", "tag", "-l", tag], cwd=repo, capture_output=True,
                       text=True)
    if r.stdout.strip() != tag:
        print(f"✗ tag {tag} 不存在（先 git tag {tag}）", file=sys.stderr)
        return EXIT_PRECONDITION

    # 校验树干净（tag 是发布闸门——脏树意味着发布内容不确定）
    r = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                       capture_output=True, text=True)
    if r.stdout.strip() and not skip_smoke:
        dirty = r.stdout.strip().split("\n")[:3]
        print(f"✗ 工作树有未提交改动：{dirty}（commit 后再 build，或 --skip-smoke 调试）",
              file=sys.stderr)
        return EXIT_PRECONDITION

    git_sha = subprocess.run(["git", "rev-parse", tag], cwd=repo,
                             capture_output=True, text=True).stdout.strip()

    # 1) git archive 导出
    build_dir = BUILD_DIR / tag
    if build_dir.exists():
        shutil.rmtree(build_dir)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[1/5] git archive {tag} → {build_dir}")
    subprocess.run(["git", "archive", tag, f"--prefix={tag}/",
                    f"--output={build_dir}.tar"], cwd=repo, check=True)
    subprocess.run(["tar", "-xf", f"{build_dir}.tar", "-C", BUILD_DIR], check=True)
    (BUILD_DIR / f"{tag}.tar").unlink()
    verify_engine_version(build_dir, tag)
    print(f"[1/5] 引擎包版本盖章 → {tag}（loadn --version 与平台 release 对齐）")

    # 2) 前端构建
    if not skip_ui:
        print("[2/5] npm run build（复用仓库 node_modules）")
        ui_src = repo / "ui"
        r = subprocess.run(["npm", "run", "build"], cwd=ui_src,
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            print(f"✗ 前端构建失败：{r.stderr[-300:]}", file=sys.stderr)
            return EXIT_PRECONDITION
        dist_src = ui_src / "dist"
        dist_dst = build_dir / "ui" / "dist"
        dist_dst.parent.mkdir(parents=True, exist_ok=True)
        if dist_dst.exists():
            shutil.rmtree(dist_dst)
        shutil.copytree(dist_src, dist_dst)
    else:
        print("[2/5] 跳过前端构建（--skip-ui）")

    # 3) wheelhouse 预热
    lock_file = repo / "requirements.prod.lock"
    if not lock_file.exists():
        print("✗ requirements.prod.lock 不存在", file=sys.stderr)
        return EXIT_PRECONDITION
    lock_hash = _file_hash(lock_file)
    wh = WHEELHOUSE_DIR / lock_hash
    print(f"[3/5] wheelhouse {wh.name}（{'命中' if wh.exists() else '预热中'}）")
    if not wh.exists():
        wh.mkdir(parents=True)
        r = subprocess.run(
            [sys.executable, "-m", "pip", "download",
             "-r", str(lock_file), "-d", str(wh)],
            cwd=repo, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            shutil.rmtree(wh, ignore_errors=True)
            print(f"✗ wheelhouse 预热失败：{r.stderr[-300:]}", file=sys.stderr)
            return EXIT_PRECONDITION

    # 4) 先 mv 到 releases/（venv 必须在最终位置创建——console script 的
    #    shebang 嵌入绝对路径，先建后移会导致解释器路径指向已删除的 build/）
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    dst = RELEASES_DIR / tag
    if dst.exists():
        print(f"⚠️  {dst} 已存在，覆盖")
        shutil.rmtree(dst)
    os.rename(build_dir, dst)
    build_dir = dst

    # venv 构建（在最终位置）
    print("[4/5] venv 构建（离线安装）")
    venv_dir = build_dir / ".venv"
    venv_python = venv_dir / "bin" / "python"
    # 用系统 Python 建 venv：venv-from-venv 的 bin/python 是符号链→
    # 开发环境路径（/data/code/...），沙箱不挂载即断（exit 126 实测根因）
    system_python = "/usr/bin/python3"
    if not Path(system_python).exists():
        system_python = sys.executable       # 兜底（非 Linux 场景）
    subprocess.run([system_python, "-m", "venv", str(venv_dir)], check=True)
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--no-index",
         "--find-links", str(wh), "setuptools>=61", "wheel", "pip",
         "-q"], check=True, timeout=300)
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--no-index",
         "--find-links", str(wh), "-r", str(lock_file), "-q"],
        check=True, timeout=600)
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--no-deps",
         "--no-build-isolation", "-e", str(build_dir), "-q"],
        check=True, timeout=300)

    # 5) RELEASE.json + 冒烟
    from datetime import datetime, timezone
    release_info = {
        "version": tag, "git_sha": git_sha, "tag": tag,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lock_hash": lock_hash,
    }
    (build_dir / "RELEASE.json").write_text(
        json.dumps(release_info, ensure_ascii=False, indent=2))

    if not skip_smoke:
        print("[5/5] 冒烟测试（throwaway HOME，绝不碰生产数据根）")
        r = _smoke_test(build_dir)
        if r != 0:
            print(f"✗ 冒烟失败（exit {r}）", file=sys.stderr)
            shutil.rmtree(build_dir, ignore_errors=True)
            return EXIT_PRECONDITION

    # mv 已在 venv 构建前完成
    _log(f"release build {tag} sha={git_sha[:10]} lock={lock_hash}")
    print(f"✓ release {tag} → {dst}")
    return EXIT_OK


def _file_hash(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def _smoke_test(release_dir: Path) -> int:
    """在临时 HOME + 固定端口起一个实例，验 /api/health 后 kill。

    绝不碰生产数据根（双实例会触发 recover_after_restart 双收养+误杀）。
    """
    import tempfile
    with tempfile.TemporaryDirectory(prefix="loadn_smoke_") as tmp:
        env = {
            **{k: v for k, v in os.environ.items()
               if k in ("PATH", "HOME", "LANG")},
            "LOADN_WEBUI_HOME": tmp,
            "LOADN_STEALTH": "off",       # 冒烟不走 GLM
        }
        venv_python = release_dir / ".venv" / "bin" / "python"
        proc = subprocess.Popen(
            [str(venv_python), "-m", "loadn_webui", "serve",
             "--port", str(SMOKE_PORT)],
            cwd=str(release_dir), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for _ in range(30):
                time.sleep(1)
                try:
                    resp = urllib.request.urlopen(
                        f"http://127.0.0.1:{SMOKE_PORT}/api/health", timeout=3)
                    if resp.status == 200:
                        data = json.loads(resp.read())
                        rel = data.get("release", {})
                        ver = rel.get("version", "")
                        if ver and ver != release_dir.name:
                            print(f"  冒烟版本不符：{ver} ≠ {release_dir.name}")
                            return 1
                        print(f"  冒烟 ✓ /api/health 200（release={ver or 'N/A'}）")
                        return 0
                except Exception:          # noqa: BLE001
                    continue
            print("  冒烟超时（30s 无 health 响应）")
            return 1
        finally:
            proc.terminate()
            proc.wait(timeout=10)


# ---------------------------------------------------------------- upgrade

def cmd_upgrade(version: str | None = None, *, wait_idle: int = 1800,
                health_timeout: int = 90, no_backup: bool = False,
                yes: bool = False) -> int:
    """升级到指定版本（缺省=最新已 build 的）。失败自动回滚（L1）。"""
    with _ops_lock():
        rels = _list_releases()
        if not rels:
            print("✗ 无可用 release", file=sys.stderr)
            return EXIT_PRECONDITION
        target = version or rels[-1]
        if target not in rels:
            print(f"✗ {target} 不在 releases/（可用：{', '.join(rels[-3:])}）",
                  file=sys.stderr)
            return EXIT_PRECONDITION
        cur = _current_version()
        if target == cur:
            print(f"✓ 已是 {target}（current 就指向它）")
            return EXIT_OK

        # 1) preflight
        print(f"[1/5] preflight（目标 {target}）")
        problems = _preflight(RELEASES_DIR / target)
        if problems:
            for p in problems:
                print(f"  ✗ {p}", file=sys.stderr)
            return EXIT_PRECONDITION

        # 2) DB 备份
        if not no_backup:
            print("[2/5] DB 备份（backup API，保留 3 份）")
            _backup_db(target)

        # 3) 等 idle
        print(f"[3/5] 等 idle（超时 {wait_idle}s，--wait-idle 0 跳过）")
        if wait_idle > 0:
            ok = _wait_idle(wait_idle, yes=yes)
            if not ok:
                return EXIT_PRECONDITION

        # 4) 换指针 + restart
        print(f"[4/5] 切换 current: {cur} → {target}")
        old = cur
        _switch_current(target)
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", "loadn"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"✗ systemctl restart 失败：{r.stderr[-200:]}", file=sys.stderr)
            # 自动回滚
            if old:
                print(f"  自动回滚 → {old}")
                _switch_current(old)
                subprocess.run(["sudo", "-n", "systemctl", "restart", "loadn"],
                               timeout=60)
            return EXIT_ROLLED_BACK

        # 5) healthcheck
        print(f"[5/5] healthcheck（超时 {health_timeout}s，验证 release={target}）")
        ok = _healthcheck(health_timeout, expect=target)
        dep = _read_deploy_json()
        if ok:
            dep["previous"] = old
            dep["current"] = target
            dep.setdefault("history", []).append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "action": "upgrade", "from": old, "to": target,
                "result": "ok"})
            _write_deploy_json(dep)
            _log(f"upgrade {old} → {target} ok")
            print(f"✓ 升级成功：{target}（previous={old}）")
            return EXIT_OK

        # 失败 → 自动回滚
        print(f"✗ healthcheck 失败——自动回滚 → {old}", file=sys.stderr)
        if old:
            _switch_current(old)
            subprocess.run(["sudo", "-n", "systemctl", "restart", "loadn"],
                           timeout=60)
            time.sleep(3)
            rb_ok = _healthcheck(30, expect=old)
            dep.setdefault("history", []).append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "action": "upgrade", "from": old, "to": target,
                "result": "failed_auto_rollback"})
            _write_deploy_json(dep)
            _log(f"upgrade {old} → {target} FAILED, rolled back to {old}")
            if rb_ok:
                print(f"✓ 已回滚到 {old}（服务正常）", file=sys.stderr)
                return EXIT_ROLLED_BACK
            print("✗✗ 回滚后 healthcheck 也失败！手动恢复见 docs/RELEASE.md L3",
                  file=sys.stderr)
            return EXIT_ROLLBACK_FAILED
        return EXIT_ROLLED_BACK


def _preflight(release_dir: Path) -> list[str]:
    problems = []
    # release 完整性
    for item in RELEASE_LAYOUT:
        if not (release_dir / item).exists():
            problems.append(f"release 缺 {item}")
    venv_py = release_dir / ".venv" / "bin" / "python"
    if not venv_py.exists():
        problems.append("release 缺 .venv")
    else:
        r = subprocess.run(
            [str(venv_py), "-c", "import loadn_webui, loadn"],
            cwd=str(release_dir), capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            problems.append(f"venv import 失败：{r.stderr[-150:]}")
    # 数据根
    for f in ("config.yaml", "var/loadn.db", "var/server_token"):
        p = DATA_ROOT / f
        if not p.exists():
            problems.append(f"数据根缺 {f}（{p}）")
        elif not os.access(p, os.R_OK):
            problems.append(f"不可读：{p}")
    # vault 属主警告（不阻断）
    vault = DATA_ROOT / "var" / "vault.enc"
    if vault.exists() and not os.access(vault, os.R_OK):
        print(f"  ⚠️  {vault} 不可读（属主 root？chown <运行用户> 后不再告警）")
    # 磁盘
    usage = shutil.disk_usage(DEPLOY_ROOT)
    if usage.free < 2 << 30:
        problems.append(f"磁盘可用 <2G（{usage.free >> 30}G）")
    return problems


def _backup_db(target: str) -> Path:
    """sqlite3 backup API（WAL 安全）→ var/backups/db-pre-<target>.db，保留 3 份。"""
    src = DATA_ROOT / "var" / "loadn.db"
    backups = DATA_ROOT / "var" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    dst = backups / f"db-pre-{target}.db"
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    with dst_conn:
        src_conn.backup(dst_conn)
    src_conn.close()
    dst_conn.close()
    # 保留 3 份：按 mtime 排序 + 刚写的 dst 永在保护集（文件名排序在
    # 版本号回退时会判反新旧，v0.3.0 备份把自己删掉的实测教训）
    old_files = sorted((f for f in backups.glob("db-pre-*.db") if f != dst),
                       key=lambda f: f.stat().st_mtime)
    for f in old_files[:-2]:
        f.unlink()
    print(f"  备份 → {dst}（{dst.stat().st_size >> 20}MB）")
    return dst


def _wait_idle(timeout_s: int, *, yes: bool = False) -> bool:
    """轮询 DB 等 turns 无 queued/running。超时询问（--yes 跳过=强切）。"""
    db = DATA_ROOT / "var" / "loadn.db"
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            n = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE status IN"
                " ('queued','running')").fetchone()[0]
            conn.close()
        except sqlite3.Error:
            n = 0  # DB 不可读时不阻塞（preflight 已单独检查）
        if n == 0:
            print("  idle ✓（无 queued/running turn）")
            return True
        print(f"  {n} 个活跃 turn，等待…（{int(time.monotonic()-t0)}s/{timeout_s}s）")
        time.sleep(30)
    if yes:
        # 实测勘误：沙箱 turn 带 --die-with-parent，主进程死=沙箱同死，
        # 「KillMode=process 幸存+收养」对沙箱 turn 不成立——强切必打断
        active = _active_turns()
        print(f"  超时但 --yes：强切（将打断 {len(active)} 个活跃 turn）",
              file=sys.stderr)
        for t in active[:5]:
            print(f"     · turn {t['id']}  {t['session_id']}", file=sys.stderr)
        if active:
            _log(f"upgrade FORCE-SWITCH sacrificing turns: "
                 f"{[t['id'] for t in active]}")
        return True
    print(f"  超时 {timeout_s}s——仍非 idle。加 --yes 强切或延长 --wait-idle",
          file=sys.stderr)
    return False


def _active_turns() -> list[dict]:
    """当前 queued/running 的 turn（强切点名用）。"""
    db = DATA_ROOT / "var" / "loadn.db"
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(
            "SELECT id, session_id FROM turns WHERE status IN"
            " ('queued','running') ORDER BY id").fetchall()
        conn.close()
        return [{"id": r[0], "session_id": r[1]} for r in rows]
    except sqlite3.Error:
        return []


def _healthcheck(timeout_s: int, *, expect: str | None = None) -> bool:
    """轮询 /api/health 至 200；expect 时验证 release 版本字段。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            resp = urllib.request.urlopen(_health_url(), timeout=5)
            if resp.status == 200:
                if expect:
                    data = json.loads(resp.read())
                    rel = (data.get("release") or {}).get("version", "")
                    if rel != expect:
                        print(f"  health 200 但 release={rel} ≠ {expect}，等…")
                        time.sleep(3)
                        continue
                return True
        except Exception:                  # noqa: BLE001
            pass
        time.sleep(3)
    return False


# ---------------------------------------------------------------- rollback

def cmd_rollback(version: str | None = None, *, yes: bool = False) -> int:
    """回滚到指定版本（缺省=deploy.json 的 previous）。"""
    with _ops_lock():
        dep = _read_deploy_json()
        target = version or dep.get("previous")
        if not target:
            print("✗ 无回滚目标（deploy.json 无 previous，且未指定版本）",
                  file=sys.stderr)
            return EXIT_PRECONDITION
        if target not in _list_releases():
            print(f"✗ {target} 不在 releases/", file=sys.stderr)
            return EXIT_PRECONDITION
        cur = _current_version()
        if target == cur:
            print(f"✓ 已是 {target}")
            return EXIT_OK

        # schema_rev 门禁
        problems = _schema_gate(target)
        if problems and not yes:
            for p in problems:
                print(f"  ⚠️  {p}", file=sys.stderr)
            print("  加 --yes 确认回滚", file=sys.stderr)
            return EXIT_PRECONDITION

        print(f"回滚：{cur} → {target}")
        _switch_current(target)
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", "loadn"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"✗ restart 失败：{r.stderr[-200:]}", file=sys.stderr)
            return EXIT_ROLLBACK_FAILED
        time.sleep(3)
        if _healthcheck(60, expect=target):
            dep["current"] = target
            dep["previous"] = cur
            dep.setdefault("history", []).append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "action": "rollback", "from": cur, "to": target,
                "result": "ok"})
            _write_deploy_json(dep)
            _log(f"rollback {cur} → {target} ok")
            print(f"✓ 回滚成功：{target}")
            return EXIT_OK
        print("✗ 回滚后 healthcheck 失败", file=sys.stderr)
        return EXIT_ROLLBACK_FAILED


def _schema_gate(target: str) -> list[str]:
    """回滚 schema_rev 门禁：目标版本声明 rev < DB rev → 警告（additive-only
    契约下旧代码可跑新 schema，多余列无害）；目标缺列 → 无法检测（旧版
    RELEASE.json 可能没写 schema_rev），仅在两者都有时比对。"""
    problems = []
    db = DATA_ROOT / "var" / "loadn.db"
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        row = conn.execute(
            "SELECT value FROM kv WHERE key='schema_rev'").fetchone()
        conn.close()
        db_rev = int(row[0]) if row else 0
    except (sqlite3.Error, TypeError):
        return problems
    target_info = _read_release_json(target)
    target_rev = target_info.get("schema_rev", db_rev)  # 缺省视为同版
    if target_rev < db_rev:
        problems.append(
            f"schema_rev 降级：目标 {target}(rev={target_rev}) < "
            f"DB(rev={db_rev})——additive-only 契约下可跑（多余列无害）")
    return problems


# ---------------------------------------------------------------- GC

def cmd_gc(keep: int = 4) -> int:
    """清理旧 release（保护 current/previous/被进程引用/schema_rev 兜底）。"""
    with _ops_lock():
        rels = _list_releases()
        dep = _read_deploy_json()
        cur = _current_version()
        prev = dep.get("previous")
        # 受保护集合
        protected = set()
        if cur:
            protected.add(cur)
        if prev:
            protected.add(prev)
        # 被进程引用的
        for v in rels:
            if v not in protected and _count_procs_referencing(v):
                protected.add(v)
                print(f"  保护 {v}（{_count_procs_referencing(v)} 个进程引用）")
        # schema_rev 兜底：保留能看懂当前 DB schema 的最新版本
        if len(rels) > 1:
            protected.add(rels[-1])  # 最新的总是保留

        deletable = [v for v in rels if v not in protected]
        # keep = 总保留数下限；protected 数已超 keep 则全可删非保护项
        protected_count = len(protected & set(rels))
        to_keep_non_protected = max(0, keep - protected_count)
        to_delete = deletable[to_keep_non_protected:]
        if not to_delete:
            print("无待清理 release（全受保护或在保留数内）")
            return 0
        for v in to_delete:
            d = RELEASES_DIR / v
            size_mb = sum(f.stat().st_size for f in d.rglob("*")
                          if f.is_file()) >> 20
            shutil.rmtree(d)
            print(f"  删除 {v}（~{size_mb}MB）")
        _log(f"gc keep={keep} deleted={','.join(to_delete)}")
    return 0


# ---------------------------------------------------------------- repair-venv

def cmd_repair_venv(version: str) -> int:
    """从 wheelhouse 离线重建某 release 的 venv（L4 恢复路径）。"""
    rel_dir = RELEASES_DIR / version
    if not rel_dir.exists():
        print(f"✗ {version} 不存在", file=sys.stderr)
        return EXIT_PRECONDITION
    info = _read_release_json(version)
    lock_hash = info.get("lock_hash", "")
    wh = WHEELHOUSE_DIR / lock_hash
    if not wh.exists():
        print(f"✗ wheelhouse/{lock_hash} 不存在（需从代码仓重建）", file=sys.stderr)
        return EXIT_PRECONDITION
    venv_dir = rel_dir / ".venv"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv_python = venv_dir / "bin" / "python"
    # 用系统 Python 建 venv：venv-from-venv 的 bin/python 是符号链→
    # 开发环境路径（/data/code/...），沙箱不挂载即断（exit 126 实测根因）
    system_python = "/usr/bin/python3"
    if not Path(system_python).exists():
        system_python = sys.executable       # 兜底（非 Linux 场景）
    subprocess.run([system_python, "-m", "venv", str(venv_dir)], check=True)
    subprocess.run([str(venv_python), "-m", "pip", "install", "--no-index",
                    "--find-links", str(wh), "setuptools>=61", "wheel", "pip",
                    "-q"], check=True, timeout=300)
    lock_file = rel_dir / "requirements.prod.lock"
    if lock_file.exists():
        subprocess.run([str(venv_python), "-m", "pip", "install", "--no-index",
                        "--find-links", str(wh), "-r", str(lock_file), "-q"],
                       check=True, timeout=600)
    subprocess.run([str(venv_python), "-m", "pip", "install", "--no-deps",
                    "--no-build-isolation", "-e", str(rel_dir), "-q"],
                   check=True, timeout=300)
    print(f"✓ venv 重建完成 → {venv_dir}")
    _log(f"repair-venv {version}")
    return EXIT_OK
