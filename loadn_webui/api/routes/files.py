"""工作区文件面：读取/打包下载/上传/ingest（大小常量钉在本模块）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from ... import artifacts as art
from ... import workspace as ws_mod
from ...engine import ENGINE

router = APIRouter(prefix="/api")

from ._common import (
    _DL_HEADERS,
    _INGEST_CORS,
    _INGEST_DIRS,
    IMG_UPLOAD_EXTS,
    MAX_INGEST_BYTES,
    _collect_archive_files,
    _get_session_or_404,
)


@router.get("/sessions/{sid}/file")
def get_file(sid: str, path: str, raw: bool = False):
    _get_session_or_404(sid)
    if raw:
        # 供 iframe src 直载（html/pdf 预览）：html 的 CSP sandbox 与 srcDoc 方案同级
        # 防护（禁脚本），但 iframe 拥有自身 URL → 文档内 fragment 锚点可原生滚动
        # （srcDoc 的锚点会按父页面 base URL 解析，点击把 iframe 导航到宿主路由）。
        # pdf 走浏览器原生阅读器：正确 mime + inline；CSP sandbox 会让部分浏览器
        # 拒绝内联渲染而强制下载，故不加。
        p = art.safe_resolve(ws_mod.ws_of(sid), path)
        if p is None:
            raise HTTPException(404, "file not found")
        suffix = p.suffix.lower()
        headers = {"Cache-Control": "no-store"}
        if suffix in (".html", ".htm"):
            return Response(content=p.read_bytes(), media_type="text/html; charset=utf-8",
                            headers={**headers, "Content-Security-Policy": "sandbox"})
        if suffix == ".pdf":
            headers["Content-Disposition"] = "inline"
            return Response(content=p.read_bytes(), media_type="application/pdf", headers=headers)
        return Response(content=p.read_bytes(), media_type="text/plain; charset=utf-8",
                        headers={**headers, "Content-Security-Policy": "sandbox"})
    return art.read_file(ws_mod.ws_of(sid), path)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
@router.get("/sessions/{sid}/archive")
def download_archive(sid: str, path: str = ""):
    """工作区目录打包下载（zip）：?path=reports/ 打包子目录，空 = 整个工作区。
    产物面板的「打包全部」就是 ?path=artifacts/。"""
    _get_session_or_404(sid)
    ws = ws_mod.ws_of(sid)
    p = art.safe_resolve(ws, path) if path else ws
    if p is None:
        raise HTTPException(404, "目录不存在")
    files = [p] if p.is_file() else _collect_archive_files(p)
    if not files:
        raise HTTPException(400, "目录为空（或内容都被过滤：隐藏文件/"
                                 "node_modules 不进包）")
    # zip 顶层目录名：所打目录的最后一段（根 = 会话标题），解压不散一地
    top = p.name if path else (sid or "workspace")
    import io
    import zipfile
    from urllib.parse import quote
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f"{top}/{f.relative_to(p)}")
    buf.seek(0)
    size_mb = buf.getbuffer().nbytes / 1024 / 1024
    # header 只容 latin-1：中文名进 filename*=UTF-8''（RFC 5987），
    # filename= 用 ASCII 剥离回退（老浏览器/下载器兼容位）
    ascii_top = top.encode("latin-1", "ignore").decode().strip("-_ ") or "workspace"
    fname = quote(f"{top}.zip")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_top}.zip"; '
                                   f"filename*=UTF-8''{fname}",
            "Cache-Control": "no-store",
            "X-Archive-Files": str(len(files)),
            "X-Archive-MB": f"{size_mb:.1f}",
            **_DL_HEADERS,
        })
@router.post("/sessions/{sid}/upload")
async def upload(sid: str, file: UploadFile = File(...)):
    _get_session_or_404(sid)
    # 附件落共享输入目录：项目子任务 → 项目根 inputs/（兄弟任务共读）
    dst_dir = ws_mod.inputs_dir_of(sid)
    dst_dir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "upload.bin").name or "upload.bin"
    stem, suffix = name.rsplit(".", 1) if "." in name else (name, "")
    suffix = ("." + suffix) if suffix else ""
    # 同名冲突自动重命名 a.png → a-1.png
    n, dst = 0, dst_dir / name
    while dst.exists():
        n += 1
        dst = dst_dir / f"{stem}-{n}{suffix}"
        name = dst.name
    total = 0
    try:
        with dst.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")
                f.write(chunk)
    except HTTPException:
        dst.unlink(missing_ok=True)
        raise
    kb = round(total / 1024, 1)
    is_image = dst.suffix.lower() in IMG_UPLOAD_EXTS
    ENGINE.publish(sid, "files", {"turn_id": None,
                                  "files": [{"path": f"inputs/{name}", "kb": kb}]})
    return {"ok": True, "path": f"inputs/{name}", "name": name, "kb": kb, "is_image": is_image}
@router.options("/sessions/{sid}/ingest")
def ingest_preflight(sid: str):
    """CORS 预检兜底（FormData POST 是 simple request 通常不触发，防御性保留）。"""
    _get_session_or_404(sid)
    return Response(status_code=204, headers={
        **_INGEST_CORS, "Access-Control-Allow-Methods": "POST",
        "Access-Control-Allow-Headers": "Content-Type"})
@router.post("/sessions/{sid}/ingest")
async def ingest_file(sid: str, file: UploadFile = File(...), to: str = "artifacts/"):
    """文件直通道（P1-3）：沙箱/浏览器容器/页内 JS 直传文件进 workspace。

    场景：证书 PDF 落在 headless 容器里——页内 fetch 直接 POST 到
    /api/sessions/<sid>/ingest?to=artifacts/（token 走 ?token=），彻底替代
    tmpfiles.org 中转与 base64 分块。目标限白名单目录，同名自动重命名。
    成败响应都带 CORS 头：跨源 JS 能读到结果（HTTPException 默认错误响应
    无 CORS 头会被浏览器吞掉，JS 只见 Failed to fetch 分不清被拒还是断网）。
    """
    _get_session_or_404(sid)
    to = to or "artifacts/"

    def _err(code: int, msg: str) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code, headers=_INGEST_CORS)

    if to not in _INGEST_DIRS:
        return _err(400, f"to 只能是 {'/'.join(_INGEST_DIRS)}")
    # inputs/ 是共享目录（项目根）；artifacts/notes/work 是任务级
    dst_dir = (ws_mod.inputs_dir_of(sid) if to == "inputs/"
               else ws_mod.ws_of(sid) / to.rstrip("/"))
    dst_dir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "upload.bin").name or "upload.bin"
    stem, suffix = name.rsplit(".", 1) if "." in name else (name, "")
    suffix = ("." + suffix) if suffix else ""
    n, dst = 0, dst_dir / name
    while dst.exists():
        n += 1
        dst = dst_dir / f"{stem}-{n}{suffix}"
        name = dst.name
    total = 0
    with dst.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_INGEST_BYTES:
                f.close()
                dst.unlink(missing_ok=True)
                return _err(413, f"文件超过 {MAX_INGEST_BYTES // (1024 * 1024)}MB 上限")
            f.write(chunk)
    ENGINE.publish(sid, "files", {"turn_id": None,
                                  "files": [{"path": f"{to}{name}", "kb": round(total / 1024, 1)}]})
    return JSONResponse({"ok": True, "path": f"{to}{name}", "name": name,
                         "kb": round(total / 1024, 1)}, headers=_INGEST_CORS)
