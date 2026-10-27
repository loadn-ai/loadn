
def test_skills_unmounted_hint():
    """v0.7.57：库内未挂载 skill 名单注入 constitution——agent 可主动提示
    补挂（生产实证：20261027_1450 会话 deep-person-recon 在库未挂载，
    agent 如实说「没有」）。"""
    from loadn_webui import workspace as ws_mod

    fake = [{"name": "deep-person-recon", "disabled": False},
            {"name": "deep-research", "disabled": False},
            {"name": "x-disabled", "disabled": True}]
    orig = ws_mod.skills_mod.available
    ws_mod.skills_mod.available = lambda: fake
    try:
        h = ws_mod._skills_unmounted_hint(["deep-research"])
        assert "deep-person-recon" in h and "deep-research" not in h
        assert "x-disabled" not in h          # 禁用不进提示
        h2 = ws_mod._skills_unmounted_hint(["deep-research",
                                            "deep-person-recon"])
        assert h2.startswith("无")             # 全挂载=无额外项
    finally:
        ws_mod.skills_mod.available = orig


def test_render_claude_md_carries_unmounted(tmp_path, monkeypatch):
    """渲染端到端：模板占位符被替换（不残留 {{SKILLS_UNMOUNTED}}）。"""
    from types import SimpleNamespace

    from loadn_webui import workspace as ws_mod
    monkeypatch.setattr(ws_mod.skills_mod, "available",
                        lambda: [{"name": "deep-person-recon",
                                  "disabled": False}])
    out = ws_mod.render_claude_md(
        "s1", "t", SimpleNamespace(name="assistant",
                                   render_context=lambda: "ctx"),
        ["file-parse"])
    assert "{{SKILLS_UNMOUNTED}}" not in out
    assert "deep-person-recon" in out
