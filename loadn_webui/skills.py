"""skill 发现、按会话挂载、管理面（CRUD / 编辑 / 安装 / 导出）。

平台 skills 只留仓库 skills/，创建会话时 symlink 到工作区 .claude/skills/
——零拷贝、版本最新、真选择性（P0 已实测无头会话识别 symlink 目录）。
追加/移除后下一 turn 生效（CLI 会话启动时枚举）；编辑文件经 symlink 实时可见。

安装三来源（P1，agentskills.io 兼容）：GitHub repo/tree URL 或 owner/repo
简写（tarball，codeload 直连不走 git）、任意 https .zip/.tar.gz 归档 URL、
zip 上传。前两类远程来源装完即 pin：盖 source 戳 + 写引擎供应链锁
（$LOADN_HOME/skills.lock.json），out-of-band 篡改 → 引擎拒索引；上传视同
用户自备不 pin。导出 export_zip 产出 agentskills.io 兼容目录 zip。
skillhub 等市场只做发现（见 skillhub.py），repo_url 片段 #a~b~c 约定在
parse_repo_url 解析。第三方 skill 代码会在 bypassPermissions 下被 agent
执行——装前即信任作者。
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

from loadn.skilllock import remove_lock_entry, update_lock_entry
from loadn.util import parse_frontmatter as parse_fm_full

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
    """SKILL.md 头部 frontmatter（name/description）。

    三轮修（backlog 清）：复用引擎侧 loadn.util.parse_frontmatter——原
    自研 split 版与引擎语义相反（首键胜 vs 后键覆盖；--- 不锚定行首），
    重复键时管理页显示 name=a、引擎注册 name=b，删除/引用按名错位。"""
    from loadn.util import parse_frontmatter as _engine_parse
    meta, _ = _engine_parse(md)
    return {"name": str(meta.get("name") or ""),
            "description": str(meta.get("description") or "")}


_FM_SPLIT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _fm_upsert(text: str, fields: dict[str, str | None]) -> str:
    """frontmatter 字段 upsert（值 None=删该键）；无 frontmatter 则新建。

    其余行原样保留（agentskills.io 的 allowed-tools/metadata 等字段不动）；
    只替换顶层标量键——被替换键的缩进续行（列表项）会随键一起丢，本仓
    upsert 的键（source/name/description）均为标量，不触及该形态。
    """
    m = _FM_SPLIT.match(text)
    if m:
        lines = m.group(1).splitlines()
        rest = text[m.end():]
    else:
        lines, rest = [], text
    drop = set(fields)
    out = [ln for ln in lines
           if not (ln[:1] not in (" ", "\t") and ":" in ln
                   and ln.partition(":")[0].strip() in drop)]
    out.extend(f"{k}: {v}" for k, v in fields.items() if v is not None)
    if not out:
        return rest
    return "---\n" + "\n".join(out) + "\n---\n" + rest


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
                # 七轮修（设定审计#6）：供应链锁状态露出——带 source 的
                # 外部 skill 受锁保护；锁校验不过会被引擎拒索引（webui
                # 仍显示=「挂着但 agent 说没有」的不可见故障）。lock_ok=
                # 锁条目存在且哈希一致
                from loadn import skilllock
                locks = skilllock.load_locks()
                ent = locks.get(p.name)
                item["pinned"] = ent is not None
                item["lock_ok"] = bool(
                    ent and skilllock.skill_hash(md) == ent.get("computedHash"))
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
        # B6 打通：外部 skill 经管理面编辑 = 有意变更 → 同步刷新锁
        # （否则编辑保存即 rug-pull 误报，下次发现直接拒索引）
        meta, _ = parse_fm_full(content)
        pin = str(meta.get("source") or "").strip()
        if pin:
            update_lock_entry(name, p, pin)
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
    # 三轮修（backlog 清）：description 单行化——多行值进 frontmatter 后：
    # 引擎解析只取首行=丢数据；注入 source: 行=触发供应链锁 fail-closed
    # =skill 被引擎拒索引（自毁）。name 已有 NAME_RE 白名单无需再洗。
    description = " ".join((description or "").split()) or name
    d = _writable_root() / name
    if skill_dir(name) is not None or d.exists():
        raise FileExistsError(f"已存在: {name}")
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(name=name, description=description))
    return {"ok": True, "name": name}


def delete(name: str) -> dict:
    """删整个 skill 目录（引用守卫在 routes 层做：sessions_using + force）。"""
    d = skill_dir(name)
    if d is None:
        raise FileNotFoundError(f"skill 不存在: {name}")
    # 外部来源 skill 连锁条目一起清（不留幽灵；用户层没有该条则静默）
    try:
        meta, _ = parse_fm_full((d / "SKILL.md").read_text(errors="replace"))
    except OSError:
        meta = {}
    if str(meta.get("source") or "").strip():
        remove_lock_entry(name)
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
    # P1 锁打通：远程来源装完即 pin——盖 source 戳 + 写引擎供应链锁
    # （$LOADN_HOME/skills.lock.json）。此后 out-of-band 篡改 SKILL.md
    # → 引擎 discover 拒索引（fail-closed）。写不进锁就拒装：戳已盖而锁
    # 缺失的 skill 会被引擎直接拒索引，不如装的时候失败得响。
    pin = None
    if via == "github" and extra.get("repo"):
        pin = f"github:{extra['repo']}" + (f"@{extra['ref']}" if extra.get("ref") else "")
    elif via == "url" and extra.get("url"):
        pin = f"url:{extra['url']}"
    if pin:
        md = dst / "SKILL.md"
        try:
            md.write_text(_fm_upsert(md.read_text(encoding="utf-8"), {"source": pin}),
                          encoding="utf-8")
            update_lock_entry(name, md, pin)
        except (OSError, UnicodeDecodeError) as e:
            shutil.rmtree(dst, ignore_errors=True)
            raise PermissionError(f"供应链锁写入失败，拒装（{e}）") from None
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


def _extract_zip(data: bytes, tmp: Path) -> None:
    """zip 解包加固（条目数/体积/路径穿越，与上传安装同一套门）。"""
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


def _extract_tarball(data: bytes, tmp: Path) -> None:
    """tar.gz 解包加固（W4：一律 data 过滤器；旧解释器直接拒装）。"""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        members = tf.getmembers()
        if len(members) > MAX_SKILL_FILES * 5:
            raise PermissionError(f"tarball 内 {len(members)} 个条目过多")
        for mm in members:
            if mm.name.startswith(("/", "\\")) or ".." in Path(mm.name).parts:
                raise PermissionError(f"非法 tar 条目: {mm.name}")
        try:
            tf.extractall(tmp, filter="data")
        except TypeError:
            raise PermissionError(
                "当前 Python 无 tar 数据过滤器（需 3.10.12+/3.12+）——拒装")


def _extract_to_tmp(data: bytes) -> Path:
    """下载/上传的归档统一落临时目录（调用方 finally 清理）。"""
    tmp = PATHS["var"] / "tmp_skill_install"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def install_from_zip(data: bytes, overwrite: bool = False) -> dict:
    """zip 上传安装：接受裸 skill 目录或单层包裹（zip 内若干含 SKILL.md 的目录）。

    上传内容视作用户自备（同 create()），不盖 source 戳、不进供应链锁。
    """
    tmp = _extract_to_tmp(data)
    try:
        _extract_zip(data, tmp)
        return _install_tree(tmp, "zip-upload", overwrite=overwrite)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_from_github(repo: str, subpath: str = "", ref: str | None = None,
                        overwrite: bool = False, _download=None) -> dict:
    """GitHub tarball 安装：codeload 流式下载 → 容错定位 → 装入。

    _download 供测试注入（返回 tar.gz bytes），生产走 httpx（延迟导入，
    保持无 httpx 环境下 skills 管理其余功能可用）。远程来源装完即 pin
    （source 戳 + 引擎锁，见 _install_dir）。
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

    tmp = _extract_to_tmp(data)
    try:
        _extract_tarball(data, tmp)
        # tarball 顶层是 repo 名目录
        roots = [p for p in tmp.iterdir() if p.is_dir()]
        root = roots[0] if len(roots) == 1 else tmp
        return _install_tree(root, "github", overwrite=overwrite,
                             repo=f"{owner}/{name}", ref=br, subpath=subpath)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_from_url(url: str, overwrite: bool = False, _download=None) -> dict:
    """任意 https 归档 URL 安装（agentskills.io 生态第三来源）。

    仅收 https + .zip/.tar.gz/.tgz（fail-closed：明文与非归档直接拒）；
    解包/扫描/装后 pin 与 GitHub 来源同一条链。_download 供测试注入。
    """
    u = (url or "").strip()
    if not u.lower().startswith("https://"):
        raise ValueError("URL 安装仅支持 https://（fail-closed）")
    low = u.lower().split("?", 1)[0]
    if low.endswith(".zip"):
        kind = "zip"
    elif low.endswith((".tar.gz", ".tgz")):
        kind = "tar"
    else:
        raise ValueError("URL 需以 .zip / .tar.gz / .tgz 结尾（技能包归档地址）")
    if _download is None:
        _download = _download_url
    data = _download(u)
    if len(data) > MAX_TARBALL_BYTES:
        raise PermissionError("下载超过 200MB 上限")
    tmp = _extract_to_tmp(data)
    try:
        if kind == "zip":
            _extract_zip(data, tmp)
            root = tmp
        else:
            _extract_tarball(data, tmp)
            roots = [p for p in tmp.iterdir() if p.is_dir()]
            root = roots[0] if len(roots) == 1 else tmp
        return _install_tree(root, "url", overwrite=overwrite, url=u)
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


