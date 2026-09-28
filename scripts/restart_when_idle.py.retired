#!/usr/bin/env python3
"""等平台完全空闲（无 queued/running turn）时安全重启 serve——激活新后端代码。

用法（脱离会话存活）：
  cd /data/code/workdaddy && setsid nohup python3 scripts/restart_when_idle.py \
    >> var/logs/restart_when_idle.log 2>&1 < /dev/null &
  加 --systemd：重启改为交接给系统 workdaddy.service（sudo -n systemctl start；
  2026-09-17 起 serve 的常驻形态是 systemd unit，手动 setsid 实例要让位）。

场景（2026-09-15）：#/cost 成本页需要重启 serve 才有 /api/stats/cost，但还有
活跃 turn 在跑——挂本看护等它自然结束后自动重启+验证，不打扰任何任务。
"""
from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "var" / "workdaddy.db"
PORT = 8792
POLL_S = 15
GIVEUP_S = 26 * 3600   # turn 硬超时 24h + 余量，超时放弃人工处理

SYSTEMD = False   # --systemd 置位：杀手动实例后 systemctl start 接管


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + msg, flush=True)


def active_turns() -> int:
    c = sqlite3.connect(DB, timeout=10)
    try:
        return c.execute(
            "SELECT COUNT(*) FROM turns WHERE status IN ('queued','running')").fetchone()[0]
    finally:
        c.close()


def http(path: str, timeout: float = 5.0):
    """返回状态码（int）或错误串；404 等以 'ERR HTTP ...' 形式返回。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERR HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}"


def serve_pids() -> list[int]:
    """找 `python3 -m workdaddy serve` 进程（cmdline 三段特征，排除本脚本）。"""
    me = os.getpid()
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit() or int(p.name) == me:
            continue
        try:
            parts = (p / "cmdline").read_bytes().decode(errors="replace").split("\0")
        except OSError:
            continue
        if "-m" in parts and "workdaddy" in parts and "serve" in parts:
            out.append(int(p.name))
    return out


def restart() -> bool:
    if active_turns() > 0:            # 临杀前最后一道复查（新 turn 刚起就放弃本轮）
        log("复查发现有活跃 turn，放弃本轮重启")
        return False
    pids = serve_pids()
    log(f"平台空闲：重启 serve（pids={pids}）")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 10      # 优雅退 10s，超时 SIGKILL
    while time.time() < deadline and any(Path(f"/proc/{p}").exists() for p in pids):
        time.sleep(0.1)
    for pid in pids:
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    time.sleep(2)
    if http("/api/health") == 200:    # 端口立刻被占：有别的东西复活了 serve
        st = http("/api/stats/cost?days=1")
        log(f"端口已被新进程接管，health=200 cost={st}")
        return st == 200
    log("重新拉起 serve（setsid 脱离，日志 → var/logs/server.log）")
    if SYSTEMD:
        r = subprocess.run(["sudo", "-n", "systemctl", "start", "workdaddy.service"],
                           capture_output=True, text=True, timeout=60)
        log(f"systemctl start: rc={r.returncode} {r.stderr.strip()[:200]}")
    else:
        with open(ROOT / "var" / "logs" / "server.log", "ab") as lf:
            subprocess.Popen(["python3", "-m", "workdaddy", "serve", "--port", str(PORT)],
                             cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(120):
        if http("/api/health") == 200:
            break
        time.sleep(0.5)
    st = http("/api/stats/cost?days=1")
    log(f"重启完成：health={http('/api/health')} cost={st}")
    return st == 200


def main() -> None:
    log("看护启动：等平台空闲后重启 serve%s"
        % ("（交接给 workdaddy.service）" if SYSTEMD else "（激活 /api/stats/cost）"))
    t0 = time.time()
    while time.time() - t0 < GIVEUP_S:
        if not SYSTEMD and http("/api/stats/cost?days=1") == 200:
            log("端点已可用（serve 已是新代码），退出")
            return
        if active_turns() == 0:
            if restart():
                return
            log("重启验证失败，5 分钟后重试")
            time.sleep(300)
            continue
        time.sleep(POLL_S)
    log("26h 仍有活跃 turn，放弃（人工处理）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--systemd", action="store_true",
                    help="重启改为交接给 workdaddy.service（常驻形态）")
    args = ap.parse_args()
    SYSTEMD = args.systemd
    main()
