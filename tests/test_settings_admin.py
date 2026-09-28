"""设置管理面扩展（队列外需求：WebUI 配置入口补全）。

覆盖：engines/claude/share/pricing 分节写路径（yaml round-trip + 内存热更）、
server 段只读脱敏、resources 的 textr 字段（明文进 yaml / 密钥进 vault）。
隔离方式承 test_notify.test_settings_roundtrip：换独立 Config 实例 +
PATHS 指向 tmp_path（不 reload config 模块，防全局污染）。
"""

import pytest


@pytest.fixture()
def sa(tmp_path, monkeypatch):
    import loadn_webui.settings_admin as sa_mod
    from loadn_webui.config import Config
    cfg = Config()
    monkeypatch.setattr(sa_mod, "CONFIG", cfg)
    monkeypatch.setattr(sa_mod, "PATHS", {"root": tmp_path, "var": tmp_path / "var"})
    return sa_mod, cfg, tmp_path


def _yaml(tmp_path):
    import yaml
    p = tmp_path / "config.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


# ---------------- engines ----------------

def test_put_engines_roundtrip(sa):
    mod, cfg, tmp = sa
    out = mod.put_engines({
        "default": "loadn",
        "no_compact": True,
        "opencode_provider": "zai",
        "engines": {"claude": {"model": "glm-5.3", "extra_args": ["--strict", ""]},
                    "loadn": {"enabled": False, "bin": "/usr/local/bin/loadn"}},
    })
    assert out["engines"]["default"] == "loadn"
    assert cfg.engines.default == "loadn"                 # 内存热更（下一 turn 生效）
    assert cfg.engines.no_compact is True
    assert cfg.engines.claude.model == "glm-5.3"
    assert cfg.engines.claude.extra_args == ["--strict"]  # 空白项剔除
    assert cfg.engines.loadn.enabled is False
    y = _yaml(tmp)
    assert y["engines"]["default"] == "loadn"             # yaml 落盘（嵌套段合并）
    assert y["engines"]["claude"]["model"] == "glm-5.3"
    assert y["engines"]["loadn"]["enabled"] is False
    # 二次写入只动给的子段，不动其他引擎
    mod.put_engines({"engines": {"opencode": {"provider": "zai"}}})
    assert _yaml(tmp)["engines"]["claude"]["model"] == "glm-5.3"


def test_put_engines_validation(sa):
    mod, _, _ = sa
    with pytest.raises(ValueError, match="未知引擎"):
        mod.put_engines({"default": "cursor"})
    with pytest.raises(ValueError, match="no_compact"):
        mod.put_engines({"no_compact": "auto"})
    with pytest.raises(ValueError, match="extra_args"):
        mod.put_engines({"engines": {"claude": {"extra_args": "--verbose"}}})
    with pytest.raises(ValueError, match="未知引擎段"):
        mod.put_engines({"engines": {"gemini": {}}})
    with pytest.raises(ValueError, match="未知字段"):
        mod.put_engines({"engines": {"claude": {"modle": "x"}}})
    with pytest.raises(ValueError, match="没有可更新"):
        mod.put_engines({})
    # tri-state 合法：null=自动
    out = mod.put_engines({"no_compact": None})
    assert out["engines"]["no_compact"] is None


# ---------------- claude ----------------

def test_put_claude(sa):
    mod, cfg, tmp = sa
    out = mod.put_claude({"effort": "medium", "model": "glm-5.3"})
    assert out["claude"]["effort"] == "medium"
    assert cfg.claude.model == "glm-5.3"
    assert _yaml(tmp)["claude"]["model"] == "glm-5.3"
    # 清空模型 = 显式 null（继承 CLI/订阅默认，覆盖旧值）
    mod.put_claude({"model": ""})
    assert cfg.claude.model is None
    assert _yaml(tmp)["claude"]["model"] is None
    with pytest.raises(ValueError, match="effort"):
        mod.put_claude({"effort": "ultra"})
    with pytest.raises(ValueError, match="没有可更新"):
        mod.put_claude({})


# ---------------- share / pricing ----------------

def test_put_share(sa):
    mod, cfg, tmp = sa
    out = mod.put_share({"base_url": "https://your-domain.com/share/"})
    assert out["share"]["base_url"] == "https://your-domain.com/share"   # 去尾斜杠
    assert cfg.share.base_url == "https://your-domain.com/share"
    assert _yaml(tmp)["share"]["base_url"].endswith("/share")
    with pytest.raises(ValueError, match="http"):
        mod.put_share({"base_url": "your-domain.com"})
    mod.put_share({"base_url": ""})          # 空 = 关闭分享
    assert cfg.share.base_url == ""


