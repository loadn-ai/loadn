"""skill 发现、按会话挂载、管理面（CRUD / 编辑 / zip / GitHub 安装）。

平台 skills 只留仓库 skills/，创建会话时 symlink 到工作区 .claude/skills/
——零拷贝、版本最新、真选择性（P0 已实测无头会话识别 symlink 目录）。
追加/移除后下一 turn 生效（CLI 会话启动时枚举）；编辑文件经 symlink 实时可见。

安装链路统一 GitHub tarball（codeload 直连可用，不走 git）；skillhub 等市场
只做发现（见 skillhub.py），repo_url 片段 #a~b~c 约定在 parse_repo_url 解析。
第三方 skill 代码会在 bypassPermissions 下被 agent 执行——装前即信任作者。
"""
from __future__ import annotations

import io
import json
import re
import shutil
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

from .config import PATHS, skills_dirs
from .util import get_logger, iso, slugify

log = get_logger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# 编辑/上传的体积与数量上限（skill 是提示词+小脚本，不该有大数据）
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SKILL_TOTAL_BYTES = 100 * 1024 * 1024
MAX_SKILL_FILES = 2000
MAX_TARBALL_BYTES = 200 * 1024 * 1024
MAX_INSTALL_AT_ONCE = 20
SOURCE_FILE = ".loadn-source.json"
DISABLED_FILE = ".loadn-disabled"   # 存在即禁用：不挂载新会话 + 摘除现有挂载


