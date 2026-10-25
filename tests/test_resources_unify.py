"""统一资源模型 v3（原生/自定义双轨合一）+ v3.1（搜索按供应商拆分）对赌测试。

覆盖：预置补种与幂等、锚字段投影（平台集成零改动续命）、retire 真删、
rename 密钥随行迁移、纯凭证条目、概览 ping/title 透传、search 拆分迁移。
隔离：独立 Config 实例 + PATHS/ROOT/CONFIG 指向 tmp_path。
"""

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import loadn_webui.config as cfg_mod
    import loadn_webui.settings_admin as sa_mod
    from loadn_webui.config import Config
    cfg = Config()
    monkeypatch.setattr(sa_mod, "CONFIG", cfg)
    monkeypatch.setattr(sa_mod, "PATHS", {"root": tmp_path,
                                          "var": tmp_path / "var"})
    monkeypatch.setattr(cfg_mod, "ROOT", tmp_path)      # retire 名单/yaml 落点
    # vault 等模块延迟 `from ..config import CONFIG`——源头也指 fake，
    # svc: 白名单校验随 fake custom_services 走（防碰真全局配置）
    monkeypatch.setattr(cfg_mod, "CONFIG", cfg)
    return sa_mod, cfg_mod, cfg, tmp_path


def _names(cfg):
    return [s.get("name") for s in (cfg.resources.custom_services or [])]


N_PRESETS = 11      # ocr/sandbox/proxy/adb/sms/mail/textr/vlm/bocha/zhipu/captcha


# ---------------- 补种 / 幂等 / 上抬 ----------------

def test_preset_seed_and_lift(env):
    """空配置 → ensure 补 11 条预置；存量扁平值无损上抬进条目。"""
    sa, cfg_mod, cfg, tmp = env
    cfg.resources.zhipu_engine = "search_pro_sogou"     # 老部署存量值
    cfg_mod.ensure_preset_services(cfg)
    names = _names(cfg)
    assert len(names) == N_PRESETS
    zhipu = next(s for s in cfg.resources.custom_services if s["name"] == "zhipu")
    eng = next(c for c in zhipu["config"] if c["key"] == "zhipu_engine")
    assert eng["value"] == "search_pro_sogou"                    # 上抬
    base = next(c for c in zhipu["config"] if c["key"] == "zhipu_base")
    assert base["value"].startswith("https://")                  # 基址锚在位
    assert cfg.resources.zhipu_engine == "search_pro_sogou"      # 锚投影回填


def test_preset_seed_idempotent(env):
    """重复 ensure 不重复补种。"""
    sa, cfg_mod, cfg, tmp = env
    cfg_mod.ensure_preset_services(cfg)
    cfg_mod.ensure_preset_services(cfg)
    assert len(_names(cfg)) == N_PRESETS


def test_existing_entry_not_overwritten(env):
    """yaml 里已有同名条目（用户改过）→ 补种跳过，用户版本保留。"""
    sa, cfg_mod, cfg, tmp = env
    cfg.resources.custom_services = [
        {"name": "zhipu", "url": "", "note": "", "category": "搜索",
         "config": [{"key": "zhipu_engine", "label": "档位",
                     "value": "search_std", "env": "LOADN_ZHIPU_ENGINE"}],
         "secrets": []}]
    cfg_mod.ensure_preset_services(cfg)
    zhipu = next(s for s in cfg.resources.custom_services if s["name"] == "zhipu")
    eng = next(c for c in zhipu["config"] if c["key"] == "zhipu_engine")
    assert eng["value"] == "search_std"
    assert cfg.resources.zhipu_engine == "search_std"


# ---------------- search 拆分迁移（v3.1） ----------------

