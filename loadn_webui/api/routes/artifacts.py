"""产物面：列表/分享/导出/下载/预览。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from ... import artifacts as art
from ... import workspace as ws_mod

router = APIRouter(prefix="/api")

from ._common import _DL_HEADERS, _get_session_or_404


@router.get("/sessions/{sid}/artifacts")
def get_artifacts(sid: str):
    _get_session_or_404(sid)
    art.scan_session(sid)
    return {"artifacts": art.list_artifacts(sid)}
@router.post("/sessions/{sid}/share")
def post_share(sid: str, body: dict):
    """铸造产物分享链接（幂等）：{path: artifacts/xxx} → {url, token}。"""
    _get_session_or_404(sid)
    from ...integrations import share as share_mod
    try:
        return share_mod.mint(sid, str(body.get("path") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
@router.post("/sessions/{sid}/export")
def post_export(sid: str, body: dict):
    _get_session_or_404(sid)
    try:
        out = art.export(sid, body.get("source_path", ""), body.get("format", "html"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return out
@router.get("/artifacts/{aid}/download")
def download_artifact(aid: int):
    a = art.get_artifact(aid)
    if a is None:
        raise HTTPException(404, "artifact not found")
    p = art.safe_resolve(ws_mod.ws_of(a["session_id"]), a["path"])
    if p is None:
        raise HTTPException(404, "file missing")
    return FileResponse(p, filename=p.name,
                        headers={"Cache-Control": "no-store", **_DL_HEADERS})
@router.get("/artifacts/{aid}/preview")
def preview_artifact(aid: int):
    a = art.get_artifact(aid)
    if a is None:
        raise HTTPException(404, "artifact not found")
    p = art.safe_resolve(ws_mod.ws_of(a["session_id"]), a["path"])
    if p is None:
        raise HTTPException(404, "file missing")
    if a["kind"] == "md":
        from ...exporter import md_to_html
        html = md_to_html.convert(p.read_text(errors="replace"), title=a["title"])
        return Response(content=html, media_type="text/html", headers=_DL_HEADERS)
    data = art.read_file(ws_mod.ws_of(a["session_id"]), a["path"])
    if data.get("cat") == "img":
        import base64
        return Response(content=base64.b64decode(data["b64"]), media_type=data["mime"],
                        headers=_DL_HEADERS)
    return Response(content=p.read_text(errors="replace"), media_type="text/plain",
                    headers=_DL_HEADERS)
