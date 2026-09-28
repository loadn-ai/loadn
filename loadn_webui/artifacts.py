"""产物：workspace/artifacts/ 扫描同步 DB、预览、导出（md→html/docx）。

导出与 doc-export skill 走同一份 exporter 代码——agent 会话内调用与
用户在 UI 点导出按钮行为一致。
"""
from __future__ import annotations

import stat
import time
from pathlib import Path

from . import db as db_mod
from .util import get_logger
from .workspace import ws_of

log = get_logger(__name__)

# 文件分类（承袭前身 webapp.py 的扩展名口径）
TEXT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv",
             ".py", ".js", ".ts", ".sh", ".html", ".css", ".xml", ".log", ".bib", ".rst"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
MAX_FILE_BYTES = 2 * 1024 * 1024

_KIND_BY_EXT = {
    ".md": "md", ".html": "html", ".docx": "docx",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".svg": "image",
    ".csv": "data", ".tsv": "data", ".json": "data", ".parquet": "data",
    ".py": "code", ".js": "code", ".ts": "code", ".sh": "code",
}


def kind_of(path: Path) -> str:
    return _KIND_BY_EXT.get(path.suffix.lower(), "other")


def safe_resolve(ws: Path, rel: str) -> Path | None:
    """防路径穿越（承袭前身 webapp._safe_file）：resolve 后必须在 ws 内。"""
    try:
        p = (ws / rel).resolve()
        p.relative_to(ws.resolve())
    except (ValueError, OSError):
        return None
    return p if p.exists() else None


def ledger_split_check(sid: str) -> list[str]:
    """台账分裂检查（P2-1）：PROGRESS.md / credentials 出现在多个层级 → 报告。

    证书项目实锤：根 PROGRESS.md 停在 #31，会话的 artifacts/PROGRESS.md 到 #40，
    credentials.md 根副本与 certificates/ 副本重复且不一致。台账只允许一个
    权威路径（宪法 §2.7），这里把违反显式暴露给 wd scan。
    """
    ws = ws_of(sid)
    problems: list[str] = []
    if not ws.exists():
        return problems
    root_prog = ws / "PROGRESS.md"
    art_prog = ws / "artifacts" / "PROGRESS.md"
    if root_prog.exists() and art_prog.exists():
        problems.append(f"PROGRESS.md 双轨: 根与 artifacts/ 各一份"
                        f"（{root_prog.stat().st_size}B vs {art_prog.stat().st_size}B）——"
                        "保留一个权威路径，另一个删除")
    cred_paths = [p for p in (ws / "credentials.md",
                              ws / "artifacts" / "credentials.md",
                              ws / "artifacts" / "certificates" / "credentials.md")
                  if p.exists()]
    if len(cred_paths) > 1:
        rel = ", ".join(str(p.relative_to(ws)) for p in cred_paths)
        problems.append(f"credentials 多副本: {rel}——结构化注册表收敛到 "
                        "artifacts/credentials.json 单一真源")
    return problems


def scan_session(sid: str) -> int:
    """扫描 workspace/<sid>/artifacts/ 同步 artifacts 表。"""
    ws = ws_of(sid) / "artifacts"
    if not ws.exists():
        return 0
    n = 0
    with db_mod.conn() as c:
        for p in sorted(ws.rglob("*")):
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(ws.parent))
            title = p.stem.replace("_", " ").replace("-", " ").strip() or p.name
            db_mod.upsert_artifact(
                c, session_id=sid, path=rel, kind=kind_of(p), title=title,
                size=st.st_size, mtime=st.st_mtime, created_by="agent")
            n += 1
    return n


def list_artifacts(sid: str) -> list[dict]:
    with db_mod.conn() as c:
        rows = c.execute(
            "SELECT * FROM artifacts WHERE session_id=? ORDER BY mtime DESC", (sid,)).fetchall()
    return [dict(r) for r in rows]


def get_artifact(aid: int) -> dict | None:
    with db_mod.conn() as c:
        row = c.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone()
    return dict(row) if row else None


