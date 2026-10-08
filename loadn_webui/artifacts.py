"""产物：workspace/artifacts/ 扫描同步 DB、预览、导出（md→html/docx）。

导出与 doc-export skill 走同一份 exporter 代码——agent 会话内调用与
用户在 UI 点导出按钮行为一致。
"""
from __future__ import annotations

import stat
import time
from pathlib import Path

from . import db as db_mod
from .util import get_logger, iso
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
    """扫描 workspace/<sid>/artifacts/ 同步 artifacts 表。

    已回填中文标题/摘要的行不被文件名标题覆盖（titlegen 结果是人工校准过的
    呈现面；文件名仍是 path 真源）。
    """
    ws = ws_of(sid) / "artifacts"
    if not ws.exists():
        return 0
    n = 0
    with db_mod.conn() as c:
        done = {r["path"] for r in c.execute(
            "SELECT path FROM artifacts WHERE session_id=? AND summary IS NOT NULL",
            (sid,))}
        for p in sorted(ws.rglob("*")):
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            from loadn.util import sanitize_text  # 六轮修 B5：rglob 出的
            rel = sanitize_text(str(p.relative_to(ws.parent)))  # surrogateescape
            fields: dict = {"session_id": sid, "path": rel,  # 文件名不清洗会
                            "kind": sanitize_text(kind_of(p)),  # 炸 sqlite bind
                            "size": st.st_size, "mtime": st.st_mtime}
            if rel not in done:
                fields["title"] = sanitize_text(
                    p.stem.replace("_", " ").replace("-", " ").strip()
                    or p.name)
                fields["created_by"] = "agent"
            db_mod.upsert_artifact(c, **fields)
            n += 1
    return n


# ---------------- 中文标题/摘要回填（titlegen，产品语言=简体中文） ----------------

_SUMMARY_INFLIGHT: set[str] = set()


async def ensure_summaries(sid: str, limit: int = 8) -> int:
    """缺摘要的文本类产物批量生成「中文短标题 + 一行摘要」。

    - 一次 LLM 调用出全批（doubao mini 级成本）；只处理前 limit 个新/变更项
    - titlegen 未启用/调用失败：静默降级（filename 标题兜底），不阻塞主流程
    - 同会话并发去重（_SUMMARY_INFLIGHT）；多语言化时把 prompt 语言抽配置
    """
    from .config import CONFIG
    if not (CONFIG.titlegen.enabled and CONFIG.titlegen.api_key):
        return 0
    if sid in _SUMMARY_INFLIGHT:
        return 0
    _SUMMARY_INFLIGHT.add(sid)
    try:
        with db_mod.conn() as c:
            rows = c.execute(
                "SELECT id, path, kind, title FROM artifacts "
                "WHERE session_id=? AND summary IS NULL AND kind IN "
                "('md','html','code','data','other') ORDER BY mtime DESC LIMIT ?",
                (sid, limit)).fetchall()
            rows = [dict(r) for r in rows]
        if not rows:
            return 0
        ws = ws_of(sid)
        items: list[str] = []
        for i, r in enumerate(rows):
            p = safe_resolve(ws, r["path"])
            head = ""
            if p is not None and p.stat().st_size <= MAX_FILE_BYTES \
                    and p.suffix.lower() in TEXT_EXTS:
                head = p.read_text(errors="replace")[:1200]
            items.append(f"[{i}] 文件: {r['path']}\n内容头: {head[:1000] or '（无文本）'}")
        from .integrations.titlegen import _chat
        reply = await _chat(
            "你是产物索引器。对每个文件给出：简体中文短标题（≤12字，概括内容而非"
            "复述文件名）与一行摘要（≤40字，说明这是什么、给谁用）。"
            '只输出 JSON 数组：[{"i":0,"t":"标题","s":"摘要"}]，不要任何其他文字。',
            "\n\n".join(items), max_tokens=1400)
        import json as _json
        import re as _re
        m = _re.search(r"\[.*\]", reply, _re.S)
        if not m:
            # 模型输出格式漂移（无 JSON 数组）：记警觉留痕，行保持 NULL 由
            # 下次触发（turn 收尾/会话打开）重试——单 turn 会话不再一锤定音
            log.warning("产物摘要回填格式不中 sid=%s 待重试（reply 头 %r）",
                        sid, reply[:80])
            return 0
        got = {int(d["i"]): d for d in _json.loads(m.group(0))
               if isinstance(d, dict) and "i" in d}
        n = 0
        with db_mod.conn() as c:
            for i, r in enumerate(rows):
                d = got.get(i)
                if not d:
                    continue
                title = str(d.get("t") or "").strip()[:24]
                summary = str(d.get("s") or "").strip()[:80]
                if not title and not summary:
                    continue
                c.execute(
                    "UPDATE artifacts SET title=COALESCE(NULLIF(?, ''), title), "
                    "summary=?, updated_at=? WHERE id=?",
                    (title or None, summary or "（见标题）", iso(), r["id"]))
                n += 1
        if n:
            log.info("产物摘要回填 sid=%s %d/%d 条", sid, n, len(rows))
        elif rows:
            log.warning("产物摘要回填 0 命中 sid=%s（%d 项待重试）", sid, len(rows))
        return n
    except Exception:  # noqa: BLE001 —— 索引面非关键路径
        log.warning("产物摘要回填失败 sid=%s（静默降级）", sid, exc_info=True)
        return 0
    finally:
        _SUMMARY_INFLIGHT.discard(sid)


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
