"""平台设置管理面：读/写 config.yaml 的 titlegen 与 run 分节。

- 与 MCP CRUD 同一套 yaml round-trip（丢注释 → 改前备份 var/backups/），
  写后同步更新内存 CONFIG（titlegen 即时生效；run 的并发数需重启，
  引擎信号量在进程启动时创建）。
- api_key 不回传明文，只回 hint（末 5 位）。
"""
from __future__ import annotations

from pathlib import Path

from .config import CONFIG, PATHS
from .mcp_admin import _dump_yaml_conf, _load_yaml_conf
from .util import get_logger

log = get_logger(__name__)

_KEY_FIELDS = ("enabled", "api_base", "model")

# resources 段的明文字段 / 密钥字段（密钥只回 set+hint，永不回明文）
_RES_PLAIN = ("ocr_url", "sandbox_url", "cdp_url", "proxy", "sms_url", "sms_phone",
              "mail_imap", "mail_smtp", "mail_user",
              "vlm_api_base", "vlm_model", "zhipu_engine", "adb_addr")
_RES_SECRETS = ("sandbox_api_key", "sms_token", "mail_auth_code", "vlm_api_key",
                "twocaptcha_key", "bocha_key", "zhipu_key")


def _conf_path() -> Path:
    return PATHS["root"] / "config.yaml"


def get_settings() -> dict:
    tg = CONFIG.titlegen
    r = CONFIG.resources
    res: dict = {k: getattr(r, k) for k in _RES_PLAIN}
    from . import vault as vault_mod
    states = vault_mod.res_secret_states()
    for k in _RES_SECRETS:
        res[f"{k}_set"] = states.get(k, False)
        res[f"{k}_hint"] = "已加密保存" if states.get(k) else ""
    if not states.get("vlm_api_key") and CONFIG.titlegen.api_key:
        res["vlm_api_key_hint"] = "继承自动标题 key"
    nf = CONFIG.notify
    return {
        "titlegen": {
            "enabled": bool(tg.enabled),
            "api_base": tg.api_base,
            "model": tg.model,
            "api_key_set": bool(tg.api_key),
            "api_key_hint": f"…{tg.api_key[-5:]}" if tg.api_key else "",
        },
        "run": {
            "max_concurrent_turns": CONFIG.run.max_concurrent_turns,
            "replay_max_events": CONFIG.run.replay_max_events,
            "events_retain_days": CONFIG.run.events_retain_days,
        },
        "convergence": convergence_snapshot(),
        "resources": res,
        "notify": {
            "provider": nf.provider,
            "bark_url": nf.bark_url,
            "events": dict(nf.events or {}),
            "serverchan_key_set": bool(nf.serverchan_key),
            "telegram_bot_token_set": bool(nf.telegram_bot_token),
            "telegram_chat_id": nf.telegram_chat_id,
        },
    }


# ---------------- 收敛度（按角色：超时 / 静默判死 / 工具轮次上限） ----------------

def convergence_snapshot() -> list[dict]:
    from . import profile as profile_mod
    return [{"name": p.name, "timeout_s": p.timeout_s,
             "stall_timeout_s": p.stall_timeout_s, "max_turns": p.max_turns}
            for p in profile_mod.load_registry().values()]


def put_convergence(body: dict) -> dict:
    """批量写角色收敛度 → registry.yaml（round-trip + 备份），下一 turn 生效。"""
    from . import profile as profile_mod
    reg = profile_mod.load_registry()
    updates = body.get("profiles") or {}
    if not updates:
        raise ValueError("没有可更新的角色")
    from .config import ROOT as _R
    path = _R / "profiles" / "registry.yaml"      # 写到数据根（用户定制层）
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_yaml_conf(path)
    raw = data.setdefault("profiles", {})
    for name, spec in updates.items():
        if name not in reg:
            raise ValueError(f"未知 profile: {name}")
        entry = raw.get(name)
        if entry is None:            # registry 缺该条（内置兜底）→ 补最小条目
            raw[name] = entry = {}
        if "timeout_s" in spec:
            v = spec["timeout_s"]
            n = int(v) if v not in (None, "") else 0   # 空/0 = 不限（跑到手动停止）
            if n != 0 and not 60 <= n <= 86400:
                raise ValueError(f"{name}.timeout_s 需在 60-86400 秒，0=不限")
            entry["timeout_s"] = n
        if "stall_timeout_s" in spec:
            n = int(spec["stall_timeout_s"])
            if not 60 <= n <= 86400:
                raise ValueError(f"{name}.stall_timeout_s 需在 60-86400 秒")
            entry["stall_timeout_s"] = n
        if "max_turns" in spec:
            v = spec["max_turns"]
            n = int(v) if v not in (None, "") else None   # 空 = 不限制
            if n is not None and not 1 <= n <= 1000:
                raise ValueError(f"{name}.max_turns 需在 1-1000 或留空")
            if n is None:
                entry.pop("max_turns", None)
            else:
                entry["max_turns"] = n
    _dump_yaml_conf(path, data)
    profile_mod.reset_cache()
    log.info("设置更新收敛度: %s", {k: v for k, v in updates.items()})
    return {"ok": True, "profiles": convergence_snapshot()}


