"""MCP servers 与内建工具开关的管理面（Tools tab 后端）。

- MCP：CRUD 写回 config.yaml（yaml round-trip 会丢注释——改前备份 var/backups/，
  README 已说明）+ 更新内存 CONFIG + 重物化所有 active 会话 .mcp.json
  （跑中的 turn 不受影响，下一 turn 生效）
- 内建工具开关：per-profile disallowed_tools → registry.yaml round-trip +
  清 profile 缓存；会话 .claude/settings.json permissions.disallow 注入
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from .. import db as db_mod
from ..config import CONFIG, PATHS
from ..util import get_logger, iso

log = get_logger(__name__)

# 暴露给 UI 的内建工具开关（有配额/副作用/子代理派生的一类；只读类不暴露）
BUILTIN_TOOLS = ["WebSearch", "WebFetch", "Task"]


def _backup(path: Path) -> None:
    if path.exists():
        bdir = PATHS["backups"]
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, bdir / f"{path.name}.{iso().replace(':', '').replace('-', '')}")


def _load_yaml_conf(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _dump_yaml_conf(path: Path, data: dict) -> None:
    _backup(path)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    tmp.replace(path)


def _validate_server(spec: dict) -> dict:
    """校验并规范化一条 mcpServers 定义（标准项目级 .mcp.json 格式）。"""
    stype = spec.get("type", "stdio")
    if stype not in ("stdio", "http", "sse"):
        raise ValueError("type 只能是 stdio|http|sse")
    out: dict = {"type": stype}
    if stype == "stdio":
        if not str(spec.get("command") or "").strip():
            raise ValueError("stdio 需要 command")
        out["command"] = str(spec["command"]).strip()
        if spec.get("args") is not None:
            if not isinstance(spec.get("args"), list) or not all(isinstance(a, str) for a in spec["args"]):
                raise ValueError("args 需为字符串数组")
            out["args"] = spec["args"]
    else:
        url = str(spec.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"{stype} 需要 http(s) url")
        out["url"] = url
    for key in ("env", "headers"):
        v = spec.get(key)
        if v is None:
            continue
        if not isinstance(v, dict) or not all(isinstance(k, str) and isinstance(x, str) for k, x in v.items()):
            raise ValueError(f"{key} 需为 string→string 映射")
        out[key] = v
    return out


def list_servers() -> dict:
    """全局 servers + 会话级覆盖引用计数 + 内建工具开关矩阵。"""
    session_use: dict[str, int] = {}
    overrides: dict[str, list[str]] = {}
    with db_mod.conn() as c:
        for r in c.execute("SELECT id, mcp_json FROM sessions WHERE status='active'"):
            try:
                sess_mcp = json.loads(r["mcp_json"] or "{}")
            except json.JSONDecodeError:
                continue
            for k in sess_mcp:
                session_use[k] = session_use.get(k, 0) + 1
                overrides.setdefault(k, []).append(r["id"])
    servers = []
    for name, spec in (CONFIG.mcp.servers or {}).items():
        servers.append({"name": name, "spec": spec,
                        "sessions_overriding": session_use.pop(name, 0)})
    for name, n in session_use.items():      # 只有会话级覆盖、无全局定义的
        servers.append({"name": name, "spec": {}, "sessions_overriding": n,
                        "session_only": True})
    return {"servers": servers}


def put_server(name: str, spec: dict) -> dict:
    if not name or not all(ch.isalnum() or ch in "-_." for ch in name) or len(name) > 64:
        raise ValueError("server 名限 [a-zA-Z0-9._-]，≤64 字符")
    norm = _validate_server(spec)
    path = PATHS["root"] / "config.yaml"
    data = _load_yaml_conf(path)
    data.setdefault("mcp", {}).setdefault("servers", {})
    data["mcp"]["servers"][name] = norm
    _dump_yaml_conf(path, data)
    CONFIG.mcp.servers = dict(data["mcp"]["servers"])
    rematerialize_sessions()
    return {"ok": True, "name": name, "spec": norm, "sessions": rematerialize_sessions()}


def delete_server(name: str) -> dict:
    path = PATHS["root"] / "config.yaml"
    data = _load_yaml_conf(path)
    servers = (data.get("mcp") or {}).get("servers") or {}
    if name not in servers:
        raise KeyError(f"全局未定义: {name}")
    _backup(path)
    del servers[name]
    _dump_yaml_conf(path, data)
    CONFIG.mcp.servers = dict(servers)
    return {"ok": True, "deleted": name, "sessions": rematerialize_sessions()}


def rematerialize_sessions() -> int:
    """全局 servers/egress 变化后重写所有 active 会话的 .mcp.json + egress 快照。"""
    from .. import params as params_mod
    from .. import workspace as ws_mod
    n = 0
    seen: set[str] = set()          # 共享工作区 dedupe：同项目子任务只写一次
    with db_mod.conn() as c:
        rows = c.execute(
            "SELECT id, mcp_json, params_json FROM sessions"
            " WHERE status='active'").fetchall()
    for r in rows:
        try:
            sess_mcp = json.loads(r["mcp_json"] or "{}")
        except json.JSONDecodeError:
            sess_mcp = {}
        ws = ws_mod.ws_of(r["id"])
        key = str(ws)
        if key in seen or not ws.is_dir():
            continue
        seen.add(key)
        ws_mod.write_mcp_json(ws, sess_mcp)
        # egress 快照带会话档合并（hook 门对齐——沙箱内 hook 读这份）
        ov = params_mod.load(r["params_json"])
        ws_mod.write_egress_snapshot(
            ws, mode=(ov.get("egress") if isinstance(ov, dict) else None))
        n += 1
    return n


# ---------------------------------------------------------------- 内建工具开关
def tools_overview() -> dict:
    from .. import profile as profile_mod
    reg = profile_mod.load_registry()
    profiles = []
    for p in reg.values():
        profiles.append({"name": p.name, "description": p.description,
                         "disallowed_tools": list(p.disallowed_tools or [])})
    return {"builtin_tools": BUILTIN_TOOLS, "profiles": profiles}


def put_profile_tools(profile_name: str, disallowed: list[str]) -> dict:
    from .. import profile as profile_mod
    reg = profile_mod.load_registry()
    if profile_name not in reg:
        raise KeyError(f"未知 profile: {profile_name}")
    norm = sorted({str(t) for t in disallowed if str(t) in BUILTIN_TOOLS})
    from ..config import ROOT as _R
    path = _R / "profiles" / "registry.yaml"      # 写到数据根（用户定制层）
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    data = _load_yaml_conf(path)
    entry = (data.get("profiles") or {}).get(profile_name)
    if entry is None:            # registry.yaml 缺该条（内置兜底 profile）→ 补最小条目
        data.setdefault("profiles", {})[profile_name] = entry = {}
    if norm:
        entry["disallowed_tools"] = norm
    else:
        entry.pop("disallowed_tools", None)
    _dump_yaml_conf(path, data)
    profile_mod.reset_cache()
    return {"ok": True, "profile": profile_name, "disallowed_tools": norm}
