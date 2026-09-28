"""平台设置管理面：读/写 config.yaml 的各分节。

- 与 MCP CRUD 同一套 yaml round-trip（丢注释 → 改前备份 var/backups/），
  写后同步更新内存 CONFIG（titlegen/notify/engines/claude/share/pricing
  即时生效——消费点每会话/每请求惰性读 CONFIG；run 的并发数需重启，
  引擎信号量在进程启动时创建）。
- api_key 等密钥不回传明文，只回 set+hint；server.token 只回布尔。
"""
from __future__ import annotations

from pathlib import Path

from .config import CONFIG, PATHS
from .integrations.mcp_admin import _dump_yaml_conf, _load_yaml_conf
from .util import get_logger

log = get_logger(__name__)

_KEY_FIELDS = ("enabled", "api_base", "model")

# resources 段的明文字段 / 密钥字段（密钥只回 set+hint，永不回明文）
_RES_PLAIN = ("ocr_url", "sandbox_url", "cdp_url", "proxy", "sms_url", "sms_phone",
              "mail_imap", "mail_smtp", "mail_user",
              "vlm_api_base", "vlm_model", "zhipu_engine", "adb_addr",
              "textr_email")
_RES_SECRETS = ("sandbox_api_key", "sms_token", "mail_auth_code", "vlm_api_key",
                "twocaptcha_key", "bocha_key", "zhipu_key", "textr_password")


def _conf_path() -> Path:
    return PATHS["root"] / "config.yaml"


def get_settings() -> dict:
    tg = CONFIG.titlegen
    r = CONFIG.resources
    res: dict = {k: getattr(r, k) for k in _RES_PLAIN}
    from .security import vault as vault_mod
    states = vault_mod.res_secret_states()
    for k in _RES_SECRETS:
        res[f"{k}_set"] = states.get(k, False)
        res[f"{k}_hint"] = "已加密保存" if states.get(k) else ""
    if not states.get("vlm_api_key") and CONFIG.titlegen.api_key:
        res["vlm_api_key_hint"] = "继承自动标题 key"
    nf = CONFIG.notify
    from .engines import ALIASES, ENGINES
    eng = CONFIG.engines
    per: dict = {}
    for name in ("claude", "loadn", "opencode", "hahaness"):
        p = getattr(eng, name)
        per[name] = {"bin": p.bin or "", "model": p.model or "",
                     "provider": p.provider or "", "enabled": bool(p.enabled),
                     "extra_args": list(p.extra_args or [])}
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
        "engines": {
            "default": eng.default,
            "available": [k for k in ENGINES if k not in ALIASES],
            "opencode_provider": eng.opencode_provider,
            "no_compact": eng.no_compact,
            "per": per,
        },
        "claude": {"effort": CONFIG.claude.effort,
                   "model": CONFIG.claude.model or "",
                   "claude_bin": CONFIG.claude.claude_bin or ""},
        "share": {"base_url": CONFIG.share.base_url},
        "pricing": {"usd_cny": CONFIG.pricing.usd_cny,
                    "api": CONFIG.pricing.api,
                    "plan_credits": CONFIG.pricing.plan_credits},
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
        # server 段只读（host/port/token 属启动期与部署面：轮换走 CLI
        # `loadn-web token rotate`——UI 里改自己正在用的 token 会把前端锁外面）
        "server": {"host": CONFIG.server.host, "port": CONFIG.server.port,
                   "token_set": bool(CONFIG.server.token),
                   "admin_token_set": bool(CONFIG.server.admin_token),
                   "token_grace_until": CONFIG.server.token_grace_until},
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


# ---------------- 引擎与模型（engines + claude 节） ----------------

# engines 节下可写的子引擎段（含旧名 hahaness 一版兼容）
_ENGINE_KEYS = ("claude", "loadn", "opencode", "hahaness")
_PER_ENGINE_FIELDS = ("bin", "model", "provider", "enabled", "extra_args")