# ---------------------------------------------------------------- 读取
def parse_frontmatter(md: str) -> dict:
    """SKILL.md 头部 frontmatter（name/description，宽松解析，同 available() 口径）。"""
    out = {"name": "", "description": ""}
    parts = md.split("---", 2)
    if len(parts) < 3:
        return out
    for ln in parts[1].splitlines():
        m = re.match(r"^(name|description):\s*(.*)$", ln.strip())
        if m and not out[m.group(1)]:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _source(name: str) -> dict | None:
    d = skill_dir(name)
    if d is None:
        return None
    try:
        return json.loads((d / SOURCE_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _dir_size(p: Path) -> int:
    n = 0
    for f in p.rglob("*"):
        if f.is_file() and not f.is_symlink():
            try:
                n += f.stat().st_size
            except OSError:
                pass
    return n


def available() -> list[dict]:
    """全部搜索路径（repo skills/ + overlay）下所有合法 skill + 管理元数据。

    同名以先者（repo）为准；返回条目带 dir 锥（读写定向）。
    """
    out = []
    seen = set()
    for root in skills_dirs():
        for p in sorted(root.iterdir()):
            if p.name in seen:
                continue
            md = p / "SKILL.md"
            if not (p.is_dir() and md.exists()):
                continue
            seen.add(p.name)
            try:
                head = parse_frontmatter(md.read_text(errors="replace"))
            except OSError:
                head = {"description": ""}
            item = {"name": p.name, "description": head.get("description", ""),
                    "disabled": (p / DISABLED_FILE).exists(),
                    "mtime": datetime.fromtimestamp(md.stat().st_mtime)
                             .strftime("%m-%d %H:%M")}
            src = _source(p.name)
            if src:
                item["source"] = src
            out.append(item)
    return out


def skill_dir(name: str) -> Path | None:
    """按搜索路径序找 skill 目录（repo skills/ 优先于 overlay）。"""
    if not NAME_RE.match(name or ""):
        return None
    for root in skills_dirs():
        p = root / name
        if p.is_dir() and (p / "SKILL.md").exists():
            return p
    return None


def _writable_root() -> Path:
    """安装/新建目标：搜索路径第一个（repo skills/，部署态可写）。"""
    dirs = skills_dirs()
    return dirs[0] if dirs else PATHS["skills"]


def get(name: str) -> dict | None:
    """详情：frontmatter + 溯源 + 文件树。"""
    d = skill_dir(name)
    if d is None:
        return None
    md = (d / "SKILL.md").read_text(errors="replace")
    fm = parse_frontmatter(md)
    files = []
    for f in sorted(d.rglob("*")):
        rel = f.relative_to(d)
        if any(part.startswith(".") for part in rel.parts):   # .git/.loadn-source 等不展示
            continue
        files.append({"path": str(rel), "size": (f.stat().st_size if f.is_file() else -1)})
    out = {"name": name, "description": fm.get("description", ""),
           "fm_name": fm.get("name", ""), "files": files, "mtime": iso()}
    src = _source(name)
    if src:
        out["source"] = src
    return out


def read_file(name: str, rel: str) -> str:
    """读 skill 内文本文件（防穿越，2MB）。"""
    d = skill_dir(name)
    if d is None:
        raise FileNotFoundError(f"skill 不存在: {name}")
    p = (d / rel).resolve()
    try:
        p.relative_to(d.resolve())
    except ValueError:
        raise PermissionError("路径越界") from None
    if not p.is_file():
        raise FileNotFoundError(rel)
    if p.stat().st_size > MAX_FILE_BYTES:
        raise PermissionError("文件超过 2MB，请在仓库内直接编辑")
    return p.read_text(errors="replace")


def write_file(name: str, rel: str, content: str, create: bool = False) -> dict:
    """写 skill 内文本文件。SKILL.md 缺 description 时警告不阻断（CLI 发现依赖它）。"""
    d = skill_dir(name)
    if d is None:
        raise FileNotFoundError(f"skill 不存在: {name}")
    p_rel = Path(rel)
    if p_rel.is_absolute() or ".." in p_rel.parts or p_rel.parts[0].startswith("."):
        raise PermissionError("非法路径")
    p = (d / p_rel).resolve()
    try:
        p.relative_to(d.resolve())
    except ValueError:
        raise PermissionError("路径越界") from None
    if p.is_dir():
        raise PermissionError("是目录")
    if p.exists() and p.stat().st_size > MAX_FILE_BYTES:
        raise PermissionError("目标文件超过 2MB")
    if len(content.encode()) > MAX_FILE_BYTES:
        raise PermissionError("内容超过 2MB")
    if not create and not p.exists():
        raise FileNotFoundError(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    warn = None
    if p.name == "SKILL.md":
        fm = parse_frontmatter(content)
        if not fm.get("description"):
            warn = "SKILL.md 缺 frontmatter description，CLI 可能无法发现该 skill"
    return {"ok": True, "path": rel, "warning": warn}


def delete_file(name: str, rel: str) -> dict:
    d = skill_dir(name)
    if d is None:
        raise FileNotFoundError(f"skill 不存在: {name}")
    if Path(rel).name == "SKILL.md" and len(Path(rel).parts) == 1:
        raise PermissionError("SKILL.md 是 skill 定义，不能删（可删整个 skill）")
    p = (d / rel).resolve()
    try:
        p.relative_to(d.resolve())
    except ValueError:
        raise PermissionError("路径越界") from None
    if not p.exists():
        raise FileNotFoundError(rel)
    shutil.rmtree(p) if p.is_dir() else p.unlink()
    return {"ok": True}


# ---------------------------------------------------------------- 新建 / 删除
SKILL_TEMPLATE = """---
name: {name}
description: {description}
---

# {name}

（在这里写这个 skill 的工作流程、约束与红线。agent 挂载后会按此文件行事。）

## 步骤

1. …

## 注意

- …
"""


def create(name: str, description: str) -> dict:
    if not NAME_RE.match(name or ""):
        raise ValueError("名称需匹配 ^[a-z0-9][a-z0-9._-]{0,63}$（小写开头，限小写字母/数字/._-）")
    d = _writable_root() / name
    if skill_dir(name) is not None or d.exists():
        raise FileExistsError(f"已存在: {name}")
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(name=name, description=description or name))
    return {"ok": True, "name": name}


def delete(name: str) -> dict:
    """删整个 skill 目录（引用守卫在 routes 层做：sessions_using + force）。"""
    d = skill_dir(name)
    if d is None:
        raise FileNotFoundError(f"skill 不存在: {name}")
    shutil.rmtree(d)
    return {"ok": True, "deleted": name}


# ---------------------------------------------------------------- 安装
def _install_dir(src: Path, src_root: Path, via: str, **extra) -> str:
    """把已解包的 skill 目录装入 skills/（命名优先 frontmatter.name）。"""
    fm = parse_frontmatter((src / "SKILL.md").read_text(errors="replace"))
    name = slugify(fm.get("name") or src.name) or slugify(src.name)
    if not NAME_RE.match(name):
        name = slugify(src.name)
    if not name or not NAME_RE.match(name):
        raise ValueError(f"无法得到合法 skill 名: {src.name}")
    dst = _writable_root() / name
    if dst.exists():
        raise FileExistsError(f"已存在同名 skill: {name}（overwrite=true 覆盖）")
    shutil.copytree(src, dst, symlinks=False)
    # W4：解包后 symlink 清扫（resolve 必须留在包内——防 data 过滤器边角）
    for lk in dst.rglob("*"):
        if lk.is_symlink():
            try:
                lk.resolve().relative_to(dst)
            except ValueError:
                shutil.rmtree(dst, ignore_errors=True)
                raise PermissionError(f"符号链接出界: {lk.relative_to(dst)}")
    total = _dir_size(dst)
    if total > MAX_SKILL_TOTAL_BYTES:
        shutil.rmtree(dst, ignore_errors=True)
        raise PermissionError(f"{name} 解包后 {total // 1048576}MB，超过 100MB 上限")
    # W4 ②：八类静态扫描——红=拒装（净卸载），黄=装+留痕
    from .security import skill_scan
    report = skill_scan.scan_skill(dst)
    if report["level"] == "red":
        shutil.rmtree(dst, ignore_errors=True)
        raise PermissionError(
            f"skill 扫描红线拒装：{[x['rule'] + ':' + x['detail'] for x in report['findings'] if x['level'] == 'red'][:3]}")
    # W4 ③：能力声明（无则全禁草案）
    skill_scan.ensure_capability(dst)
    src_meta = {"via": via, "installed_at": iso(),
                "scan": {"level": report["level"], "n": len(report["findings"])},
                "subdir": str(src.relative_to(src_root)) or "."}
    src_meta.update(extra)
    (dst / SOURCE_FILE).write_text(json.dumps(src_meta, ensure_ascii=False, indent=2))
    return name


def _find_skill_dirs(root: Path, subpath: str) -> list[Path]:
    """定位含 SKILL.md 的目录：精确 subpath 优先，退化按'尾部路径段重合度'打分。

    市场 repo_url 片段约定（#skills~x~y）可能漂移或夹带未知段——取与片段
    尾部连续重合段数最多的候选；片段为空 → 根或全部 skill 目录。
    """
    norm = "/".join(p for p in Path(subpath or "").parts if p not in ("", ".", "/"))
    if norm and (root / norm / "SKILL.md").is_file():
        return [root / norm]
    segs = norm.split("/") if norm else []
    best_k, cands = 0, []
    for md in root.rglob("SKILL.md"):
        parts = list(md.parent.relative_to(root).parts)
        if not segs:                       # 片段为空：仓库里全部 skill 目录
            cands.append(md.parent)
            if len(cands) >= MAX_INSTALL_AT_ONCE:
                break
            continue
        k = 0
        for i in range(1, min(len(parts), len(segs)) + 1):
            if parts[-i:] == segs[-i:]:
                k = i                      # 尾部连续重合段数（取最大 i）
        if k > best_k:
            best_k, cands = k, [md.parent]
        elif k == best_k and k > 0:
            cands.append(md.parent)
        if len(cands) >= MAX_INSTALL_AT_ONCE:
            break
    return sorted(set(cands))


def install_from_zip(data: bytes, overwrite: bool = False) -> dict:
    """zip 上传安装：接受裸 skill 目录或单层包裹（zip 内若干含 SKILL.md 的目录）。"""
    tmp = PATHS["var"] / "tmp_skill_install"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_SKILL_FILES:
                raise PermissionError(f"zip 内 {len(infos)} 个条目超过上限 {MAX_SKILL_FILES}")
            for info in infos:
                n = info.filename
                if n.startswith(("/", "\\")) or ".." in Path(n).parts or ":" in n:
                    raise PermissionError(f"非法 zip 条目: {n}")
            total = sum(i.file_size for i in infos if not i.is_dir())
            if total > MAX_SKILL_TOTAL_BYTES:
                raise PermissionError("zip 解包超 100MB 上限")
            zf.extractall(tmp)
        return _install_tree(tmp, "zip-upload", overwrite=overwrite)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_from_github(repo: str, subpath: str = "", ref: str | None = None,
                        overwrite: bool = False, _download=None) -> dict:
    """GitHub tarball 安装：codeload 流式下载 → 容错定位 → 装入。

    _download 供测试注入（返回 tar.gz bytes），生产走 httpx（延迟导入，
    保持无 httpx 环境下 skills 管理其余功能可用）。
    """
    m = re.match(r"^([\w.-]+)/([\w.-]+)$", repo or "")
    if not m:
        raise ValueError(f"repo 需形如 owner/name: {repo}")
    owner, name = m.group(1), m.group(2)
    if _download is None:
        _download = _download_tarball
    branches = [ref] if ref else ["main", "master"]
    last_err = ""
    for br in branches:
        if not br:
            continue
        try:
            data = _download(owner, name, br)
            break
        except RuntimeError as e:
            last_err = str(e)
    else:
        raise RuntimeError(f"下载失败（试过 {branches}）: {last_err}")
    if len(data) > MAX_TARBALL_BYTES:
        raise PermissionError("tarball 超过 200MB 上限")

    tmp = PATHS["var"] / "tmp_skill_install"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            members = tf.getmembers()
            if len(members) > MAX_SKILL_FILES * 5:
                raise PermissionError(f"tarball 内 {len(members)} 个条目过多")
            for mm in members:
                if mm.name.startswith(("/", "\\")) or ".." in Path(mm.name).parts:
                    raise PermissionError(f"非法 tar 条目: {mm.name}")
            # W4 解包加固：一律 data 过滤器；旧解释器（无 filter 形参）直接拒装
            try:
                tf.extractall(tmp, filter="data")
            except TypeError:
                raise PermissionError(
                    "当前 Python 无 tar 数据过滤器（需 3.10.12+/3.12+）——拒装")
        # tarball 顶层是 repo 名目录
        roots = [p for p in tmp.iterdir() if p.is_dir()]
        root = roots[0] if len(roots) == 1 else tmp
        return _install_tree(root, "github", overwrite=overwrite,
                             repo=f"{owner}/{name}", ref=br, subpath=subpath)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _install_tree(root: Path, via: str, overwrite: bool = False, **extra) -> dict:
    sub = str(extra.get("subpath", ""))
    # skillhub 片段分隔符不统一：#skills-pdf 与 #skills~pdf 同指 skills/pdf。
    # 先按 ~→/ 原样定位，落空再尝试破折号拆分；尾段匹配本身可容错多段。
    cands = [sub]
    if sub and "-" in sub:
        cands.append(sub.replace("-", "/"))
    dirs: list[Path] = []
    for sp in cands:
        dirs = _find_skill_dirs(root, sp)
        if dirs:
            break
    if not dirs:
        raise FileNotFoundError("未找到含 SKILL.md 的目录（检查路径或仓库结构）")
    installed, errors = [], []
    for d in dirs:
        if overwrite:
            fm = parse_frontmatter((d / "SKILL.md").read_text(errors="replace"))
            old = slugify(fm.get("name") or d.name) or d.name
            _old = skill_dir(old)
            if _old is not None and _old.parent == _writable_root():
                shutil.rmtree(_old)
        try:
            installed.append(_install_dir(d, root, via, **extra))
        except FileExistsError as e:
            errors.append(str(e))
    if not installed:
        raise FileExistsError("; ".join(errors) or "安装失败")
    out = {"ok": True, "installed": installed}
    if errors:
        out["skipped"] = errors
    return out


def _download_tarball(owner: str, repo: str, branch: str) -> bytes:
    import httpx
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}"
    chunks, total = [], 0
    with httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=True) as cli:
        with cli.stream("GET", url) as r:
            if r.status_code == 404:
                raise RuntimeError(f"{owner}/{repo}@{branch} 404")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            for chunk in r.iter_bytes(1 << 20):
                total += len(chunk)
                if total > MAX_TARBALL_BYTES:
                    raise PermissionError("tarball 超过 200MB 上限")
                chunks.append(chunk)
    return b"".join(chunks)


