"""测试隔离（承袭 papergo/workdaddy conftest 方法论，monorepo 合并版）。

- 引擎：LOADN_HOME=临时目录（loadn 测试树）
- 平台：LOADN_WEBUI_HOME=临时目录 + symlink 资产（profiles/prompts 真实内容、
  skills 借用原 workdaddy 仓存量资产——monorepo skills/ 只收公开包，私有走
  $LOADN_HOME/skills overlay，测试期用 LOADN_SKILLS_EXTRA 挂原仓）
- LOADN_CLAUDE_BIN=假 CLI；LOADN_FAKE_LOG（旧 WORKDADDY_FAKE_LOG 兼容）记录 argv
- 起**真 uvicorn 线程**（随机端口）：httpx ASGITransport 不透传 StreamingResponse
  的并发 chunk，SSE 必须走真 HTTP；顺带覆盖 uvicorn 行为本身。

env 设置在 conftest 顶层执行——早于任何测试模块 import。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import pytest_asyncio

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FAKE = Path(__file__).resolve().parent / "fake_claude.py"

# 引擎数据根隔离（loadn 包测试用）
_ENGINE_HOME = Path(tempfile.mkdtemp(prefix="loadn_test_home_"))
os.environ.setdefault("LOADN_HOME", str(_ENGINE_HOME))

# 平台根隔离（loadn_webui 测试用）
_HOME = Path(tempfile.mkdtemp(prefix="loadn_webui_test_home_"))
for _name in ("profiles", "prompts"):
    _src = REPO / _name
    if _src.exists():
        (_HOME / _name).symlink_to(_src, target_is_directory=True)
# skills：测试期直连 legacy 资产；CI（无本机资产）回落 monorepo skills/（空）
_LEGACY_SKILLS_LINK = Path(" /home/user/.loadn-data/skills")
_SKILLS_SRC = _LEGACY_SKILLS_LINK if _LEGACY_SKILLS_LINK.exists() else REPO / "skills"
(_HOME / "skills").symlink_to(_SKILLS_SRC, target_is_directory=True)
LEGACY_ASSETS = _LEGACY_SKILLS_LINK.exists()
# skills：monorepo skills/ 仅公开包；测试借用原 workdaddy 仓资产（迁移过渡）
_LEGACY_SKILLS = Path(" /home/user/.loadn-data/skills")
if _LEGACY_SKILLS.exists():
    os.environ["LOADN_SKILLS_EXTRA"] = str(_LEGACY_SKILLS)
os.environ["LOADN_WEBUI_HOME"] = str(_HOME)
os.environ["LOADN_CLAUDE_BIN"] = str(FAKE)
os.environ["WORKDADDY_CLAUDE_BIN"] = str(FAKE)   # fake_claude.py 内部协议仍用旧名
_FAKE_LOG = _HOME / "fake_argv.jsonl"
os.environ["LOADN_FAKE_LOG"] = str(_FAKE_LOG)
os.environ["WORKDADDY_FAKE_LOG"] = str(_FAKE_LOG)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def server_url():
    import uvicorn

    from loadn_webui.api.app import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            import httpx
            httpx.get(f"{url}/api/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        raise RuntimeError("测试服务器未就绪")
    yield url
    server.should_exit = True
    t.join(timeout=10)


@pytest_asyncio.fixture()
async def client(server_url):
    import httpx

    from loadn_webui.config import CONFIG
    # W0：测试服务起来后 lifespan 已生成/加载 token——存量测试统一带双头
    # （token + admin），语义等价于旧的无认证访问；W0 专项测试自建裸 client。
    _tok = CONFIG.server.token
    _headers = {"X-Workdaddy-Token": _tok, "X-Workdaddy-Admin": _tok} if _tok else {}
    async with httpx.AsyncClient(base_url=server_url, timeout=30,
                                 headers=_headers) as c:
        yield c


@pytest.fixture()
def fake_calls():
    def read() -> list[dict]:
        if not _FAKE_LOG.exists():
            return []
        return [json.loads(l) for l in _FAKE_LOG.read_text().splitlines() if l.strip()]
    return read


async def wait_turn(client, sid: str, tid: int, timeout_s: float = 20) -> dict:
    import asyncio
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < timeout_s:
        await asyncio.sleep(0.15)
        resp = await client.get(f"/api/sessions/{sid}")
        turns = resp.json()["turns"]
        t = next((x for x in turns if x["id"] == tid), None)
        if t and t["status"] in ("done", "error", "stopped", "interrupted"):
            return t
    raise TimeoutError(f"turn {tid} 未在 {timeout_s}s 内到终态")


@pytest.fixture()
def ws_root():
    from loadn_webui.config import PATHS
    return PATHS["workspace"]