def test_search_split_migration(env):
    """旧 search 聚合卡 → ensure 自动拆 bocha+zhipu 独立卡，锚值继承，
    yaml 落盘（重启不复活旧卡）。"""
    sa, cfg_mod, cfg, tmp = env
    import yaml
    # 老库存量：yaml 已有 search 条目（zhipu_engine 已被用户改过）
    (tmp / "config.yaml").write_text(yaml.safe_dump({
        "resources": {"custom_services": [
            {"name": "search", "url": "", "note": "", "category": "AI 能力",
             "config": [{"key": "zhipu_engine", "label": "智谱档",
                         "value": "search_pro_quark",
                         "env": "LOADN_SEARCH_ZHIPU_ENGINE"}],
             "secrets": []}]}}))
    cfg2 = cfg_mod.load_config()
    names = _names(cfg2)
    assert "search" not in names, "旧聚合卡应被拆走"
    assert "bocha" in names and "zhipu" in names
    zhipu = next(s for s in cfg2.resources.custom_services if s["name"] == "zhipu")
    eng = next(c for c in zhipu["config"] if c["key"] == "zhipu_engine")
    assert eng["value"] == "search_pro_quark", "旧卡锚值必须继承"
    # yaml 落盘：旧卡已移除（补种按设计只在内存——重启幂等补齐）
    data = yaml.safe_load((tmp / "config.yaml").read_text())
    ynames = [s["name"] for s in data["resources"]["custom_services"]]
    assert "search" not in ynames
    # 重启模拟：yaml 重载 → 旧卡不复活、新两卡补齐、锚值仍继承
    cfg3 = cfg_mod.load_config()
    n3 = _names(cfg3)
    assert "search" not in n3 and "bocha" in n3 and "zhipu" in n3
    z3 = next(s for s in cfg3.resources.custom_services if s["name"] == "zhipu")
    e3 = next(c for c in z3["config"] if c["key"] == "zhipu_engine")
    assert e3["value"] == "search_pro_quark"


# ---------------- 锚投影（平台集成零改动续命） ----------------

def test_anchor_projection_on_put(env):
    """保存 zhipu 条目改引擎档/基址 → ResourcesConfig 同名属性立即跟随。"""
    sa, cfg_mod, cfg, tmp = env
    cfg_mod.ensure_preset_services(cfg)
    sa.put_custom_service({
        "name": "zhipu", "title": "智谱搜索", "icon": "search",
        "category": "搜索", "desc": "搜索", "ping": ["zhipu"],
        "config": [{"key": "zhipu_engine", "label": "引擎档位",
                    "value": "search_pro_sogou", "env": "LOADN_ZHIPU_ENGINE"},
                   {"key": "zhipu_base", "label": "API 地址",
                    "value": "https://mirror.example.cn/v4/web_search",
                    "env": "LOADN_ZHIPU_BASE"}],
        "secrets": [{"label": "API Key", "env_key": "Z",
                     "vault_ref": "zhipu_key"}],
    })
    assert cfg.resources.zhipu_engine == "search_pro_sogou"
    assert cfg.resources.zhipu_base == "https://mirror.example.cn/v4/web_search"
    # yaml 持久化（条目 SoT）
    import yaml
    data = yaml.safe_load((tmp / "config.yaml").read_text())
    ent = next(s for s in data["resources"]["custom_services"]
               if s["name"] == "zhipu")
    assert ent["ping"] == ["zhipu"]


def test_non_anchor_key_not_projected(env):
    """自定义字段的 key 不得误投影（仅锚键白名单生效）。"""
    sa, cfg_mod, cfg, tmp = env
    before = cfg.resources.mailboxes        # list 字段，若被投影即类型污染
    sa.put_custom_service({
        "name": "weird", "url": "http://x.example",
        "config": [{"key": "mailboxes", "label": "L", "value": "oops",
                    "env": "LOADN_WEIRD_L"}],
        "secrets": []})
    assert cfg.resources.mailboxes == before
    assert cfg.resources.mailboxes != "oops"


# ---------------- retire（真删语义） ----------------

def test_delete_preset_not_resurrected(env):
    """删除预置卡 → retire 记名 → 再 ensure 不复活。"""
    sa, cfg_mod, cfg, tmp = env
    cfg_mod.ensure_preset_services(cfg)
    sa.delete_custom_service("bocha")
    assert "bocha" not in _names(cfg)
    cfg_mod.ensure_preset_services(cfg)
    assert "bocha" not in _names(cfg)               # 不复活
    assert len(_names(cfg)) == N_PRESETS - 1
    assert "bocha" in (tmp / ".preset_services_retired").read_text()


def test_delete_custom_no_retire_side_effect(env):
    """删除非预置名：retire 名单不记（无害 no-op）。"""
    sa, cfg_mod, cfg, tmp = env
    cfg.resources.custom_services = [{"name": "my_svc", "url": "http://x"}]
    sa.delete_custom_service("my_svc")
    assert not (tmp / ".preset_services_retired").exists()