def read_file(ws: Path, rel: str) -> dict:
    """分类读取工作区文件（预览用）。返回 {cat, path, ...}，cat: text|img|json|too_large|binary。"""
    p = safe_resolve(ws, rel)
    if p is None:
        return {"error": "not_found"}
    try:
        size = p.stat().st_size
    except OSError:
        return {"error": "not_found"}
    if size > MAX_FILE_BYTES:
        return {"cat": "too_large", "path": rel, "size": size}
    ext = p.suffix.lower()
    if ext in IMG_EXTS:
        import base64
        return {"cat": "img", "path": rel,
                "b64": base64.b64encode(p.read_bytes()).decode(),
                "mime": f"image/{'svg+xml' if ext == '.svg' else ext.lstrip('.')}"}
    if ext not in TEXT_EXTS:
        return {"cat": "binary", "path": rel, "size": size}
    text = p.read_text(errors="replace")
    if ext in (".json", ".csv", ".tsv"):
        return {"cat": "text", "path": rel, "text": text, "sub": ext.lstrip(".")}
    return {"cat": "text", "path": rel, "text": text}


_TREE_TTL = 5.0
_tree_cache: dict[str, tuple[float, list[dict]]] = {}


def tree(sid: str) -> list[dict]:
    """工作区文件树（扁平 relpath 列表，前端自组树）。

    前端每会话轮询本接口，而研究型工作区（chrome 登录态 profile、datasets、
    node_modules）rglob 一次可达分钟级——同步 def 路由跑在 AnyIO 线程池
    （40 线程），轮询不停时池子被 rglob 全占，所有同步路由排队超时，
    整个 API 表现为「卡死」（2026-09-14 py-spy 实测 40 线程全卡在本函数）。
    三层防御：跳过重目录 + TTL 缓存 + 遍历止损。
    """
    hit = _tree_cache.get(sid)
    if hit and time.monotonic() - hit[0] < _TREE_TTL:
        return hit[1]
    ws = ws_of(sid)
    out: list[dict] = []
    if ws.exists():
        walked = 0
        for p in ws.rglob("*"):
            walked += 1
            if walked > 200_000 or len(out) >= 4000:   # 病态工作区止损
                break
            rel = p.relative_to(ws).parts
            # 隐藏目录 + 重目录（node_modules / chrome*）不进树：既是噪音也是慢源
            if any(s.startswith(".") or s == "node_modules" or s.startswith("chrome")
                   for s in rel):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"path": str(p.relative_to(ws)), "dir": stat.S_ISDIR(st.st_mode),
                        "size": None if stat.S_ISDIR(st.st_mode) else st.st_size,
                        "mtime": st.st_mtime})
        out.sort(key=lambda x: x["path"])
        out = out[:2000]
    _tree_cache[sid] = (time.monotonic(), out)
    return out


def export(sid: str, source_path: str, fmt: str) -> dict:
    """md → html/docx，产物落 artifacts/ 并登记。source_path 是工作区相对路径。"""
    ws = ws_of(sid)
    src = safe_resolve(ws, source_path)
    if src is None or src.suffix.lower() != ".md":
        raise ValueError(f"源文件不存在或非 md：{source_path}")
    if fmt not in ("html", "docx"):
        raise ValueError(f"不支持的格式：{fmt}")

    from .exporter import md_to_docx, md_to_html
    dst_dir = ws / "artifacts"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{src.stem}.{fmt}"
    md_text = src.read_text(errors="replace")
    if fmt == "html":
        html = md_to_html.convert(md_text, title=src.stem)
        dst.write_text(html, encoding="utf-8")
    else:
        md_to_docx.convert(md_text, dst)

    st = dst.stat()
    with db_mod.conn() as c:
        db_mod.upsert_artifact(
            c, session_id=sid, path=str(dst.relative_to(ws)), kind=fmt,
            title=dst.stem, size=st.st_size, mtime=st.st_mtime, created_by="export")
        row = c.execute("SELECT id FROM artifacts WHERE session_id=? AND path=?",
                        (sid, str(dst.relative_to(ws)))).fetchone()
    return {"artifact_id": row["id"] if row else None,
            "path": str(dst.relative_to(ws)), "format": fmt}
