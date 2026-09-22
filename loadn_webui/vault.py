"""账号保险库（P0-2）：平台级账号凭证的单一真源。

证书项目的教训：密码明文躺在各会话 state.json / credentials.md 里，Google
密码记错一次触发 48h 安全延迟；账号身份散在 playbook / state / credentials
三处互相漂移。本模块把账号收进 var/vault.json（0600、原子写、跨会话共享），
登录脚本一律 `wd r account --platform x --field password` 取用，skill 文本
禁止出现明文密码。

schema（platform 为键，全小写规范化）：
  {"google": {"username": "...", "password": "...", "recovery": "...",
              "email": "绑定邮箱", "phone": "绑定手机", "twofa": "2FA 说明",
              "status": "ok|suspended|locked|...", "notes": "...",
              "updated_at": "..."}}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import PATHS
from .util import iso

# 可写字段白名单（platform 自身是键）；frozenset 防拼错静默丢字段
FIELDS = ("username", "password", "recovery", "email", "phone", "twofa",
          "status", "notes")
# 非敏感字段：--list / 默认展示可见；password/recovery 只在显式 --field 或全量视图
SAFE_FIELDS = ("username", "email", "phone", "twofa", "status", "notes")


def vault_path() -> Path:
    return PATHS["var"] / "vault.json"


def load() -> dict:
    p = vault_path()
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    p = vault_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(p)
    os.chmod(p, 0o600)          # 凭证文件：仅属主可读写


def norm_platform(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "-")


def get(platform: str) -> dict | None:
    return load().get(norm_platform(platform))


def put(platform: str, **fields) -> dict:
    """写入/更新账号条目（白名单字段；platform 维度整体 merge）。"""
    bad = set(fields) - set(FIELDS)
    if bad:
        raise ValueError(f"未知字段: {', '.join(sorted(bad))}（可用: {', '.join(FIELDS)}）")
    key = norm_platform(platform)
    if not key:
        raise ValueError("platform 不能为空")
    data = load()
    entry = dict(data.get(key) or {})
    entry.update({k: v for k, v in fields.items() if v is not None})
    entry["updated_at"] = iso()
    data[key] = entry
    save(data)
    return entry


def delete(platform: str) -> bool:
    key = norm_platform(platform)
    data = load()
    if key not in data:
        return False
    del data[key]
    save(data)
    return True


def list_platforms() -> list[dict]:
    """全部条目概览（不含 password/recovery 明文，只带 has_password 标记）。"""
    out = []
    for key, e in sorted(load().items()):
        row = {"platform": key}
        for f in SAFE_FIELDS:
            row[f] = e.get(f) or ""
        row["has_password"] = bool(e.get("password"))
        row["has_recovery"] = bool(e.get("recovery"))
        row["updated_at"] = e.get("updated_at") or ""
        out.append(row)
    return out


def view(platform: str, reveal: bool = False) -> dict | None:
    """单账号视图。reveal=False 打码密码（默认，防 terminal 留痕）；True 全量。"""
    e = get(platform)
    if e is None:
        return None
    if reveal:
        return {"platform": norm_platform(platform), **e}
    masked = {k: v for k, v in e.items() if k not in ("password", "recovery")}
    if e.get("password"):
        masked["password"] = f"••••（{len(e['password'])} 位，--field password 取明文）"
    if e.get("recovery"):
        masked["recovery"] = "已设置（--field recovery 取明文）"
    return {"platform": norm_platform(platform), **masked}


def field(platform: str, name: str) -> str | None:
    """取单字段明文（登录脚本 $(wd r account --platform x --field password) 用）。"""
    e = get(platform)
    if e is None:
        return None
    if name not in FIELDS:
        raise ValueError(f"未知字段 {name!r}（可用: {', '.join(FIELDS)}）")
    v = e.get(name)
    return str(v) if v is not None else None
