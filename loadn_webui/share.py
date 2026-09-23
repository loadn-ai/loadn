"""产物分享：DB 令牌铸造 + 主服务上的公开只读路由 /share/<token>。

架构（2026-09-10，同日合并进主服务）：VPS 反代 your-domain.com/share 到本机
nginx :80（路由器 30080→80 转发），nginx 再把 /share 反代到主服务 8792。
auth 中间件只护 /api 前缀，/share 是免认证公开路径——URL 即凭证：

- token = 20 hex（80 bit）随机，不可猜；路径不进 URL（不泄露会话名）；
- 铸造侧（POST /api/sessions/{sid}/share）限 artifacts/ 下文件，
  同一 (sid, path) 幂等复用同一 token；删除 shares 行即吊销；
- 渲染与站内预览同构：md → exporter.md_to_html；html 原样（加 CSP sandbox，
  产物是不可信内容，别让脚本跑在分享域源上）；图片 inline；其余按下载。
"""
from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

from . import artifacts as art
from . import workspace as ws_mod
from . import db as db_mod
from .config import CONFIG, PATHS

TOKEN_LEN = 20          # hex 字符数（80 bit）

# 公开端点一律带上：产物是不可信内容
COMMON_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}

share_router = APIRouter()


def mint(sid: str, path: str) -> dict:
    """铸造（幂等）分享链接。path 必须是 artifacts/ 下的现有文件。

    返回 {url, token}；未配置 share.base_url 时抛 ValueError。
    """
    base = (CONFIG.share.base_url or "").rstrip("/")
    if not base:
        raise ValueError("share 未配置：config.yaml 的 share.base_url 为空")
    if not path.startswith("artifacts/"):
        raise ValueError("只能分享 artifacts/ 下的产物")
    p = art.safe_resolve(ws_mod.ws_of(sid), path)
    if p is None:
        raise ValueError("文件不存在")
    with db_mod.conn() as c:
        row = db_mod.find_share(c, sid, path)
        token = row["token"] if row else secrets.token_hex(TOKEN_LEN // 2)
        if not row:
            db_mod.create_share(c, sid, path, token)
    return {"token": token, "url": f"{base}/{token}/{quote(p.name)}"}


def _serve(token: str) -> Response:
    with db_mod.conn() as c:
        row = db_mod.get_share(c, token)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404,
                            headers=COMMON_HEADERS)
    p = art.safe_resolve(ws_mod.ws_of(row["session_id"]), row["path"])
    if p is None:
        return JSONResponse({"error": "gone"}, status_code=404, headers=COMMON_HEADERS)
    disp = f"inline; filename*=UTF-8''{quote(p.name)}"
    headers = {**COMMON_HEADERS, "Content-Disposition": disp}
    kind = art.kind_of(p)
    if kind == "md":
        from .exporter import md_to_html
        html = md_to_html.convert(p.read_text(errors="replace"), title=p.stem)
        return Response(content=html, media_type="text/html; charset=utf-8",
                        headers={**headers, "Content-Security-Policy": "sandbox"})
    if kind == "html":
        # sandbox：允许渲染样式，禁脚本/表单——防产物里的脚本跑在分享域源上
        return Response(content=p.read_bytes(), media_type="text/html; charset=utf-8",
                        headers={**headers, "Content-Security-Policy": "sandbox"})
    if kind == "image":
        import mimetypes
        media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return FileResponse(p, media_type=media, headers=headers)
    # docx / data / code 等：按下载交付
    return FileResponse(p, filename=p.name, headers=COMMON_HEADERS)


# GET+HEAD 都要通：微信/Slack 等链接预览器先发 HEAD
@share_router.api_route("/share/{token}", methods=["GET", "HEAD"])
def share_by_token(token: str):
    return _serve(token)


@share_router.api_route("/share/{token}/{fname:path}", methods=["GET", "HEAD"])
def share_with_name(token: str, fname: str):
    """带文件名的美化 URL（文件名仅装饰，实际文件由 token 决定）。"""
    return _serve(token)