def put_engines(body: dict) -> dict:
    """engines 节写入（默认引擎/opencode 前缀/内压开关/每引擎覆盖）。

    消费点（engines.resolve、各 spec.build_argv）每会话惰性读 CONFIG，
    setattr 即热生效（下一 turn 起）。
    """
    from .engines import ENGINES
    updates: dict = {}
    if "default" in body:
        v = str(body["default"] or "").strip()
        if v not in ENGINES:
            raise ValueError(f"未知引擎: {v!r}（可用 {'|'.join(sorted(ENGINES))}）")
        updates["default"] = v
    if "opencode_provider" in body:
        v = str(body["opencode_provider"] or "").strip()
        if not v:
            raise ValueError("opencode_provider 不能为空")
        updates["opencode_provider"] = v
    if "no_compact" in body:
        v = body["no_compact"]
        if v is not None and not isinstance(v, bool):
            raise ValueError("no_compact 需为 true/false/null（null=自动）")
        updates["no_compact"] = v
    per = body.get("engines") or {}
    if not isinstance(per, dict):
        raise ValueError("engines 需为对象 {claude|loadn|opencode|hahaness: {...}}")
    eng_updates: dict[str, dict] = {}
    for name, spec in per.items():
        if name not in _ENGINE_KEYS:
            raise ValueError(f"未知引擎段: {name!r}（可用 {'|'.join(_ENGINE_KEYS)}）")
        if not isinstance(spec, dict):
            raise ValueError(f"engines.{name} 需为对象")
        entry: dict = {}
        for k in ("bin", "model", "provider"):
            if k in spec:
                v = str(spec[k] or "").strip()
                entry[k] = v or None          # 空 = 清空覆盖（回自动探测/继承）
        if "enabled" in spec:
            if not isinstance(spec["enabled"], bool):
                raise ValueError(f"engines.{name}.enabled 需为布尔")
            entry["enabled"] = spec["enabled"]
        if "extra_args" in spec:
            args = spec["extra_args"]
            if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
                raise ValueError(f"engines.{name}.extra_args 需为字符串列表")
            entry["extra_args"] = [a.strip() for a in args if a.strip()]
        unknown = set(spec) - set(_PER_ENGINE_FIELDS)
        if unknown:
            raise ValueError(f"engines.{name} 含未知字段: {sorted(unknown)}")
        if entry:
            eng_updates[name] = entry
    if not updates and not eng_updates:
        raise ValueError("没有可更新的字段")
    data = _load_yaml_conf(_conf_path())
    sec = data.setdefault("engines", {})
    for k, v in updates.items():
        sec[k] = v
    for name, entry in eng_updates.items():    # 嵌套段与 yaml 现值合并
        cur = sec.get(name)
        base = dict(cur) if isinstance(cur, dict) else {}
        base.update(entry)
        sec[name] = base
    _dump_yaml_conf(_conf_path(), data)
    for k, v in updates.items():
        setattr(CONFIG.engines, k, v)
    for name, entry in eng_updates.items():
        obj = getattr(CONFIG.engines, name)
        for k, v in entry.items():
            setattr(obj, k, v)
    log.info("设置更新 engines: %s %s", updates,
             {k: sorted(v) for k, v in eng_updates.items()})
    return get_settings()


def put_claude(body: dict) -> dict:
    """claude 节写入（无头会话 effort/默认模型/claude CLI 路径）。"""
    from .params import _EFFORTS
    updates: dict = {}
    if "effort" in body:
        v = str(body["effort"] or "").strip()
        if v not in _EFFORTS:
            raise ValueError(f"effort 需为 {'|'.join(_EFFORTS)}")
        updates["effort"] = v
    if "model" in body:
        # 空 = 清空覆盖（继承 CLI/订阅默认）——显式写 null，加载层 setattr(None)
        updates["model"] = str(body["model"] or "").strip() or None
    if "claude_bin" in body:
        updates["claude_bin"] = str(body["claude_bin"] or "").strip() or None
    if not updates:
        raise ValueError("没有可更新的字段")
    _write_section("claude", updates)
    for k, v in updates.items():
        setattr(CONFIG.claude, k, v)
    log.info("设置更新 claude: %s", updates)
    return get_settings()


def put_share(body: dict) -> dict:
    """share 节写入（分享外链 base_url）。"""
    updates: dict = {}
    if "base_url" in body:
        v = str(body["base_url"] or "").strip().rstrip("/")
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("base_url 必须是 http(s) URL")
        updates["base_url"] = v
    if not updates:
        raise ValueError("没有可更新的字段")
    _write_section("share", updates)
    CONFIG.share.base_url = updates["base_url"]
    return get_settings()


def _check_price_table(name: str, table: object) -> None:
    """价目表结构校验：{模型名: {字段: 数值}}（整表替换语义，结构错启动即算错账）。"""
    if not isinstance(table, dict):
        raise ValueError(f"pricing.{name} 需为对象 {{模型名: {{字段: 数值}}}}")
    for model, prices in table.items():
        if not isinstance(prices, dict) or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool)
                for v in prices.values()):
            raise ValueError(f"pricing.{name}.{model} 需为 {{字段: 数值}} 对象")


def put_pricing(body: dict) -> dict:
    """pricing 节写入（usd_cny 汇率 + api/plan_credits 整表替换）。"""
    updates: dict = {}
    if "usd_cny" in body:
        v = body["usd_cny"]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 100:
            raise ValueError("usd_cny 需为 0-100 的数（0=内置 7.1）")
        updates["usd_cny"] = float(v)
    for k in ("api", "plan_credits"):
        if k in body:
            _check_price_table(k, body[k])
            updates[k] = body[k]      # 整表替换（{}=恢复内置价目，与加载语义一致）
    if not updates:
        raise ValueError("没有可更新的字段")
    _write_section("pricing", updates)
    for k, v in updates.items():
        setattr(CONFIG.pricing, k, v)
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
    from .security import vault as vault_mod
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
    from .security.egress_grants import valid_host
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
    from .security.audit import audit
    audit("egress_policy", {"action": f"allowlist-{action}", "host": h,
                            "allow": allow})
    log.info("egress 白名单 %s: %s（共 %d 域）", action, h, len(allow))
    return {"ok": True, "host": h, "allow": allow}