def _download_url(url: str) -> bytes:
    """任意 https 归档下载（流式 + 体积上限，与 codeload 同款门）。"""
    import httpx
    chunks, total = [], 0
    with httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=True) as cli:
        with cli.stream("GET", url) as r:
            if r.status_code == 404:
                raise RuntimeError(f"{url} 404")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            for chunk in r.iter_bytes(1 << 20):
                total += len(chunk)
                if total > MAX_TARBALL_BYTES:
                    raise PermissionError("下载超过 200MB 上限")
                chunks.append(chunk)
    return b"".join(chunks)


_GH_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)"
    r"(?:/(?:tree|blob)/([\w.-]+)((?:/[\w.-]+)+)?)?"
    r"(?:#(.*))?$")


def parse_repo_url(url: str) -> dict:
    """GitHub 地址 → {repo, subpath, ref}。支持四形态：
    - github.com/owner/repo#skills~a~b     （skillhub repo_url 片段约定，~ 即 /）
    - github.com/owner/repo#/skills/a/b
    - github.com/owner/repo/tree/branch/skills/a/b
    - owner/repo[/sub/path]                （agentskills.io 生态常用简写）
    """
    s = (url or "").strip()
    m = _GH_URL.match(s)
    if not m:
        # owner/repo 简写（无协议头；带协议的第三方站地址仍拒绝）
        if "://" not in s and "/" in s:
            parts = [p for p in s.split("/") if p]
            if len(parts) >= 2 and all(re.match(r"^[\w.-]+$", p) for p in parts):
                return {"repo": f"{parts[0]}/{parts[1]}",
                        "subpath": "/".join(parts[2:]), "ref": ""}
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