def test_put_pricing(sa, monkeypatch):
    mod, cfg, tmp = sa
    out = mod.put_pricing({"usd_cny": 7.3, "api": {"glm-5.3": {"input": 0.5}}})
    assert out["pricing"]["usd_cny"] == 7.3
    assert cfg.pricing.api == {"glm-5.3": {"input": 0.5}}
    assert _yaml(tmp)["pricing"]["api"] == {"glm-5.3": {"input": 0.5}}
    # 空表 = 整表恢复内置。pricing._tables 调用时才 import CONFIG——
    # 把全局 config.CONFIG 也指到本测试实例，回退路径才算真被验证
    import loadn_webui.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG", cfg)
    from loadn_webui.integrations import pricing
    mod.put_pricing({"api": {}})
    api, _, _ = pricing._tables()
    assert api == pricing.API_PRICING
    with pytest.raises(ValueError, match="usd_cny"):
        mod.put_pricing({"usd_cny": 1000})
    with pytest.raises(ValueError, match="数值"):
        mod.put_pricing({"api": {"glm-5.3": {"input": "$0.5"}}})
    with pytest.raises(ValueError, match="pricing.api"):
        mod.put_pricing({"api": ["glm-5.3"]})


# ---------------- 脱敏（redact） ----------------

def test_get_settings_no_secret_leak(sa):
    """>>> 安全纪律：任何配置读路径不回传 token/密钥明文。"""
    mod, cfg, _ = sa
    cfg.server.token = "sec-" + "x" * 20
    cfg.server.admin_token = "adm-" + "y" * 20
    cfg.titlegen.api_key = "sk-tg" + "z" * 10
    s = mod.get_settings()
    blob = repr(s)
    assert "sec-" not in blob and "adm-" not in blob and "sk-tg" not in blob
    assert s["server"]["token_set"] is True and s["server"]["admin_token_set"] is True
    assert s["server"]["host"] and s["server"]["port"] > 0
    assert s["titlegen"]["api_key_set"] is True
    assert "…" in s["titlegen"]["api_key_hint"]          # 只回末 5 位 hint
    # engines/claude/share/pricing 新分节形状
    assert s["engines"]["available"]                     # 至少注册表内置引擎
    assert set(s["engines"]["per"]) >= {"claude", "loadn", "opencode"}
    assert s["claude"]["effort"] in ("low", "medium", "high")


# ---------------- resources 扩展（textr：明文 yaml / 密钥 vault） ----------------

def test_put_resources_textr(sa, monkeypatch):
    import loadn_webui.security.vault as vault
    mod, cfg, tmp = sa
    monkeypatch.setattr(vault, "PATHS", {"root": tmp, "var": tmp / "var"})
    out = mod.put_resources({"textr_email": "u@example.com",
                             "textr_password": "secret-textr"})
    assert out["resources"]["textr_email"] == "u@example.com"
    assert out["resources"]["textr_password_set"] is True
    assert cfg.resources.textr_email == "u@example.com"
    y = _yaml(tmp)
    assert y["resources"]["textr_email"] == "u@example.com"
    assert "textr_password" not in str(y)                # 密钥不落 yaml
    assert vault.get_res_secret("textr_password") == "secret-textr"  # 进 vault


# ---------------- engines 覆盖删除（null=整段删） + override 标记 ----------------

def test_put_engines_null_deletes_section(sa):
    """null=清除覆盖：yaml 段删除不残留、CONFIG 重置回默认；白名单不被 null 绕过。"""
    mod, cfg, tmp = sa
    mod.put_engines({"engines": {"claude": {"model": "glm-5.3", "extra_args": ["--x"]}}})
    assert _yaml(tmp)["engines"]["claude"]["model"] == "glm-5.3"
    out = mod.put_engines({"engines": {"claude": None, "loadn": {"model": "glm-5.3-flash"}}})
    y = _yaml(tmp)["engines"]
    assert "claude" not in y                          # 整段删除（非 null 键残留）
    assert y["loadn"]["model"] == "glm-5.3-flash"     # 同请求混用：删与写并存
    assert cfg.engines.claude.model is None           # CONFIG 重置（干净实例非逐键）
    assert cfg.engines.claude.extra_args == []
    assert cfg.engines.loadn.model == "glm-5.3-flash"
    with pytest.raises(ValueError, match="未知引擎段"):
        mod.put_engines({"engines": {"gemini": None}})   # null 不绕白名单


def test_get_settings_engine_override_flag(sa):
    """>>> 守卫对赌：override 标记以 yaml 原始节为真源（真值断言，非 truthy 串）。"""
    mod, _, tmp = sa
    s = mod.get_settings()
    assert all(s["engines"]["per"][n]["override"] is False
               for n in ("claude", "loadn", "opencode", "hahaness"))  # 无段=假
    mod.put_engines({"engines": {"claude": {"model": "glm-5.3"}}})
    per = mod.get_settings()["engines"]["per"]
    assert per["claude"]["override"] is True          # 有段=真（CONFIG 值无法区分，靠 yaml）
    assert per["loadn"]["override"] is False
    mod.put_engines({"engines": {"claude": None}})
    assert mod.get_settings()["engines"]["per"]["claude"]["override"] is False


def test_get_settings_new_sections(sa):
    """additive 新键：convergence_defaults（profile 真源）与 pricing.builtin 内置表。"""
    mod, _, _ = sa
    s = mod.get_settings()
    assert s["convergence_defaults"] == {"timeout_s": 3600,
                                         "stall_timeout_s": 1800, "max_turns": None}
    assert "glm-5.3" in s["pricing"]["builtin"]["api"]
    assert "cache_input" in s["pricing"]["builtin"]["plan_credits"]["glm-5.3"]
    assert s["pricing"]["api"] == {}                  # 默认覆盖缺席（内置兜底态）


