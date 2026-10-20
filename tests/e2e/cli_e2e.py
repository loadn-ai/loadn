import json, subprocess, sys, tempfile, uuid
from pathlib import Path

W = Path("/home/woldy/.loadn-data/workspace/20261010_1040-新任务c0b0/work/cli_e2e")
LOADN = "/data/code/loadn/.venv/bin/loadn"
OK = []

def run(args, fake_dir=None, stdin=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(HOME), "LOADN_PROVIDER": "fake"}
    if fake_dir:
        env["LOADN_FAKE_DIR"] = str(fake_dir)
    return subprocess.run([LOADN, *args], capture_output=True, text=True,
                          timeout=60, env=env, input=stdin, cwd=str(WORK))

def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (("   " + str(detail)[:130]) if not cond else ""))

with tempfile.TemporaryDirectory() as td:
    HOME = Path(td) / "home"; HOME.mkdir()
    WORK = Path(td) / "ws"; WORK.mkdir()
    r = run(["--version"])
    check("C1 --version", r.returncode == 0 and "loadn 0" in r.stdout, r.stderr[:100])
    r = run(["-p", "你好"], fake_dir=W)
    check("C2 -p text 正文", r.returncode == 0 and "这是测试回复" in r.stdout,
          (r.returncode, r.stdout[:60], r.stderr[:150]))
    sid = str(uuid.uuid4())
    r = run(["-p", "--output-format", "stream-json", "--session-id", sid, "你好"], fake_dir=W)
    lines = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    ok3 = bool(lines) and lines[0].get("type") == "system" and lines[0].get("subtype") == "init"
    check("C3 stream-json init", ok3, lines[0] if lines else "no output")
    check("C3b result 终态", any(e.get("type") == "result" for e in lines))
    r = run(["-p", "--output-format", "stream-json", "--model", "glm-test-model-x", "你好"], fake_dir=W)
    first = json.loads(r.stdout.splitlines()[0])
    check("C4 --model 进 init", first.get("model") == "glm-test-model-x", first.get("model"))
    r = run(["-p", "--output-format", "text"], fake_dir=W, stdin="管道输入的问候")
    check("C5 stdin 管道", r.returncode == 0 and "测试回复" in r.stdout, (r.returncode, r.stderr[:150]))
    sid2 = str(uuid.uuid4())
    _ = run(["-p", "--session-id", sid2, "第一轮"], fake_dir=W)
    r2 = run(["-p", "--session-id", sid2, "第二轮"], fake_dir=W)
    check("C6 already-in-use", r2.returncode == 1 and "already in use" in r2.stderr,
          (r2.returncode, r2.stderr[:100]))
    r = run(["-p", "--resume", sid2, "--output-format", "text", "继续"], fake_dir=W)
    check("C7 --resume", r.returncode == 0 and "测试回复" in r.stdout, (r.returncode, r.stderr[:150]))
    TF = Path(td) / "fake_tools"; TF.mkdir()
    (TF / "tools").write_text("[]", encoding="utf-8")
    r = run(["-p", "--output-format", "stream-json", "跑个工具"], fake_dir=TF)
    evs = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    tu = [e for e in evs if e.get("type") == "assistant"
          and any(b.get("type") == "tool_use"
                  for b in (e.get("message") or {}).get("content") or [])]
    tr = [e for e in evs if e.get("type") == "user"]
    check("C8a tool_use 发出", len(tu) >= 1)
    check("C8b tool_result 回喂", len(tr) >= 1)
    check("C8c Bash 真执行 hello-fake", "hello-fake" in r.stdout)
    for sub in ("skills", "memory", "auth"):
        r = run([sub, "--help"])
        check("C9 %s --help" % sub, r.returncode == 0, (r.returncode, r.stderr[:80]))

print()
print("===== CLI e2e: %d/%d passed =====" % (sum(OK), len(OK)))
sys.exit(0 if all(OK) else 1)