def _refresh_session_snapshots() -> None:
    """egress 面（白名单/档位）变更后刷新活跃会话快照（失败不阻断——
    快照缺席时 hook 回退 CONFIG，语义仍正确只是沙箱内看不到新值）。"""
    try:
        from .integrations.mcp_admin import rematerialize_sessions
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
    from .security.audit import audit
    audit("egress_policy", {"action": "policy-put", **updates})
    log.info("egress 策略更新：%s（热生效）", updates)
    return {"ok": True, **updates}


async def test_resources(only: list[str] | None = None) -> dict:
    """探测外部资源连通性（vlm 只查配置；真实链路用 CLI `wd r vlm` 验证）。"""
    from .integrations import resources
    return await resources.ping_all(only)


async def test_titlegen() -> dict:
    """真实调用一次，验证 key/模型/网络连通。"""
    from .integrations.titlegen import _chat
    reply = await _chat("你是一个连通性测试器。", "只回复两个字：正常", max_tokens=16)
    return {"ok": True, "reply": reply.strip()[:50]}


async def test_notify() -> dict:
    """真实推一条测试通知（发给用户自己；provider 未配置时 ok=False）。"""
    from .integrations.notify import ping
    return await ping()


def put_security_ops(body: dict) -> dict:
    """安全运维面写（v0.6.5 显式配置化七键的 WebUI 入口——安全中心）：
    sandbox 档位 / codemode·lsp 开关 / shared_readonly·resource_bridges
    授权面 / approval TTL 与终态 / egress 代理端口。键可省略（只改给的）。

    红线键（审计链/确认码门本体/信任门/SSRF 段）不在此面——宪法不可配。
    sandbox 档位变更在下一任务 spawn 生效（wrap_engine 惰性读 CONFIG；
    在跑任务沙箱已定型）——手改 yaml 才需重启，此写路径带 setattr 即热。
    """
    from .config import SANDBOX_TIERS
    updates: dict = {}
    if (sbx := body.get("sandbox")) is not None:
        if sbx not in SANDBOX_TIERS:
            raise ValueError(f"sandbox 需为 {' | '.join(SANDBOX_TIERS)}")
        updates["sandbox"] = sbx
    for k in ("codemode_enabled", "lsp_enabled"):
        if (v := body.get(k)) is not None:
            if not isinstance(v, bool):
                raise ValueError(f"{k} 需为布尔")
            updates[k] = v
    for k in ("shared_readonly", "resource_bridges"):
        if (v := body.get(k)) is not None:
            if not isinstance(v, list):
                raise ValueError(f"{k} 需为数组")
            if k == "resource_bridges":
                for b in v:
                    if not isinstance(b, dict) or not str(b.get("path") or "").strip() \
                            or b.get("mode") not in ("ro", "rw", "dev"):
                        raise ValueError(
                            "resource_bridges 每项需 {path: 绝对路径, mode: ro|rw|dev}")
            updates[k] = v
    if (ttl := body.get("approval_ttl_s")) is not None:
        if not isinstance(ttl, int) or isinstance(ttl, bool) \
                or not 60 <= ttl <= 86400:
            raise ValueError("approval_ttl_s 需为 60-86400 的整数（秒）")
        updates["approval_ttl_s"] = ttl
    if (ae := body.get("approval_enforce")) is not None:
        if ae not in ("enforce", "warn"):
            raise ValueError("approval_enforce 需为 enforce | warn")
        updates["approval_enforce"] = ae
    if (pp := body.get("egress_proxy_port")) is not None:
        if not isinstance(pp, int) or isinstance(pp, bool) \
                or not 0 <= pp <= 65535:
            raise ValueError("egress_proxy_port 需为 0-65535（0=随机）")
        updates["egress_proxy_port"] = pp
    if not updates:
        raise ValueError("无可更新字段")
    data = _load_yaml_conf(_conf_path())
    data.setdefault("security", {}).update(updates)
    _dump_yaml_conf(_conf_path(), data)
    for k, v in updates.items():
        setattr(CONFIG.security, k, v)
    from .security.audit import audit
    audit("policy_change", {"action": "security-ops-put", **updates})
    log.info("安全运维面更新：%s（sandbox 档位下一任务生效，其余热生效）",
             {k: v for k, v in updates.items()})
    return {"ok": True, **updates}
