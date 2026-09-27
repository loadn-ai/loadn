"""profiles / skills / skillhub 市场面（技能 CRUD、编辑、安装、翻译）。"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ... import profile as profile_mod
from ... import skill_zh, skillhub
from ... import skills as skills_mod

router = APIRouter(prefix="/api")

from ._common import _http_err


@router.get("/profiles")
def list_profiles():
    reg = profile_mod.load_registry()
    return {"profiles": [{"name": p.name, "description": p.description, "skills": p.skills,
                          "effort": p.effort, "timeout_s": p.timeout_s} for p in reg.values()]}
@router.get("/skills")
def list_skills():
    return {"skills": skills_mod.available()}
@router.post("/skills/translate")
async def translate_skills(body: dict):
    """skill 卡片中文简介：批量 [{name, description}] → [{name, zh}]。
    无汉字的描述才送译（kv 缓存）；未配模型/失败 zh=null，前端回退原文。"""
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(400, "items 须为列表")
    return {"items": await skill_zh.translate_batch(items)}
@router.get("/skills/{name}")
def skill_detail(name: str):
    d = skills_mod.get(name)
    if d is None:
        raise HTTPException(404, f"skill 不存在: {name}")
    return d
@router.post("/skills")
def create_skill(body: dict):
    try:
        return skills_mod.create(str(body.get("name") or ""), str(body.get("description") or ""))
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.post("/skills/{name}/toggle")
def toggle_skill(name: str, body: dict):
    """启用/禁用 skill：禁用后不进新会话、active 会话下一 turn 失效。"""
    try:
        return skills_mod.set_disabled(name, bool(body.get("disabled")))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.delete("/skills/{name}")
def delete_skill(name: str, force: bool = False):
    try:
        if not force:
            used = skills_mod.sessions_using(name)
            if used:
                raise HTTPException(409, f"被 {len(used)} 个会话挂载"
                                             f"（force=true 强删，会话内失效）: {', '.join(used[:5])}")
        return skills_mod.delete(name)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.get("/skills/{name}/file")
def get_skill_file(name: str, path: str):
    try:
        return {"path": path, "content": skills_mod.read_file(name, path)}
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.put("/skills/{name}/file")
def put_skill_file(name: str, body: dict):
    try:
        return skills_mod.write_file(name, str(body.get("path") or ""),
                                     str(body.get("content") or ""), create=False)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.post("/skills/{name}/file")
def post_skill_file(name: str, body: dict):
    try:
        return skills_mod.write_file(name, str(body.get("path") or ""),
                                     str(body.get("content") or ""), create=True)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.delete("/skills/{name}/file")
def delete_skill_file(name: str, path: str):
    try:
        return skills_mod.delete_file(name, path)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.post("/skills/install")
def install_skill(body: dict):
    try:
        repo = str(body.get("repo") or "")
        subpath = str(body.get("subpath") or "")
        ref = body.get("ref") or None
        if body.get("repo_url"):
            parsed = skills_mod.parse_repo_url(str(body["repo_url"]))
            repo = parsed["repo"]
            subpath = parsed["subpath"]
            ref = parsed["ref"] or ref
        return skills_mod.install_from_github(
            repo, subpath, ref, overwrite=bool(body.get("overwrite")))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.post("/skills/upload")
async def upload_skill_zip(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(400, "zip 超过 100MB")
    try:
        return skills_mod.install_from_zip(data, overwrite=False)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
@router.get("/skillhub/search")
def skillhub_search(q: str = ""):
    return skillhub.search(q)
@router.get("/skillhub/catalog")
def skillhub_catalog():
    return skillhub.catalog()
