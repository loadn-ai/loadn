"""onboard 引导真机 e2e（pexpect 伪终端）：setup 子命令全流程 + 首跑自动触发 +
幂等 + 零配置快速失败（不裸 Traceback）。"""
import json
import stat
import sys
import tempfile
from pathlib import Path

import pexpect

LOADN = "/data/code/loadn/.venv/bin/loadn"
OK = []

def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (("   " + str(detail)[:150]) if not cond else ""))

def base_env(home: Path) -> dict:
    return {"PATH": "/usr/bin:/bin", "HOME": str(home),
            "LOADN_HOME": str(home / ".loadn"),
            "TERM": "xterm-256color", "LANG": "C.UTF-8"}

with tempfile.TemporaryDirectory() as td:
    td = Path(td); ws = td / "ws"; ws.mkdir()

    # O1: loadn setup 全流程（预设1 GLM：选1 → base_url 回车 → api_key → model 回车）
    home = td / "home"; home.mkdir()
    c = pexpect.spawn(LOADN, ["setup"], encoding="utf-8", timeout=30,
                      env=base_env(home), cwd=str(ws), dimensions=(30, 100))
    c.expect(["选择端点", pexpect.TIMEOUT], timeout=15)
    c.sendline("1")
    c.expect([r"base_url \[https://open\.bigmodel\.cn", pexpect.TIMEOUT], timeout=10)
    c.sendline("")
    c.expect(["api_key", pexpect.TIMEOUT], timeout=10)
    c.sendline("sk-e2e-onboard-1234567890")
    c.expect([r"model \[glm-4\.6\]", pexpect.TIMEOUT], timeout=10)
    c.sendline("")
    c.expect(["已写入", pexpect.TIMEOUT], timeout=10)
    c.expect(pexpect.EOF, timeout=10)
    cfg = home / ".loadn" / "config.json"
    data = json.loads(cfg.read_text()) if cfg.exists() else {}
    check("O1 setup 全流程写 config.json",
          data.get("model") == "glm-4.6"
          and data.get("base_url") == "https://open.bigmodel.cn/api/paas/v4"
          and data.get("provider") == "openai_compat"
          and data.get("api_key") == "sk-e2e-onboard-1234567890", data)
    check("O1b config.json 0600", cfg.exists()
          and stat.S_IMODE(cfg.stat().st_mode) == 0o600)
    check("O1c marker 落盘", (home / ".loadn" / ".onsite_done").exists())

    # O2: 再跑 setup —— 已配置零问询直接返回（幂等）
    c2 = pexpect.spawn(LOADN, ["setup"], encoding="utf-8", timeout=30,
                       env=base_env(home), cwd=str(ws), dimensions=(30, 100))
    c2.expect(["已配置", pexpect.TIMEOUT], timeout=15)
    idx = c2.expect(["选择端点", pexpect.EOF], timeout=8)
    check("O2 二次 setup 零问询", idx == 1, "又出选单=幂等破功")
    c2.expect(pexpect.EOF, timeout=5)

    # O3: 首跑自动触发——全新 HOME 裸交互 loadn 先出引导；跳过后零配置
    #     快速失败给 setup 指引（不再裸 Traceback）
    home2 = td / "home2"; home2.mkdir()
    c3 = pexpect.spawn(LOADN, [], encoding="utf-8", timeout=40,
                       env=base_env(home2), cwd=str(ws), dimensions=(30, 100))
    i = c3.expect(["选择端点", pexpect.TIMEOUT], timeout=25)
    check("O3 首跑自动引导先于一切", i == 0)
    c3.sendline("s")
    j = c3.expect(["未配置模型端点", "Traceback"], timeout=25)
    check("O3b 跳过+零配置=友好提示非 Traceback", j == 0, f"j={j}")
    c3.expect(pexpect.EOF, timeout=10)
    check("O3c 跳过也写 marker", (home2 / ".loadn" / ".onsite_done").exists())

    # O4: 二次裸跑不再引导（marker 幂等）——仍零配置 → 直达快速失败提示
    c4 = pexpect.spawn(LOADN, [], encoding="utf-8", timeout=40,
                       env=base_env(home2), cwd=str(ws), dimensions=(30, 100))
    idx = c4.expect(["选择端点", "未配置模型端点|loadn setup"], timeout=25)
    check("O4 二次裸跑无引导（marker 幂等）", idx == 1, f"idx={idx}")
    c4.expect(pexpect.EOF, timeout=10)

    # O5: 已配置（O1 的 home）裸跑 → 无引导直达 TUI（fake 假端点可渲染）
    env5 = base_env(home); env5["LOADN_PROVIDER"] = "fake"
    c5 = pexpect.spawn(LOADN, [], encoding="utf-8", timeout=40,
                       env=env5, cwd=str(ws), dimensions=(30, 100))
    idx = c5.expect(["选择端点", "发消息|/help|Ctrl-D"], timeout=30)
    check("O5 已配置裸跑直达 TUI", idx == 1, f"idx={idx}")
    k = c5.expect(["loadn setup"], timeout=15)   # TUI 欢迎行带引导指引
    check("O5b TUI 欢迎行提示 loadn setup", k == 0)
    c5.sendcontrol("d")
    c5.expect(pexpect.EOF, timeout=15)

print()
print(f"{'ALL PASS' if all(OK) else 'SOME FAILED'}: {sum(OK)}/{len(OK)}")
sys.exit(0 if all(OK) else 1)
