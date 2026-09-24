"""执行沙箱（W2-a 第一步）：bwrap 文件系统隔离，--share-net 过渡。

设计要点（v1.1 §6.3 + 实机验证 2026-09-22）：
- **同路径 bind**：workspace/venv/档案挂载点路径与宿主逐字一致——cwd、
  steer 文件（LOADN_STEER_FILE）、transcript 约定全部不变，根治 PROTOCOL
  §5 双源耦合在路径映射下的断链风险。
- 挂载矩阵（loadn 引擎首版；claude/opencode 见 profile 备注）：
  ro：/usr（+usrmerge symlink /bin /lib /lib64）、/etc、/opt、venv、
      ~/.loadn/config.json
  rw：workspace/<sid>（同路径）、~/.loadn/sessions/<sid>（仅本会话档案）
  不挂：平台 var/（vault.enc/db/audit）、config.yaml、~/.claude、~/.ssh、
      ~/.aws、~/.loadn/sessions/<其他会话>
- env：--clearenv + 白名单逐项 --setenv（复用 W3.3 _spawn_env 语义；
  PYTHONDONTWRITEBYTECODE=1 因 venv ro）。
- 网络：--share-net 过渡（unshare-net 等 W5.1 代理，M1 切换）。
- 安全语义：symlink 先 resolve 再校验相对工作区（出界拒），D1 用例锁定。

上线形态：security.sandbox ∈ config.SANDBOX_TIERS（off 默认双轨 | bwrap 生产 |
vm-bwrap 桌面 VM 执行域 | seatbelt/appcontainer/remote 占位未实现）；resolve_tier()
探测降档 fail-closed + sandbox_tier 审计，doctor 展示解析结果。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

from .config import PATHS
from .util import get_logger

log = get_logger(__name__)


def bwrap_available() -> bool:
    """探测 bwrap 与非特权 user namespace（不可用则 doctor 分级降级）。"""
    exe = shutil.which("bwrap")
    if not exe:
        return False
    import subprocess
    try:
        p = subprocess.run(
            [exe, "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
             "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
             "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
             "--unshare-ipc", "--unshare-pid", "--share-net",
             "--die-with-parent", "/usr/bin/true"],
            capture_output=True, timeout=10)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---- W2 档位解析（A/B/C 三方案归一的枚举层） ----
# 探测/解析进程内缓存：bwrap_available() 真跑一次子进程（10s 超时），不能
# 每 spawn 都探测；sandbox 是重启语义配置（egress 白名单才热更），进程内
# 不会变值 → 按 requested 值缓存解析结果即可。
_probe_cache: bool | None = None
_resolve_cache: dict[str, tuple[str, str]] = {}
_warned: set[str] = set()


def _reset_tier_cache() -> None:
    """测试用：清空探测/解析/告警去重缓存（monkeypatch 环境后须重解析）。"""
    global _probe_cache
    _probe_cache = None
    _resolve_cache.clear()
    _warned.clear()


def resolve_tier(requested: str | None = None) -> tuple[str, str]:
    """档位解析：requested → (effective, reason)。

    统一规则（fail-closed）：探测不到/未实现的档位降为 off，reason 描述
    原因（空串=未降档）。vm-bwrap 执行语义=bwrap（平台运行于 loadn desktop
    置备的 Linux VM 内，requested 值本身即「VM 执行域」的报告标记）；
    seatbelt/appcontainer/remote 是枚举占位（原生轻量档/远程执行器，长期
    可选），未实现 → 降档 off 并审计。
    """
    from .config import CONFIG
    req = requested if requested is not None else CONFIG.security.sandbox
    if req in _resolve_cache:
        return _resolve_cache[req]
    global _probe_cache
    if req == "off":
        out = ("off", "")
    elif req in ("bwrap", "vm-bwrap"):
        if _probe_cache is None:
            _probe_cache = bwrap_available()
        out = (req, "") if _probe_cache else ("off", "bwrap-unavailable")
    elif req == "seatbelt":
        out = ("off", "seatbelt-not-implemented")
    elif req == "appcontainer":
        out = ("off", "appcontainer-not-implemented")
    elif req == "remote":
        out = ("off", "remote-not-implemented")
    else:                              # 防御（load_config 已校验枚举）
        out = ("off", f"unknown-tier:{req}")
    _resolve_cache[req] = out
    return out


def tier_status() -> dict:
    """当前档位三态（/api/health 与安全中心展示用）。"""
    from .config import CONFIG
    req = CONFIG.security.sandbox
    eff, reason = resolve_tier(req)
    return {"requested": req, "effective": eff, "reason": reason}


def resolve_and_audit() -> dict:
    """启动时档位解析 + 审计留痕（app lifespan 调用；降档显著告警）。"""
    from .audit import audit
    st = tier_status()
    audit("sandbox_tier", dict(st))
    if st["reason"]:
        log.warning("沙箱档位降级：%s → %s（%s）——引擎将直跑，本机文件未隔离",
                    st["requested"], st["effective"], st["reason"])
    return st


def _resolve_in(ws: Path, p: Path) -> bool:
    """symlink 出界校验：resolve 后必须仍在 ws 内。"""
    try:
        (ws / p).resolve().relative_to(ws.resolve())
        return True
    except (ValueError, OSError):
        return False


def _project_binds(argv: list[str], project_root: Path | None) -> list[str]:
    """项目子任务的项目根挂载：宪法等 ro（祖先链可读到项目 CLAUDE.md）+
    共享 inputs/ rw。必须在任务目录 bind 之前插入——后挂的父目录会遮住
    已挂子目录的 rw 视图。"""
    if project_root is None:
        return []
    argv += ["--ro-bind", str(project_root), str(project_root)]
    shared = project_root / "inputs"
    if shared.is_dir():
        argv += ["--bind", str(shared), str(shared)]
    return argv


def wrap_loadn(cmd: list[str], env: dict, *, sid_session: str,
               cwd: Path, project_root: Path | None = None,
               owner_sid: str = "") -> list[str] | None:
    """loadn 引擎的 bwrap 包裹。返回完整 argv；环境不可用时返回 None
    （调用方回落直跑并审计 sandbox_violation 计 warn）。

    owner_sid：平台会话 id（审批临时授权的判定边界）——有会话级 socket 时
    bind 之，代理凭 accept 来源按任务放行；sid_session 是引擎会话 id（档案）。
    """
    exe = shutil.which("bwrap")
    if not exe:
        return None
    home = Path.home()
    # 引擎本体定位（与 engines/loadn.py resolve_bin 同序的最小复刻）
    engine_bin = (shutil.which("loadn")
                 or str(Path(sys.executable).parent / "loadn"))
    venv_dir = Path(engine_bin).resolve().parent.parent      # bin/ → venv 根
    # editable 安装指回源码树：包目录必须 ro 挂（不暴露 webui/config）
    pkg_src = venv_dir.parent / "loadn"

    ws = Path(cwd)
    # 档案根 env 感知（测试隔离/部署重定位走 LOADN_HOME；沙箱内同路径 bind）
    engine_home = Path(os.environ.get("LOADN_HOME")
                       or os.environ.get("HAHANESS_HOME") or home / ".loadn")
    archive = engine_home / "sessions" / sid_session

    argv = [exe,
            "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            "--ro-bind-try", "/etc", "/etc",
            "--ro-bind-try", "/opt", "/opt",
            # systemd-resolved stub（/etc/resolv.conf 是指向 /run 的 symlink）
            "--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve",
            "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/tmp",
            # 引擎运行时（venv 只读；pyc 不写）+ 包源码树（editable 指回）。
            # loadn_webui 树也必须挂：PreToolUse hook 子进程（policy-check）
            # 在沙箱内 import loadn_webui——漏挂则 hook 静默失效（E2E 实测教训）
            "--ro-bind", str(venv_dir), str(venv_dir),
            "--ro-bind-try", str(pkg_src), str(pkg_src),
            "--ro-bind-try", str(venv_dir.parent / "loadn_webui"),
            str(venv_dir.parent / "loadn_webui"),
            # workspace 同路径 rw（inputs 子目录 ro 由策略层后续收紧）
            ]
    _project_binds(argv, project_root)
    argv += [
            "--bind", str(ws), str(ws),
            # 本会话引擎档案（resume/判死需要；其他会话不可见）
            ]
    archive.mkdir(parents=True, exist_ok=True)
    argv += ["--bind", str(archive), str(archive)]
    # 取证落盘（provider 400 dump 到 $LOADN_HOME/debug）需要可写
    debug_dir = engine_home / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    argv += ["--bind", str(debug_dir), str(debug_dir)]
    for f in ("config.json", "settings.json"):
        src = engine_home / f
        if src.exists():
            argv += ["--ro-bind", str(src), str(src)]
    # P5：不再挂全局 ~/.claude/settings.json——LLM 走虚拟网关域（spawn
    # env 注入净化 BASE_URL+dummy token，真凭证由代理控制域注入）。
    # P3：物理断网——unshare-net + 唯一出口=挂载的代理 unix socket。
    # 沙箱内 lo up（user ns 内可行）+ socat 桥 TCP:代理端口 → unix socket，
    # 引擎 env 的 https_proxy 指向沙箱内桥（引擎零改动）。
    uds = _egress_uds(owner_sid)
    if uds is not None:
        argv += ["--ro-bind", str(uds), str(uds)]
    argv += [
        "--clearenv",
        "--unshare-net" if uds is not None else "--share-net",
        "--unshare-ipc", "--unshare-pid",
        "--die-with-parent",
    ]
    for k, v in env.items():
        argv += ["--setenv", k, v]
    argv += ["--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    if uds is None:
        return argv + cmd
    proxy_port = env.get("https_proxy", "").rsplit(":", 1)[-1] or "8793"
    boot = (f"ip link set lo up 2>/dev/null; "
            f"socat TCP-LISTEN:{proxy_port},bind=127.0.0.1,fork,reuseaddr "
            f"UNIX-CONNECT:{uds} & "
            f'exec "$@"')
    return argv + ["bash", "-c", boot, "boot", *cmd]


def _egress_uds(sid: str = "") -> Path | None:
    """宿主代理 unix socket 路径（存在才启用断网形态）。

    sid（平台会话 id）有会话级 socket 时优先——审批式临时授权/弹卡确认
    的判定边界；没有则回落共享 socket（仅全局白名单，无任务级放行面）。
    三态档位都走代理（off=直通+审计），故不再按 mode 关闭。
    """
    if sid:
        tag = hashlib.sha1(sid.encode()).hexdigest()[:12]
        per = PATHS["run"] / f"egress-{tag}.sock"
        if per.exists():
            return per
    uds = PATHS["run"] / "egress.sock"
    return uds if uds.exists() else None
    if sid:
        tag = hashlib.sha1(sid.encode()).hexdigest()[:12]
        per = PATHS["run"] / f"egress-{tag}.sock"
        if per.exists():
            return per
    uds = PATHS["run"] / "egress.sock"
    return uds if uds.exists() else None


def _wrap_generic(cmd: list[str], env: dict, *, cwd: Path,
                  extra_binds: list[tuple[str, str, str]],
                  project_root: Path | None = None) -> list[str] | None:
    """通用 bwrap 骨架（W2-a2）：基础系统 ro + workspace rw + 引擎专属 binds。

    extra_binds: [(mode, src, dst)] mode ∈ ro|rw|try-ro
    project_root: 项目子任务的项目根（宪法 ro + 共享 inputs rw；先于 cwd 挂载）
    """
    exe = shutil.which("bwrap")
    if not exe:
        return None
    ws = Path(cwd)
    argv = [exe,
            "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            "--ro-bind-try", "/etc", "/etc",
            "--ro-bind-try", "/opt", "/opt",
            "--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            ]
    _project_binds(argv, project_root)
    argv += [
            "--bind", str(ws), str(ws),
            "--clearenv", "--share-net", "--unshare-ipc", "--unshare-pid",
            "--die-with-parent"]
    for mode, src, dst in extra_binds:
        if not Path(src).exists():
            if mode == "try-ro":
                continue
            return None                  # 必需挂载缺失 → 回落直跑
        flag = {"ro": "--ro-bind", "rw": "--bind",
                "try-ro": "--ro-bind-try"}[mode]
        argv += [flag, str(src), str(dst or src)]
    for k, v in env.items():
        argv += ["--setenv", k, v]
    argv += cmd
    return argv


def wrap_engine(cmd: list[str], env: dict, *, engine: str, sid: str,
                cwd: Path, project_root: Path | None = None,
                owner_sid: str = "") -> tuple[list[str], str]:
    """spawn 入口：按引擎/profile 选包裹。返回 (argv, mode)。

    mode: "bwrap" | "direct"（配置显式 off）| "direct-fallback"（想要隔离
    但档位降级/挂载缺失，audit 留痕由调用方记 sandbox_violation）。
    claude：nvm node 树 ro（CLI 运行时）+ ~/.claude/projects rw（档案）+
    ~/.claude/settings.json ro（网关 env 段=M1 known-gap）+ 全局 CLAUDE.md ro。
    opencode：node 树 ro + ~/.local/share/opencode rw（档案）。
    project_root：项目子任务的项目根（宪法 ro + 共享 inputs rw）。
    owner_sid：平台会话 id（loadn 引擎会话级 egress socket 用）。
    """
    from .config import CONFIG
    tier, reason = resolve_tier()
    if tier == "off":
        if reason and reason not in _warned:
            _warned.add(reason)
            log.warning("沙箱档位 %s 未生效（%s），引擎直跑（文件未隔离，审计记录）",
                        CONFIG.security.sandbox, reason)
        # 想要隔离但降档 → direct-fallback（复用既有 sandbox_violation 遥测
        # 口径）；显式 off 才是诚意的 direct
        return cmd, "direct-fallback" if reason else "direct"
    if engine in ("loadn", "hahaness"):  # hahaness=alias（旧引擎名会话）
        pass                                     # 走 wrap_loadn（同路径 bind 矩阵）
    elif engine == "claude":
        home = Path.home()
        # node 树从引擎 bin 派生（cmd[0] 在 node 树内）——不硬编码版本路径，
        # 任意 nvm/fnm/系统安装位置都成立（开源可移植性）
        node = Path(cmd[0]).absolute().parent.parent
        wrapped = _wrap_generic(
            cmd, env, cwd=cwd, project_root=project_root, extra_binds=[
                ("ro", str(node), str(node)),
                ("rw", str(home / ".claude/projects"), str(home / ".claude/projects")),
                # settings.json 不挂（网关 env 由 spawn 注入；token 零入沙箱）
                ("try-ro", str(home / ".claude/CLAUDE.md"), ""),
                ("try-ro", str(home / ".claude/statsig"), ""),
                ("try-ro", str(home / ".claude/cache"), ""),
            ])
        if wrapped is not None:
            return wrapped, "bwrap"
        return cmd, "direct-fallback"
    elif engine == "opencode":
        home = Path.home()
        node = Path(cmd[0]).absolute().parent.parent   # 同上：从 bin 派生
        oc = home / ".local/share/opencode"
        wrapped = _wrap_generic(
            cmd, env, cwd=cwd, project_root=project_root, extra_binds=[
                ("try-ro", str(node), str(node)),
                ("rw", str(oc), str(oc)),
                ("try-ro", str(home / ".config/opencode"),
                 str(home / ".config/opencode")),   # provider/MCP 配置
            ])
        if wrapped is not None:
            return wrapped, "bwrap"
        return cmd, "direct-fallback"
    else:
        return cmd, "direct"
    # 会话档案 id：cwd 名即 sid（ws_of 约定），引擎档案 id=claude_session_id
    # 由调用方给——这里用 spawn argv 里的 --session-id/--resume 值
    sid_session = sid
    for i, a in enumerate(cmd):
        if a in ("--session-id", "--resume") and i + 1 < len(cmd):
            sid_session = cmd[i + 1]
            break
    wrapped = wrap_loadn(cmd, env, sid_session=sid_session, cwd=Path(cwd),
                         project_root=project_root, owner_sid=owner_sid)
    if wrapped is None:
        log.warning("bwrap 不可用，%s 引擎直跑（sandbox=off 回落，审计记录）", engine)
        return cmd, "direct-fallback"
    return wrapped, "bwrap"