# ---------------- convergence 恢复默认（reset=pop 三键，条目必须保留） ----------------

def _setup_registry(tmp, body: str):
    import loadn_webui.config as config_mod
    import loadn_webui.profile as profile_mod
    # ROOT 双消费点（put_convergence 写路径 + behavior_file 读路径）都指 tmp；
    # registry 缓存清空防跨用例污染
    orig = config_mod.ROOT
    config_mod.ROOT = tmp
    d = tmp / "profiles"
    d.mkdir(exist_ok=True)
    (d / "registry.yaml").write_text(body)
    (d / "researcher.md").write_text("# 正文\n")
    profile_mod.reset_cache()
    return profile_mod, lambda: (setattr(config_mod, "ROOT", orig),
                                 profile_mod.reset_cache())


def test_put_convergence_reset(sa):
    """reset 弹三键回 profile 默认；条目本体与兄弟键保留（删条目=删角色，红线）。"""
    mod, _, tmp = sa
    profile_mod, teardown = _setup_registry(tmp, (
        "profiles:\n"
        "  researcher:\n"
        "    description: 研究员\n"
        "    timeout_s: 1200\n"
        "    stall_timeout_s: 900\n"
        "    max_turns: 50\n"
    ))
    try:
        out = mod.put_convergence({"reset": ["researcher"]})
        import yaml as _y
        raw = _y.safe_load((tmp / "profiles" / "registry.yaml").read_text())
        entry = raw["profiles"]["researcher"]
        assert entry == {"description": "研究员"}     # 红线：角色条目还在，只弹收敛三键
        snap = {p["name"]: p for p in out["profiles"]}
        assert snap["researcher"]["timeout_s"] == 3600    # 回 PROFILE_DEFAULTS
        assert snap["researcher"]["stall_timeout_s"] == 1800
        assert snap["researcher"]["max_turns"] is None
    finally:
        teardown()


def test_put_convergence_reset_coexist_and_guards(sa):
    """reset 与 profiles 同请求并存（reset 先应用）；未知角色/非数组/空 body 拒绝。"""
    mod, _, tmp = sa
    profile_mod, teardown = _setup_registry(tmp, (
        "profiles:\n"
        "  researcher:\n"
        "    timeout_s: 1200\n"
        "  coder:\n"
        "    timeout_s: 600\n"
    ))
    try:
        out = mod.put_convergence({"reset": ["researcher"],
                                   "profiles": {"coder": {"timeout_s": 300}}})
        snap = {p["name"]: p for p in out["profiles"]}
        assert snap["researcher"]["timeout_s"] == 3600  # reset 生效
        assert snap["coder"]["timeout_s"] == 300        # 同请求写值也生效
        with pytest.raises(ValueError, match="未知 profile"):
            mod.put_convergence({"reset": ["nope"]})
        with pytest.raises(ValueError, match="reset 需为角色名数组"):
            mod.put_convergence({"reset": "researcher"})
        with pytest.raises(ValueError, match="没有可更新的角色"):
            mod.put_convergence({})
    finally:
        teardown()


def test_put_convergence_reset_fallback_keeps_role(sa):
    """registry 空表兜底态（仅 builtin assistant）reset：写空条目占位，角色不消失。"""
    mod, _, tmp = sa
    profile_mod, teardown = _setup_registry(tmp, "profiles: {}\n")
    try:
        out = mod.put_convergence({"reset": ["assistant"]})
        import yaml as _y
        raw = _y.safe_load((tmp / "profiles" / "registry.yaml").read_text())
        assert raw["profiles"]["assistant"] == {}       # 空条目=纯默认，角色保留
        assert [p["name"] for p in out["profiles"]] == ["assistant"]
    finally:
        teardown()


# ---------------- API 层接线（真 uvicorn，管理面双头由 client fixture 携带） ----------------

async def test_settings_api_wiring(client):
    """新端点过路由/管理面门：合法写 200 + 坏值 4xx（非 500）。

    注意服务线程与测试同进程共享全局 CONFIG——改完还原，防泄漏进后续用例。
    """
    from loadn_webui.config import CONFIG
    try:
        r = await client.put('/api/settings/engines', json={"default": "loadn"})
        assert r.status_code == 200 and r.json()["engines"]["default"] == "loadn"
        assert CONFIG.engines.default == "loadn"         # 服务进程内热更可见
        r2 = await client.put('/api/settings/claude', json={"effort": "low"})
        assert r2.status_code == 200 and r2.json()["claude"]["effort"] == "low"
        r3 = await client.put('/api/settings/engines', json={"default": "cursor"})
        assert 400 <= r3.status_code < 500
        r4 = await client.put('/api/settings/pricing', json={"usd_cny": 7.2})
        assert r4.status_code == 200
    finally:                                             # 还原共享单例
        CONFIG.engines.default = "claude"
        CONFIG.claude.effort = "high"
        CONFIG.pricing.usd_cny = 0.0