_GH_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)"
    r"(?:/(?:tree|blob)/([\w.-]+)((?:/[\w.-]+)+)?)?"
    r"(?:#(.*))?$")


def parse_repo_url(url: str) -> dict:
    """GitHub URL → {repo, subpath, ref}。支持三形态：
    - github.com/owner/repo#skills~a~b     （skillhub repo_url 片段约定，~ 即 /）
    - github.com/owner/repo#/skills/a/b
    - github.com/owner/repo/tree/branch/skills/a/b
    """
    m = _GH_URL.match((url or "").strip())
    if not m:
        raise ValueError(f"不是可安装的 GitHub 仓库地址: {url}")
    owner, repo = m.group(1), m.group(2)
    ref, sub = "", ""
    if m.group(3):                       # tree/branch 形态
        ref = m.group(3)
        sub = (m.group(4) or "").strip("/")
    elif m.group(5):                     # #fragment 形态
        frag = m.group(5)
        if frag.startswith("/"):
            sub = frag.strip("/")
        else:
            sub = "/".join(x for x in frag.split("~") if x and x not in (".",))
    return {"repo": f"{owner}/{repo}", "subpath": sub, "ref": ref}


# ---------------------------------------------------------------- 挂载（会话）
def mount(ws: Path, names: list[str]) -> list[str]:
    """把仓库 skills symlink 进 ws/.claude/skills/（全量重建，幂等）。

    返回实际挂载成功的名单（禁用的不计入）。未知/已禁用 skill 记日志跳过
    （宪法里的 SKILLS_LIST 同步重渲染由调用方负责）。
    """
    target = ws / ".claude" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    mounted = []
    for name in names:
        src = skill_dir(name)
        if src is None:
            log.warning("挂载跳过未知 skill：%s", name)
            continue
        dst = target / name
        if (src / DISABLED_FILE).exists():        # 禁用：不留挂载点（幂等清旧链接）
            log.info("挂载跳过已禁用 skill：%s", name)
            if dst.is_symlink():
                dst.unlink()
            elif dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            continue
        if dst.is_symlink() or dst.exists():
            if dst.is_symlink() and Path(dst.resolve()) == src.resolve():
                mounted.append(name)
                continue
            shutil.rmtree(dst, ignore_errors=True) if dst.is_dir() else dst.unlink(missing_ok=True)
        try:
            dst.symlink_to(src, target_is_directory=True)
            mounted.append(name)
        except OSError as e:
            log.error("symlink skill 失败 %s: %s", name, e)
    return mounted