def _write_section(section: str, updates: dict) -> None:
    path = _conf_path()
    data = _load_yaml_conf(path)
    data.setdefault(section, {}).update(updates)
    _dump_yaml_conf(path, data)


def put_titlegen(body: dict) -> dict:
    updates = {}
    if "enabled" in body:
        updates["enabled"] = bool(body["enabled"])
    if "api_base" in body:
        base = str(body["api_base"] or "").strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            raise ValueError("api_base 必须是 http(s) URL")
        updates["api_base"] = base or None
    if "model" in body:
        model = str(body["model"] or "").strip()
        if not model:
            raise ValueError("model 不能为空")
        updates["model"] = model
    api_key = str(body.get("api_key") or "").strip()
    if api_key:                                  # 留空 = 保持不变
        updates["api_key"] = api_key
    if not updates:
        raise ValueError("没有可更新的字段")
    updates = {k: v for k, v in updates.items() if v is not None}
    _write_section("titlegen", updates)
    for k, v in updates.items():
        setattr(CONFIG.titlegen, k, v)
    log.info("设置更新 titlegen: %s", {k: v for k, v in updates.items() if k != "api_key"})
    return get_settings()


def put_run(body: dict) -> dict:
    updates = {}
    if "max_concurrent_turns" in body:
        n = int(body["max_concurrent_turns"])
        if not 1 <= n <= 16:
            raise ValueError("max_concurrent_turns 需在 1-16")
        updates["max_concurrent_turns"] = n
    if "replay_max_events" in body:
        n = int(body["replay_max_events"])
        if not 20 <= n <= 2000:
            raise ValueError("replay_max_events 需在 20-2000")
        updates["replay_max_events"] = n
    if "events_retain_days" in body:
        n = float(body["events_retain_days"])
        if not 0.5 <= n <= 365:
            raise ValueError("events_retain_days 需在 0.5-365 天")
        updates["events_retain_days"] = n
    if not updates:
        raise ValueError("没有可更新的字段")
    _write_section("run", updates)
    for k, v in updates.items():
        setattr(CONFIG.run, k, v)
    return get_settings()


_NOTIFY_PROVIDERS = ("", "bark", "serverchan", "telegram")
_NOTIFY_SECRET_FIELDS = ("serverchan_key", "telegram_bot_token")


def put_notify(body: dict) -> dict:
    """notify 段写入（provider/bark_url/chat_id/事件开关；密钥留空=不变）。"""
    updates: dict = {}
    if "provider" in body:
        v = str(body["provider"] or "").strip()
        if v not in _NOTIFY_PROVIDERS:
            raise ValueError(f"provider 只能是 {'|'.join(_NOTIFY_PROVIDERS)}")
        updates["provider"] = v
    if "bark_url" in body:
        v = str(body["bark_url"] or "").strip().rstrip("/")
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("bark_url 必须是 http(s) URL")
        updates["bark_url"] = v
    if "telegram_chat_id" in body:
        updates["telegram_chat_id"] = str(body["telegram_chat_id"] or "").strip()
    if "events" in body:
        ev = body["events"]
        if not isinstance(ev, dict):
            raise ValueError("events 须为对象 {on_error,on_scheduled,on_turn_done}")
        updates["events"] = {k: bool(v) for k, v in ev.items()
                             if k in ("on_error", "on_scheduled", "on_turn_done")}
        updates["events"] = {**dict(CONFIG.notify.events or {}), **updates["events"]}
    for k in _NOTIFY_SECRET_FIELDS:
        v = str(body.get(k) or "").strip()
        if v:
            updates[k] = v
    if not updates:
        raise ValueError("没有可更新的字段")
    _write_section("notify", updates)
    for k, v in updates.items():
        setattr(CONFIG.notify, k, v)
    log.info("设置更新 notify: %s", {k: v for k, v in updates.items()
                                     if k not in _NOTIFY_SECRET_FIELDS}
             | {"secrets": [k for k in updates if k in _NOTIFY_SECRET_FIELDS]})
    return get_settings()


_RES_URL_FIELDS = ("ocr_url", "sandbox_url", "cdp_url", "proxy", "sms_url", "vlm_api_base")


def put_resources(body: dict) -> dict:
    updates: dict = {}
    for k in _RES_PLAIN:
        if k not in body:
            continue
        v = str(body[k] or "").strip().rstrip("/") if k.endswith("_url") or k == "proxy" \
            else str(body[k] or "").strip()
        if v and k in _RES_URL_FIELDS and not v.startswith(("http://", "https://")):
            raise ValueError(f"{k} 必须是 http(s) URL")
        if k == "adb_addr" and v and ":" not in v:
            raise ValueError("adb_addr 需要 host:port 形式")
        updates[k] = v
    from . import vault as vault_mod
    for k in _RES_SECRETS:
        v = str(body.get(k) or "").strip()
        if v:                                      # 密钥进 vault（AES-GCM），不落 yaml
            vault_mod.set_res_secret(k, v)
    if not updates:
        return get_settings()                      # 只改了密钥：已入 vault
    _write_section("resources", updates)
    for k, v in updates.items():
        setattr(CONFIG.resources, k, v)
    log.info("设置更新 resources: %s", {k: v for k, v in updates.items()
                                        if k not in _RES_SECRETS} | {"secrets": sorted(
                                            k for k in updates if k in _RES_SECRETS)})
    return get_settings()


