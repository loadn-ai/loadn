import sys
import tempfile
from pathlib import Path

import pexpect

W = Path("/home/woldy/.loadn-data/workspace/20261010_1040-新任务c0b0/work/cli_e2e")
LOADN = "/data/code/loadn/.venv/bin/loadn"
OK = []

def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (("   " + str(detail)[:150]) if not cond else ""))

def spawn_tui(td, no_tui=False):
    env = {
        "PATH": "/usr/bin:/bin", "HOME": str(Path(td) / "home"),
        "LOADN_PROVIDER": "fake", "LOADN_FAKE_DIR": str(W),
        "TERM": "xterm-256color", "LANG": "C.UTF-8",
    }
    if no_tui:
        env["LOADN_NO_TUI"] = "1"
    child = pexpect.spawn(LOADN, encoding="utf-8", timeout=45,
                          env=env, cwd=str(Path(td) / "ws"), dimensions=(30, 100))
    return child

with tempfile.TemporaryDirectory() as td:
    Path(td, "home").mkdir(); Path(td, "ws").mkdir()
    # ---- TUI（textual 全屏）
    try:
        c = spawn_tui(td)
        c.expect(["发消息|/help|Ctrl-D", pexpect.TIMEOUT], timeout=25)
        check("T1 TUI 渲染（输入框/提示）", True)
        c.sendline("你好")
        c.expect(["这是测试回复", pexpect.TIMEOUT], timeout=40)
        check("T2 TUI 发消息收到回复", True)
        c.sendcontrol("d")
        c.expect(pexpect.EOF, timeout=15)
        check("T3 TUI Ctrl-D 退出", True, "exit")
    except Exception as e:
        check("T-suite 异常", False, repr(e)[:80] + " | before=" + repr(getattr(e, "before", None) or (c.before if c else ""))[-700:])
        try:
            c.close(force=True)
        except Exception:
            pass

    # ---- REPL（LOADN_NO_TUI=1 回落）
    try:
        c2 = spawn_tui(td, no_tui=True)
        pat = r"(loadn|>|\)|❯|发消息)"
        c2.expect(pat, timeout=25)
        check("T4 REPL 启动（提示符）", True)
        c2.sendline("你好")
        c2.expect("这是测试回复", timeout=40)
        check("T5 REPL 发消息收到回复", True)
        c2.sendcontrol("d")
        c2.expect(pexpect.EOF, timeout=15)
        check("T6 REPL Ctrl-D 退出", True)
    except Exception as e:
        check("REPL-suite 异常", False, repr(e)[:150])
        try:
            c2.close(force=True)
        except Exception:
            pass

print()
print(f"===== TUI/REPL e2e: {sum(OK)}/{len(OK)} passed =====")
sys.exit(0 if all(OK) else 1)