def set_disabled(name: str, disabled: bool) -> dict:
    """启用/禁用 skill（skills/<name>/.loadn-disabled 标记）。

    禁用：落标记 + 摘掉 active 会话里已挂的 symlink（下一 turn 生效）；
    启用：删标记 + 给 skills_json 引用它的 active 会话补挂回来。两向都
    重渲染宪法，SKILLS_LIST 与实际挂载保持一致。
    """
    d = skill_dir(name)
    if d is None:
        raise FileNotFoundError(f"skill 不存在: {name}")
    mark = d / DISABLED_FILE
    if disabled:
        mark.write_text(iso() + "\n")
    else:
        mark.unlink(missing_ok=True)
    from . import db as db_mod
    from . import workspace as ws_mod  # 延迟导入避循环
    for sid in sessions_using(name):
        with db_mod.conn() as c:
            row = db_mod.get_session(c, sid)
        if row is not None and row["project_id"]:
            continue   # 项目子任务：共享挂载/宪法属项目，子任务无权改写
        link = ws_mod.ws_of(sid) / ".claude" / "skills" / name
        if disabled:
            if link.is_symlink():
                link.unlink()
            elif link.exists():
                shutil.rmtree(link, ignore_errors=True)
        else:
            mount(ws_mod.ws_of(sid), [name])   # 补挂回来
        ws_mod.rerender(sid)               # skills=None：枚举实际挂载重渲染宪法
    return {"ok": True, "name": name, "disabled": disabled}


def sessions_using(name: str) -> list[str]:
    """挂载了该 skill 的 active 会话 id（删除守卫用）。"""
    from . import db as db_mod
    out = []
    with db_mod.conn() as c:
        for r in c.execute("SELECT id, skills_json FROM sessions WHERE status='active'"):
            try:
                if name in (json.loads(r["skills_json"] or "[]")):
                    out.append(r["id"])
            except json.JSONDecodeError:
                pass
    return out
