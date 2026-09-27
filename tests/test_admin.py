"""管理面测试：skills CRUD/编辑/zip+GitHub 安装、skillhub 搜索、MCP CRUD、profile 工具开关。

conftest 把 skills/ profiles/ symlink 到真实仓库；本模块的 fixture 先换成
真实目录拷贝（管理面要写入），teardown 恢复 registry.yaml 原文并清掉测试装的
skill——不污染仓库资产、不影响后续模块。
"""
import io
import json
import os
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml


@pytest.fixture(scope="module", autouse=True)
def writable_assets():
    from loadn_webui.config import PATHS

    home = Path(os.environ["LOADN_WEBUI_HOME"])
    swaps = {}
    for name in ("skills", "profiles"):
        p = home / name
        if p.is_symlink():
            src = Path(os.path.realpath(p))
            swaps[name] = src
            p.unlink()
            shutil.copytree(src, p)
    from loadn_webui.config import behavior_file
    reg = behavior_file("profiles", "registry.yaml")
    reg_orig = reg.read_bytes() if reg.exists() else None
    base_skills = {x.name for x in PATHS["skills"].iterdir()}
    yield
    if reg_orig is not None:
        reg.write_bytes(reg_orig)
    for x in Path(PATHS["skills"]).iterdir():
        if x.name not in base_skills:
            shutil.rmtree(x, ignore_errors=True)


# ---------------------------------------------------------------- skills CRUD
async def test_skill_crud_and_mount(client):
    # 新建
    r = await client.post("/api/skills", json={"name": "crud-test", "description": "测试技能"})
    assert r.status_code == 200, r.text
    # 非法名
    r = await client.post("/api/skills", json={"name": "Bad Name!", "description": "x"})
    assert r.status_code == 400
    # 详情
    r = await client.get("/api/skills/crud-test")
    assert r.status_code == 200
    d = r.json()
    assert d["description"] == "测试技能"
    assert any(f["path"] == "SKILL.md" for f in d["files"])
    # 编辑 SKILL.md
    md = (await client.get("/api/skills/crud-test/file?path=SKILL.md")).json()["content"]
    md = md.replace("description: 测试技能", "description: 改过的描述")
    r = await client.put("/api/skills/crud-test/file", json={"path": "SKILL.md", "content": md})
    assert r.status_code == 200
    mine = [s for s in (await client.get("/api/skills")).json()["skills"] if s["name"] == "crud-test"]
    assert mine and mine[0]["description"] == "改过的描述"
    # 新建/读取/删除普通文件
    assert (await client.post("/api/skills/crud-test/file",
                              json={"path": "scripts/run.py", "content": "print(1)"})).status_code == 200
    assert (await client.get("/api/skills/crud-test/file?path=scripts/run.py")).json()["content"] == "print(1)"
    # 路径穿越拒绝（指向兄弟 skill）
    assert (await client.get("/api/skills/crud-test/file?path=../deep-research/SKILL.md")).status_code == 400
    assert (await client.put("/api/skills/crud-test/file",
                             json={"path": "../evil.md", "content": "x"})).status_code == 400
    assert (await client.delete("/api/skills/crud-test/file?path=scripts/run.py")).status_code == 200
    # 挂载进会话（symlink）
    r = await client.post("/api/sessions", json={"title": "挂载 crud-test", "skills": ["crud-test"]})
    sid = r.json()["session"]["id"]
    from loadn_webui.config import PATHS
    link = PATHS["workspace"] / sid / ".claude" / "skills" / "crud-test"
    assert link.is_symlink() and (link / "SKILL.md").exists()
    # 删除守卫：被会话引用 → 409；force → 200
    assert (await client.delete("/api/skills/crud-test")).status_code == 409
    assert (await client.delete("/api/skills/crud-test?force=true")).status_code == 200
    assert (await client.get("/api/skills/crud-test")).status_code == 404


