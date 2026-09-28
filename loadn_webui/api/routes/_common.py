"""路由域共享件：异常映射、分区互斥等横切 helper（无端点）。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from ... import db as db_mod
from ... import profile as profile_mod

_ERR_MAP = ((FileNotFoundError, 404), (KeyError, 404), (FileExistsError, 409),
            (ValueError, 400), (PermissionError, 400), (RuntimeError, 502))
def _http_err(e: Exception) -> HTTPException:
    for ty, code in _ERR_MAP:
        if isinstance(e, ty):
            return HTTPException(code, str(e))
    return HTTPException(500, str(e))
_PARTITION_KEYS = ("pinned", "starred", "category_id")
def _apply_partition_mutex(body: dict, updates: dict) -> None:
    """分区互斥：本次落位某个桶时，未显式给出的另两位清零（置顶/收藏/分类
    三桶独占，归档走 status 不在此列）。纯标记请求不 touch updated_at。"""
    if any(k in body for k in _PARTITION_KEYS):
        for k in _PARTITION_KEYS:
            if k not in updates:
                updates[k] = None if k == "category_id" else 0
def _get_session_or_404(sid: str) -> dict:
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
        if sess is None:
            raise HTTPException(404, f"session not found: {sid}")
        d = db_mod.to_dict(sess)
        d["usage"] = db_mod.usage_totals(c, sid)
        return d
_FOLD_KEEP = 50          # 每条消息最多保留的块数（留尾部=最近的过程）
_FOLD_STR = 400          # 单块内字符串截断（input 脚本/result 输出全文太长）
def _trunc_obj(v, limit: int):
    if isinstance(v, str):
        return v if len(v) <= limit else v[:limit] + "…"
    if isinstance(v, dict):
        return {k: _trunc_obj(x, limit) for k, x in v.items()}
    if isinstance(v, list):
        return [_trunc_obj(x, limit) for x in v[:20]] + (["…"] if len(v) > 20 else [])
    return v
def _fold_blocks(bj: str | None, keep: int = _FOLD_KEEP) -> str | None:
    if not bj:
        return bj
    try:
        blocks = json.loads(bj)
    except (TypeError, json.JSONDecodeError):
        return bj
    # 正文段（text）全保留——消息主体，此前也以整段 content 全量下发（同量级）；
    # thinking/tool 过程块只留尾部 keep 条（16.8MB 详情响应的教训）
    proc_idx = [i for i, b in enumerate(blocks)
                if not (isinstance(b, dict) and b.get("type") == "text")]
    if len(proc_idx) > keep:
        dropped = len(proc_idx) - keep
        keep_set = set(proc_idx[-keep:])
        out: list = []
        marked = False
        for i, b in enumerate(blocks):
            is_text = isinstance(b, dict) and b.get("type") == "text"
            if i not in keep_set and not is_text:
                if not marked:
                    out.append({"type": "tool", "name": "⋯",
                                "brief": f"（已折叠前 {dropped} 条过程细节，正文完整）"})
                    marked = True
                continue
            out.append(b)
        blocks = out
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") == "text":
            continue   # 正文段不做 400 字截断
        for k in ("input", "result", "text"):
            if k in b:
                b[k] = _trunc_obj(b[k], _FOLD_STR)
    return json.dumps(blocks, ensure_ascii=False)
_RES_SERVICE_FIELDS = (
    ("ocr_url", "OCR 服务", "识别"),
    ("sandbox_url", "AIO 沙箱", "浏览器/命令行/文件"),
    ("cdp_url", "沙箱 Chrome CDP", "浏览器驱动"),
    ("proxy", "出网代理", "clash 等"),
    ("sms_url", "短信查询服务", "真机验证码"),
    ("sms_phone", "短信手机号", ""),
    ("mail_imap", "主邮箱 IMAP", ""),
    ("mail_smtp", "主邮箱 SMTP", ""),
    ("mail_user", "主邮箱账号", ""),
    ("vlm_api_base", "视觉模型 API", ""),
    ("vlm_model", "视觉模型", ""),
    ("zhipu_engine", "智谱搜索档位", ""),
    ("adb_addr", "Android 真机", ""),
    ("textr_email", "Textr 账号", ""),
)
def _job_fields(body: dict, sid: str | None) -> dict:
    """创建 job 的字段规范化。触发三选一：cron > at/in；kind 两种动作；
    every/max_fires 防跑飞钳制（递归默认上限 20）。"""
    from ...cron import next_run_iso, parse_cron
    from ...scheduler import parse_when
    kind = str(body.get("kind") or "").strip()
    if sid is not None:
        kind = kind or "message"      # 会话内端点恒 message
    elif not kind:
        if body.get("session_id"):
            kind = "message"          # 全局端点带 session_id = 显式指认目标会话
        else:
            # 两者都没有：静默默认成 new_session 会让人误建错任务
            raise HTTPException(400, "需要 kind（message 投递到现有会话 / "
                                     "new_session 到点新建）或 session_id")
    if kind not in ("message", "new_session"):
        raise HTTPException(400, "kind 只能是 message|new_session")
    fields: dict = {"kind": kind, "session_id": None}
    if kind == "message":
        target = sid if sid is not None else str(body.get("session_id") or "").strip()
        if not target:
            raise HTTPException(400, "message 任务需要 session_id（或走 /sessions/{sid}/schedules）")
        _get_session_or_404(target)
        fields["session_id"] = target
    else:
        # new_session：到点新建会话；title/profile/engine 可选（profile 校验
        # 用注册表白名单，engine 校验与聊天框内核切换同源）
        fields["title"] = str(body.get("title") or "").strip()[:80] or None
        prof = str(body.get("profile") or "").strip()
        if prof and prof != "auto":
            if prof not in profile_mod.load_registry():
                raise HTTPException(400, f"未知 profile：{prof}")
            fields["profile"] = prof
        eng = str(body.get("engine") or "").strip()
        if eng:
            from ... import engines as engines_mod
            if eng not in engines_mod.ENGINES:
                raise HTTPException(400, f"未知引擎：{eng}（可用：{sorted(engines_mod.ENGINES)}）")
            fields["engine"] = eng
    if "label" in body:
        fields["label"] = str(body["label"] or "").strip()[:80] or None
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt 不能为空")
    fields["prompt"] = prompt

    # 触发三选一：cron（挂钟对齐，如每天 20:00）> at/in（一次或 every 递归）
    cron = str(body.get("cron") or "").strip()
    at, in_ = str(body.get("at") or "").strip(), str(body.get("in") or "").strip()
    if cron:
        try:
            parse_cron(cron)
        except ValueError as e:
            raise HTTPException(400, str(e))
        fields["cron"] = cron
        fields["every_s"] = None
        fields["due_at"] = next_run_iso(cron)
        max_fires = int(body.get("max_fires") or 20)
    elif at or in_:
        fields["cron"] = None
        try:
            fields["due_at"] = parse_when(at=at, in_=in_)
        except ValueError as e:
            raise HTTPException(400, str(e))
        every_s = body.get("every_s")
        if every_s is not None:
            every_s = int(every_s)
            if not 60 <= every_s <= 86400 * 30:
                raise HTTPException(400, "every_s 需在 60s-30天（递归间隔）")
            fields["every_s"] = every_s
            # 递归 job 必须有触发上限（默认 20），防无人值守跑飞
            max_fires = int(body.get("max_fires") or 20)
        else:
            fields["every_s"] = None
            max_fires = int(body.get("max_fires") or 1)
    else:
        raise HTTPException(400, "需要 cron / at / in 之一"
                            "（如 cron='0 20 * * *' / at=2026-09-15 20:30 / in=90m）")
    if not 1 <= max_fires <= 100000:
        raise HTTPException(400, "max_fires 需在 1-100000")
    fields["max_fires"] = max_fires
    return fields
def _validate_attachments(raw) -> list[dict]:
    """附件列表规范化：必须是 inputs/ 内的相对路径，≤20 条，字段白名单。"""
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > 20:
        raise HTTPException(400, "attachments 需为列表（≤20 条）")
    out = []
    for a in raw:
        if not isinstance(a, dict):
            raise HTTPException(400, "attachments 条目需为对象")
        path = str(a.get("path") or "")
        if not path.startswith("inputs/") or ".." in path or path.startswith("/"):
            raise HTTPException(400, f"附件路径非法: {path}")
        out.append({
            "type": "attachment", "path": path,
            "name": str(a.get("name") or Path(path).name)[:200],
            "kb": a.get("kb") if isinstance(a.get("kb"), (int, float)) else None,
            "is_image": bool(a.get("is_image")),
        })
    return out
IMG_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_ARCHIVE_MAX_FILES = 5000
_DL_HEADERS = {"Content-Security-Policy": "default-src 'none'; img-src data:; "
                                          "style-src 'unsafe-inline'",
               "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "no-referrer"}
_ARCHIVE_MAX_BYTES = 2 * 1024 * 1024 * 1024   # 打包下载总大小闸（测试打桩点）


def _collect_archive_files(root: Path) -> list[Path]:
    """root 下的待打包文件（宽进严出）：跳隐藏项/node_modules/chrome*
    （平台控制文件 .fake/.steer.jsonl 与依赖树/登录态不该出工作区）、
    跳符号链接（防环）。超容量闸抛 HTTPException。"""
    out: list[Path] = []
    total = 0
    walked = 0
    for p in root.rglob("*"):
        walked += 1
        if walked > 200_000:      # 病态大目录止损（chrome profile 可达数十万项）
            break
        rel = p.relative_to(root).parts
        if any(s.startswith(".") or s == "node_modules" or s.startswith("chrome")
               for s in rel):
            continue
        try:
            if p.is_symlink() or not p.is_file():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        out.append(p)
        total += size
        if len(out) > _ARCHIVE_MAX_FILES:
            raise HTTPException(400, f"文件数超过 {_ARCHIVE_MAX_FILES}，"
                                     "请分目录打包")
        if total > _ARCHIVE_MAX_BYTES:
            raise HTTPException(400, "总大小超过 2GB，请分目录打包")
    return out
MAX_INGEST_BYTES = 200 * 1024 * 1024
_INGEST_DIRS = ("artifacts/", "notes/", "work/", "inputs/")
_INGEST_CORS = {"Access-Control-Allow-Origin": "*"}
