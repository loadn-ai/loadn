# 若无既有注册表测试文件此段独立成篇——entry-point 第三方引擎挂载回归


def _plant_dist(tmp_path, name, module_src, ep_value):
    """造一个最小 dist-info（entry_points.txt）进 sys.path——不真装包。"""
    pkg = tmp_path / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "engine.py").write_text(module_src)
    di = tmp_path / f"{name}-1.0.dist-info"
    di.mkdir(parents=True, exist_ok=True)
    (di / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\n")
    (di / "entry_points.txt").write_text(
        f"[loadn.webui.engines]\n{name} = {ep_value}\n")
    return tmp_path


def test_entrypoint_engine_registration(tmp_path, monkeypatch):
    src = (
        "from loadn_webui.engines.base import EngineSpec\n"
        "class FakeSpec(EngineSpec):\n"
        "    name = 'fakeep'\n"
        "    def resolve_bin(self):\n"
        "        return '/bin/true'\n"
        "    def build_argv(self, call):\n"
        "        return [], {}\n"
        "def make():\n"
        "    return FakeSpec()\n"
    )
    root = _plant_dist(tmp_path, "fakeep_pkg", src,
                       "fakeep_pkg.engine:make")   # entry 名=包名（可不同名）
    monkeypatch.syspath_prepend(str(root))
    from loadn_webui import engines as eng
    reg: dict = dict(eng.ENGINES)
    loaded = eng.load_entrypoint_engines(reg)
    assert "fakeep_pkg" in loaded
    assert isinstance(reg["fakeep_pkg"], eng.EngineSpec)
    assert reg["fakeep_pkg"].name == "fakeep"


def test_entrypoint_bad_engine_ignored(tmp_path, monkeypatch):
    """坏 entry point（非 EngineSpec/加载异常）只跳过，不炸。"""
    root = _plant_dist(tmp_path, "badep_pkg", "raise RuntimeError('boom')\n",
                       "badep_pkg.engine:nothing")
    monkeypatch.syspath_prepend(str(root))
    from loadn_webui import engines as eng
    reg: dict = {}
    assert eng.load_entrypoint_engines(reg) == []