# ---------------------------------------------------------------- 禁用/启用
async def test_skill_toggle(client):
    from loadn_webui.config import PATHS

    assert (await client.post("/api/skills/toggle-test/toggle",
                              json={"disabled": True})).status_code == 404   # 不存在
    assert (await client.post("/api/skills", json={"name": "toggle-test",
                                                   "description": "开关测试"})).status_code == 200
    r = await client.post("/api/sessions", json={"title": "开关", "skills": ["toggle-test"]})
    sid = r.json()["session"]["id"]
    link = PATHS["workspace"] / sid / ".claude" / "skills" / "toggle-test"
    assert link.is_symlink()
    assert "toggle-test" in (PATHS["workspace"] / sid / "CLAUDE.md").read_text()

    # 禁用：列表标记 + active 会话摘挂 + 宪法不再列它 + 新会话点名也不挂
    r = await client.post("/api/skills/toggle-test/toggle", json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"] is True
    assert any(s["disabled"] for s in (await client.get("/api/skills")).json()["skills"]
               if s["name"] == "toggle-test")
    assert not link.exists()
    assert "toggle-test" not in (PATHS["workspace"] / sid / "CLAUDE.md").read_text()
    r = await client.post("/api/sessions", json={"title": "开关2", "skills": ["toggle-test"]})
    sid2 = r.json()["session"]["id"]
    assert not (PATHS["workspace"] / sid2 / ".claude" / "skills" / "toggle-test").exists()

    # 启用：引用它的 active 会话补挂回来 + 宪法恢复
    assert (await client.post("/api/skills/toggle-test/toggle",
                              json={"disabled": False})).status_code == 200
    assert link.is_symlink()
    assert "toggle-test" in (PATHS["workspace"] / sid / "CLAUDE.md").read_text()

    # 真实 skill 走 profile 默认挂载路径：禁用后默认会话拿不到、别的照挂
    assert (await client.post("/api/skills/wechat-send/toggle",
                              json={"disabled": True})).status_code == 200
    r = await client.post("/api/sessions", json={})
    ws3 = PATHS["workspace"] / r.json()["session"]["id"] / ".claude" / "skills"
    assert (ws3 / "file-parse").is_symlink()
    assert not (ws3 / "wechat-send").exists()
    assert (await client.post("/api/skills/wechat-send/toggle",
                              json={"disabled": False})).status_code == 200


# ---------------------------------------------------------------- 安装
def _tar_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        def add(name: str, data: str):
            b = data.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(b)
            tf.addfile(ti, io.BytesIO(b))
        add("fakerepo-main/skills/tar-skill/SKILL.md",
            "---\nname: tar-skill\ndescription: 从 tarball 装的\n---\n# tar-skill\n")
        add("fakerepo-main/skills/tar-skill/scripts/x.py", "print('x')\n")
        add("fakerepo-main/README.md", "root readme")
    return buf.getvalue()


async def test_install_from_github(client, monkeypatch):
    from loadn_webui import skills as skills_mod
    monkeypatch.setattr(skills_mod, "_download_tarball", lambda o, n, b: _tar_bytes())
    r = await client.post("/api/skills/install",
                          json={"repo": "fake/repo", "subpath": "skills/tar-skill"})
    assert r.status_code == 200, r.text
    assert r.json()["installed"] == ["tar-skill"]
    # 溯源 + 文件落盘
    from loadn_webui.config import PATHS
    d = PATHS["skills"] / "tar-skill"
    assert (d / "scripts" / "x.py").exists()
    src = json.loads((d / (".loadn-source.json")).read_text())
    assert src["repo"] == "fake/repo" and src["via"] == "github"
    # 已存在 → 409
    assert (await client.post("/api/skills/install",
                              json={"repo": "fake/repo", "subpath": "skills/tar-skill"})).status_code == 409
    # subpath 容错：碎片后缀匹配也能定位
    shutil.rmtree(d)
    r = await client.post("/api/skills/install",
                          json={"repo_url": "https://github.com/fake/repo#whatever~tar-skill"})
    assert r.status_code == 200 and r.json()["installed"] == ["tar-skill"]


def test_parse_repo_url():
    from loadn_webui.skills import parse_repo_url
    u = parse_repo_url("https://github.com/NousResearch/hermes-agent#optional-skills~finance~excel-author")
    assert u == {"repo": "NousResearch/hermes-agent", "subpath": "optional-skills/finance/excel-author", "ref": ""}
    u = parse_repo_url("github.com/owner/repo#/skills/a/b")
    assert u["subpath"] == "skills/a/b"
    u = parse_repo_url("https://github.com/anthropics/skills/tree/main/skills/docx")
    assert u == {"repo": "anthropics/skills", "subpath": "skills/docx", "ref": "main"}
    with pytest.raises(ValueError):
        parse_repo_url("https://clawhub.ai/skills/excel-pro")


async def test_install_zip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("zip-skill/SKILL.md", "---\nname: zip-skill\ndescription: zip 装的\n---\n# z\n")
        zf.writestr("zip-skill/scripts/a.sh", "echo a\n")
    r = await client.post("/api/skills/upload", files={"file": ("s.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["installed"] == ["zip-skill"]
    from loadn_webui.config import PATHS
    assert (PATHS["skills"] / "zip-skill" / "scripts" / "a.sh").exists()
    # 恶意 zip（路径穿越）拒绝
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil/SKILL.md", "x")
    r = await client.post("/api/skills/upload", files={"file": ("b.zip", bad.getvalue(), "application/zip")})
    assert r.status_code == 400


# ---------------------------------------------------------------- skillhub
async def test_skillhub_search(client, monkeypatch):
    from loadn_webui.integrations import skillhub
    monkeypatch.setattr(skillhub, "_http_get", lambda url, timeout=10.0: {"skills": [
        {"name": "excel", "slug": "x-excel", "description": "Excel 操作", "category": "development",
         "author": "openclaw", "github_stars": 123, "repo_url": "https://github.com/o/r#skills~excel"},
        {"name": "claw", "slug": "clawhub-x", "description": "非 GH", "category": "dev",
         "author": "a", "github_stars": 0, "repo_url": "https://clawhub.ai/skills/x"},
    ]})
    r = await client.get("/api/skillhub/search?q=excel")
    assert r.status_code == 200
    skills = r.json()["skills"]
    assert len(skills) == 2
    assert skills[0]["installable"] is True
    assert skills[1]["installable"] is False
    # 市场不可达降级
    monkeypatch.setattr(skillhub, "_http_get", lambda url, timeout=10.0: None)
    d = (await client.get("/api/skillhub/search?q=x")).json()
    assert d.get("error") and d["skills"] == []


# ---------------------------------------------------------------- MCP / 工具
async def test_mcp_crud_and_rematerialize(client):
    # 先建一个会话（后续 PUT 全局 server 应重写它的 .mcp.json）
    r = await client.post("/api/sessions", json={"title": "mcp 测试"})
    sid = r.json()["session"]["id"]
    from loadn_webui.config import PATHS
    ws = PATHS["workspace"] / sid
    # 添加 stdio server
    r = await client.put("/api/tools/mcp/test-srv", json={"spec": {
        "type": "stdio", "command": "echo", "args": ["hi"]}})
    assert r.status_code == 200, r.text
    # 校验失败：http 无 url
    assert (await client.put("/api/tools/mcp/bad", json={"spec": {"type": "http"}})).status_code == 400
    # config.yaml 落盘 + 内存生效 + 会话 .mcp.json 重物化
    conf = yaml.safe_load((PATHS["root"] / "config.yaml").read_text())
    assert conf["mcp"]["servers"]["test-srv"]["command"] == "echo"
    mcp = json.loads((ws / ".mcp.json").read_text())
    assert "test-srv" in mcp["mcpServers"]
    # 列表
    servers = {s["name"]: s for s in (await client.get("/api/tools")).json()["servers"]}
    assert "test-srv" in servers
    # 删除（全局清空且会话无覆盖 → .mcp.json 正确地被移除）
    assert (await client.delete("/api/tools/mcp/test-srv")).status_code == 200
    if (ws / ".mcp.json").exists():
        mcp = json.loads((ws / ".mcp.json").read_text())
        assert "test-srv" not in mcp["mcpServers"]
    else:
        assert not (ws / ".mcp.json").exists()


async def test_profile_builtin_tools(client):
    r = await client.put("/api/tools/profile/researcher",
                         json={"disallowed_tools": ["WebSearch", "BogusTool"]})
    assert r.status_code == 200, r.text
    assert r.json()["disallowed_tools"] == ["WebSearch"]      # 白名单外被滤掉
    # registry.yaml 落盘 + 缓存刷新
    profs = {p["name"]: p for p in (await client.get("/api/tools")).json()["profiles"]}
    assert "WebSearch" in profs["researcher"]["disallowed_tools"]
    # 新会话 settings.json 注入 permissions.disallow
    r = await client.post("/api/sessions", json={"title": "工具开关", "profile": "researcher"})
    sid = r.json()["session"]["id"]
    from loadn_webui.config import PATHS
    st = json.loads((PATHS["workspace"] / sid / ".claude" / "settings.json").read_text())
    # AskUserQuestion 是平台级禁用（无头无弹窗），始终在 disallow 首位；
    # mcp__web_reader/mcp__4_5v_mcp 是 Z.AI 网关注入工具的平台级兜底禁用
    assert st["permissions"]["disallow"] == ["AskUserQuestion", "mcp__web_reader",
                                             "mcp__4_5v_mcp", "WebSearch"]
    # 关掉后恢复
    await client.put("/api/tools/profile/researcher", json={"disallowed_tools": []})
    r = await client.post("/api/sessions", json={"title": "工具开关2", "profile": "researcher"})
    sid2 = r.json()["session"]["id"]
    st2 = json.loads((PATHS["workspace"] / sid2 / ".claude" / "settings.json").read_text())
    assert st2["permissions"]["disallow"] == ["AskUserQuestion", "mcp__web_reader",
                                               "mcp__4_5v_mcp"]


# ---------------------------------------------------------------- 收敛度设置
async def test_convergence_roundtrip(client):
    import yaml as _yaml

    from loadn_webui import profile as profile_mod

    # registry.yaml 是 symlink 的真实资产：PUT 直写且不恢复——先存原文，
    # 测试结束原样写回（否则每跑一轮全量测试就把线上超时改成 7200）
    from loadn_webui.config import behavior_file as _bf
    reg_path = _bf("profiles", "registry.yaml")
    orig_text = reg_path.read_text()
    try:
        await _convergence_body(client, _yaml, profile_mod, reg_path)
    finally:
        reg_path.write_text(orig_text)
        profile_mod.reset_cache()


async def _convergence_body(client, _yaml, profile_mod, reg_path):
    # GET：三角色现值（registry.yaml 预置）
    r = await client.get("/api/settings")
    assert r.status_code == 200
    conv = {x["name"]: x for x in r.json()["convergence"]}
    assert conv["researcher"]["timeout_s"] == 86400
    assert conv["researcher"]["max_turns"] is None

    # PUT：改超时 + 设轮次上限 → API 回显 + 落盘 + 内存缓存刷新
    r = await client.put("/api/settings/convergence", json={"profiles": {
        "researcher": {"timeout_s": 7200, "stall_timeout_s": 1800, "max_turns": 80}}})
    assert r.status_code == 200, r.text
    conv = {x["name"]: x for x in r.json()["profiles"]}
    assert conv["researcher"]["timeout_s"] == 7200
    assert conv["researcher"]["max_turns"] == 80
    assert profile_mod.get("researcher").max_turns == 80
    data = _yaml.safe_load(reg_path.read_text())
    assert data["profiles"]["researcher"]["max_turns"] == 80

    # 轮次清空 → None + 键移除
    r = await client.put("/api/settings/convergence", json={"profiles": {
        "researcher": {"max_turns": None}}})
    assert r.status_code == 200
    assert profile_mod.get("researcher").max_turns is None
    data = _yaml.safe_load(reg_path.read_text())
    assert "max_turns" not in data["profiles"]["researcher"]

    # 越界 / 未知角色 → 400
    r = await client.put("/api/settings/convergence", json={"profiles": {
        "researcher": {"timeout_s": 5}}})
    assert r.status_code == 400
    r = await client.put("/api/settings/convergence", json={"profiles": {
        "nope": {"timeout_s": 600}}})
    assert r.status_code == 400
    r = await client.put("/api/settings/convergence", json={})
    assert r.status_code == 400


async def test_skills_translate(client, monkeypatch):
    """中文简介端点：已是中文不送译；英文送译并落 kv 缓存（第二次不再调模型）。"""
    from loadn_webui.integrations import skill_zh
    # 中文描述 → zh=null，不触发模型
    r = await client.post("/api/skills/translate", json={"items": [
        {"name": "file-parse", "description": "把文档解析成文本"}]})
    assert r.status_code == 200
    assert r.json()["items"] == [{"name": "file-parse", "zh": None}]
    # 非法 body → 400
    r = await client.post("/api/skills/translate", json={"items": "x"})
    assert r.status_code == 400

    calls: list[str] = []

    async def fake(name: str, desc: str) -> str:
        calls.append(name)
        return f"{name}：中文简介"

    monkeypatch.setattr(skill_zh, "translate_one", fake)
    body = {"items": [{"name": "msexcel", "description": "Excel wizardry and sorcery."}]}
    r1 = await client.post("/api/skills/translate", json=body)
    assert r1.json()["items"][0]["zh"] == "msexcel：中文简介"
    # 第二次命中 kv 缓存，不再调模型
    r2 = await client.post("/api/skills/translate", json=body)
    assert r2.json()["items"][0]["zh"] == "msexcel：中文简介"
    assert calls == ["msexcel"]
    # 描述变了 → 缓存键变 → 重新送译
    body2 = {"items": [{"name": "msexcel", "description": "Excel wizardry v2."}]}
    r3 = await client.post("/api/skills/translate", json=body2)
    assert r3.json()["items"][0]["zh"] == "msexcel：中文简介"
    assert calls == ["msexcel", "msexcel"]