def export_zip(name: str) -> bytes:
    """导出为 agentskills.io 兼容 zip（顶层 <name>/ 目录形状，可直接再装）。

    frontmatter 规范化：补 name/description（agentskills.io 标准必填）、
    剥 source 戳（溯源属本机安装记录，新环境装时重新 pin）；
    .loadn-* 内部元数据与符号链接不入包。
    """
    d = skill_dir(name)
    if d is None:
        raise FileNotFoundError(f"skill 不存在: {name}")
    if _dir_size(d) > MAX_SKILL_TOTAL_BYTES:
        raise PermissionError("skill 体积超过导出上限")
    fm = parse_frontmatter((d / "SKILL.md").read_text(errors="replace"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(d.rglob("*")):
            rel = f.relative_to(d)
            if any(part.startswith(".") for part in rel.parts):
                continue          # .loadn-source / .loadn-disabled / .loadn-capabilities
            if f.is_symlink():
                continue          # 符号链接不可移植，不入包
            if f.name == "SKILL.md" and len(rel.parts) == 1:
                zf.writestr(f"{name}/SKILL.md", _fm_upsert(
                    f.read_text(errors="replace"),
                    {"name": fm.get("name") or name,
                     "description": fm.get("description") or name,
                     "source": None}))
            elif f.is_file():
                zf.write(f, f"{name}/{rel}")
    return buf.getvalue()


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