# ---------------- rename（ID 可改，密钥随行） ----------------

def test_rename_migrates_entry_and_secret(env):
    """rename_from：条目改名 + vault svc: 密钥随行迁移。"""
    sa, cfg_mod, cfg, tmp = env
    from loadn_webui.security import vault as V
    cfg.resources.custom_services = [{"name": "my_svc", "url": "http://x"}]
    V.set_res_secret("svc:my_svc", "top-secret")
    sa.put_custom_service({
        "name": "my_svc2", "title": "改名", "desc": "d", "rename_from": "my_svc",
        "url": "http://x", "config": [], "secrets": [
            {"label": "K", "env_key": "K", "vault_ref": "svc:my_svc2"}]})
    names = _names(cfg)
    assert "my_svc" not in names and "my_svc2" in names
    assert V.get_res_secret("svc:my_svc2") == "top-secret"
    assert V.get_res_secret("svc:my_svc") == ""


def test_rename_preset_retires_old_name(env):
    """预置卡改名：旧名 retire（重启不复活旧卡）、锚字段继续投影。"""
    sa, cfg_mod, cfg, tmp = env
    cfg_mod.ensure_preset_services(cfg)
    sa.put_custom_service({
        "name": "zhipu_search", "title": "智谱搜索", "icon": "search",
        "category": "搜索", "desc": "搜索", "rename_from": "zhipu",
        "ping": ["zhipu"],
        "config": [{"key": "zhipu_engine", "label": "引擎档位",
                    "value": "search_std", "env": "LOADN_ZHIPU_ENGINE"}],
        "secrets": []})
    cfg_mod.ensure_preset_services(cfg)
    names = _names(cfg)
    assert "zhipu" not in names and "zhipu_search" in names
    assert cfg.resources.zhipu_engine == "search_std"   # 锚不受改名影响


# ---------------- 纯凭证条目 / ping / title ----------------

def test_secrets_only_entry_legal(env):
    """仅 secrets 无 url/config 的纯凭证条目合法（v3 放宽）。"""
    sa, cfg_mod, cfg, tmp = env
    out = sa.put_custom_service({
        "name": "pure_creds", "desc": "纯凭证",
        "config": [],
        "secrets": [{"label": "K", "env_key": "K",
                     "vault_ref": "svc:pure_creds"}]})
    assert out["ok"] is True
    ent = next(s for s in cfg.resources.custom_services
               if s["name"] == "pure_creds")
    assert ent["ping"] == ["svc:pure_creds"]            # 默认探测自身


def test_ping_passthrough_in_overview(env):
    """概览视图：ping 透传 + 纯凭证条目不造 endpoint 兜底。"""
    from loadn_webui.api.routes.admin import _svc_overview
    ov = _svc_overview({"name": "captcha", "config": [], "ping": ["twocaptcha"],
                        "secrets": [{"label": "K", "env_key": "K",
                                     "vault_ref": "twocaptcha_key"}]}, {})
    assert ov["ping"] == ["twocaptcha"]
    assert ov["config"] == []                            # 不造兜底
    ov2 = _svc_overview({"name": "legacy", "url": "http://x"}, {})
    assert ov2["ping"] == ["svc:legacy"]                 # 老条目默认
    assert ov2["config"][0]["value"] == "http://x"       # 老条目归一化保留


def test_title_display_name(env):
    """title 显示名：种子补种带中文名；put 可写自由文本。"""
    sa, cfg_mod, cfg, tmp = env
    cfg_mod.ensure_preset_services(cfg)
    zhipu = next(s for s in cfg.resources.custom_services
                 if s["name"] == "zhipu")
    assert zhipu["title"] == "智谱搜索"
    bocha = next(s for s in cfg.resources.custom_services
                 if s["name"] == "bocha")
    assert bocha["title"] == "博查搜索"
    sa.put_custom_service({"name": "my_svc", "title": "我的服务",
                           "url": "http://x", "config": [], "secrets": []})
    ent = next(s for s in cfg.resources.custom_services if s["name"] == "my_svc")
    assert ent["title"] == "我的服务"
