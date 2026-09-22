"""账号保险库（P0-2）：平台级账号凭证的单一真源。

证书项目的教训：密码明文躺在各会话 state.json / credentials.md 里，Google
密码记错一次触发 48h 安全延迟；账号身份散在 playbook / state / credentials
三处互相漂移。本模块把账号收进 **var/vault.enc**（AES-256-GCM，0600、原子写；W3.1 起
加密，格式 [LDV1][ver u16][nonce 12B][ct||tag]，主密钥 env LOADN_VAULT_KEY
> keyring > 自动生成 var/.vault_key——密钥与密文同机，防误拷贝/误提交，
不防全控同用户的攻击者），登录脚本一律 `wd r account --platform x
--field password` 取用，skill 文本禁止出现明文密码。

schema（platform 为键，全小写规范化）：
  {"google": {"username": "...", "password": "...", "recovery": "...",
              "email": "绑定邮箱", "phone": "绑定手机", "twofa": "2FA 说明",
              "status": "ok|suspended|locked|...", "notes": "...",
              "updated_at": "..."}}
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from .config import PATHS
from .util import get_logger, iso

log = get_logger(__name__)

_MAGIC = b"LDV1"          # 加密文件头（W3.1：vault.json → vault.enc AES-256-GCM）
_VERSION = 1

# 可写字段白名单（platform 自身是键）；frozenset 防拼错静默丢字段
FIELDS = ("username", "password", "recovery", "email", "phone", "twofa",
          "status", "notes")
# 非敏感字段：--list / 默认展示可见；password/recovery 只在显式 --field 或全量视图
SAFE_FIELDS = ("username", "email", "phone", "twofa", "status", "notes")


def vault_path() -> Path:
    return PATHS["var"] / "vault.enc"


def legacy_path() -> Path:
    return PATHS["var"] / "vault.json"


def _key_path() -> Path:
    return PATHS["var"] / ".vault_key"


def _master_key() -> bytes:
    """主密钥：env > keyring > 自动生成密钥文件（0600，32B）。"""
    env_key = os.environ.get("LOADN_VAULT_KEY", "").strip()
    if env_key:
        import hashlib
        return hashlib.sha256(env_key.encode()).digest()
    try:                                    # keyring 可用则用（无桌面机常失败）
        import keyring
        v = keyring.get_password("loadn-vault", "master")
        if v:
            import hashlib
            return hashlib.sha256(v.encode()).digest()
    except Exception:                       # noqa: BLE001
        pass
    p = _key_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        import base64
        tmp = p.with_suffix(".tmp")
        tmp.write_text(base64.b64encode(secrets.token_bytes(32)).decode())
        tmp.replace(p)
        os.chmod(p, 0o600)
        log.info("vault 主密钥已生成 %s（0600）", p)
    import base64
    return base64.b64decode(p.read_text().strip())


def _encrypt(data: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_master_key()).encrypt(
        nonce, json.dumps(data, ensure_ascii=False).encode(), None)
    return _MAGIC + _VERSION.to_bytes(2, "big") + nonce + ct


def _decrypt(blob: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 4 + 2 + 12 + 16 or blob[:4] != _MAGIC:
        raise ValueError("vault.enc 格式不符（非 LDV1）")
    nonce, ct = blob[6:18], blob[18:]
    raw = AESGCM(_master_key()).decrypt(nonce, ct, None)   # GCM 校验失败即抛
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def load() -> dict:
    _migrate_legacy_if_any()
    p = vault_path()
    if not p.exists():
        return {}
    try:
        return _decrypt(p.read_bytes())
    except Exception:                       # noqa: BLE001
        log.exception("vault 解密失败（密钥不符或文件损坏）——按空库处理")
        return {}


def save(data: dict) -> None:
    p = vault_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(_encrypt(data))
    tmp.replace(p)
    os.chmod(p, 0o600)          # 凭证文件：仅属主可读写


def _wipe(path: Path) -> None:
    """三次覆写后删除（旧明文迁移用）。"""
    if not path.exists():
        return
    size = path.stat().st_size
    for pattern in (b"\x00", b"\xff", os.urandom(1) * max(size, 1)):
        with path.open("rb+") as f:
            f.write(pattern * size if pattern * size else pattern)
            f.flush()
            os.fsync(f.fileno())
    path.unlink()


def _migrate_legacy_if_any() -> bool:
    """旧明文 vault.json → vault.enc（一次），旧文件三次覆写删除。"""
    old, new = legacy_path(), vault_path()
    if new.exists() or not old.exists():
        return False
    try:
        data = json.loads(old.read_text())
        data = data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    save(data)
    # 回滚通道：旧代码读不了 enc——明文副本留 backups/（0600，回滚后人工删）
    bak = PATHS.get("backups") or (PATHS["var"] / "backups")
    bak.mkdir(parents=True, exist_ok=True)
    bak_f = bak / "vault.json.premigrate.bak"
    if not bak_f.exists():
        bak_f.write_bytes(old.read_bytes())
        os.chmod(bak_f, 0o600)
    _wipe(old)
    log.info("vault 迁移完成：明文 %s → 加密 %s（旧文件已覆写删除）", old, new)
    return True


def verify() -> dict:
    """健康检查：格式/密钥可解/条目数/旧明文残留。"""
    _migrate_legacy_if_any()
    out = {"encrypted": vault_path().exists(),
           "legacy_plaintext": legacy_path().exists(),
           "entries": 0, "ok": False}
    if out["encrypted"]:
        try:
            out["entries"] = len(_decrypt(vault_path().read_bytes()))
            out["ok"] = True
        except Exception as e:              # noqa: BLE001
            out["error"] = str(e)
    else:
        out["error"] = "vault.enc 不存在（空库或未初始化）"
    return out


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