def put_egress_allow(action: str, host: str) -> dict:
    """出口白名单增删（数据流向页「一键放行/移除」）。

    yaml round-trip 持久化 + 内存 CONFIG 原地更新——proxy._allowed 实时读
    CONFIG，写完即热生效（免重启，不再为加一个域杀掉在跑的 turn）。
    """
    from .egress_grants import valid_host
    h = valid_host(host or "")
    if not h:
        raise ValueError(f"域名非法: {host!r}（需形如 api.example.com，无 scheme/路径）")
    data = _load_yaml_conf(_conf_path())
    sec = data.setdefault("security", {})
    if "egress_allow" in sec:
        base = sec["egress_allow"] or []
    else:
        # yaml 未写该键：以内存现行清单为底（出厂默认或热更后的状态）——
        # 若以空表为底，首次放行会把默认白名单整体清零
        base = CONFIG.security.egress_allow
    allow = [str(a) for a in base if str(a).strip()]
    if action == "add":
        if h not in allow:
            allow.append(h)
    elif action == "remove":
        allow = [a for a in allow if a != h]
    else:
        raise ValueError("action 只能是 add|remove")
    sec["egress_allow"] = allow
    _dump_yaml_conf(_conf_path(), data)
    CONFIG.security.egress_allow = allow
    _refresh_session_snapshots()      # hook 门（会话快照）与 proxy 门同步放行
    from .audit import audit
    audit("egress_policy", {"action": f"allowlist-{action}", "host": h,
                            "allow": allow})
    log.info("egress 白名单 %s: %s（共 %d 域）", action, h, len(allow))
    return {"ok": True, "host": h, "allow": allow}


def _refresh_session_snapshots() -> None:
    """egress 面（白名单/档位）变更后刷新活跃会话快照（失败不阻断——
    快照缺席时 hook 回退 CONFIG，语义仍正确只是沙箱内看不到新值）。"""
    try:
        from .mcp_admin import rematerialize_sessions
        rematerialize_sessions()
    except Exception as e:  # noqa: BLE001
        log.warning("egress 会话快照刷新失败（hook 将回退 CONFIG）：%r", e)


def put_security_egress(body: dict) -> dict:
    """出口管控策略写（安全中心 EgressDetail 编辑控件的后端）：
    {mode: off|warn|enforce, on_deny: deny|ask, ask_wait_s: 15-600}，
    键可省略（只改给的）。yaml round-trip 持久化 + CONFIG 原地更新
    （代理 _gate 每连接读 CONFIG，写完即热生效）+ 审计。

    任务级放开不在这里——那是会话 params.egress（PATCH /sessions/{sid}）。
    """
    updates: dict = {}
    if (mode := body.get("mode")) is not None:
        if mode not in ("off", "warn", "enforce"):
            raise ValueError("mode 需为 off | warn | enforce")
        updates["egress_mode"] = mode
    if (on_deny := body.get("on_deny")) is not None:
        if on_deny not in ("deny", "ask"):
            raise ValueError("on_deny 需为 deny | ask")
        updates["egress_on_deny"] = on_deny
    if (wait_s := body.get("ask_wait_s")) is not None:
        if not isinstance(wait_s, int) or isinstance(wait_s, bool) \
                or not 15 <= wait_s <= 600:
            raise ValueError("ask_wait_s 需为 15-600 的整数（秒）")
        updates["egress_ask_wait_s"] = wait_s
    if not updates:
        raise ValueError("无可更新字段（mode/on_deny/ask_wait_s 至少给一个）")
    data = _load_yaml_conf(_conf_path())
    data.setdefault("security", {}).update(updates)
    _dump_yaml_conf(_conf_path(), data)
    for k, v in updates.items():
        setattr(CONFIG.security, k, v)
    _refresh_session_snapshots()      # 档位变更同步进会话快照（hook 门）
    from .audit import audit
    audit("egress_policy", {"action": "policy-put", **updates})
    log.info("egress 策略更新：%s（热生效）", updates)
    return {"ok": True, **updates}


async def test_resources(only: list[str] | None = None) -> dict:
    """探测外部资源连通性（vlm 只查配置；真实链路用 CLI `wd r vlm` 验证）。"""
    from . import resources
    return await resources.ping_all(only)


async def test_titlegen() -> dict:
    """真实调用一次，验证 key/模型/网络连通。"""
    from .titlegen import _chat
    reply = await _chat("你是一个连通性测试器。", "只回复两个字：正常", max_tokens=16)
    return {"ok": True, "reply": reply.strip()[:50]}
