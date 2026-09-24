"""loadn auth（P3-5b，pi auth-command 同构）：provider 凭据管理。

子命令：
- ``login <provider>``：getpass 收 token（不回显、不进 shell 历史）；
  ``--base-url`` 可选（订阅型/网关端点——token-plan 形态）
- ``refresh <provider>``：与 login 同语义（重输即刷新；pi 的 refresh 不
  偷偷复用旧 token——交互重输是诚实的刷新）
- ``logout <provider>``：删引擎侧条目；平台 vault 有 loadn-web 即同步删
- ``status``：已配置 provider 一览（token 打码）
- ``check <provider> [--credentials]``：解析当前生效凭据（解析链同
  provider_config）；``--credentials`` 导出 JSON（脚本/CI——等价
  ``pi auth check --credentials``）

存储（验收：挂载面扫描零命中——凭据不出现在任何 workspace/cwd）：
- 引擎侧真源 ``$LOADN_HOME/auth.json``：**0600 + 原子写**（tmp→chmod→
  rename）。HOME 不进沙箱挂载面；信任级与 ~/.claude/settings.json 的
  env 段同级（直跑模式既定先例）——引擎保持 httpx 单依赖，无法用平台
  vault 的 AES-GCM，这是宪法的显式取舍
- 平台 vault 同步（best-effort）：同 venv 装了 loadn-web 即 subprocess
  ``loadn-web r account --platform llm-<provider> --set password=…``
  落加密 vault（AES-256-GCM 真源）；没有 loadn-web 只提示不阻断

解析优先级（provider_config 同链）：config.json > auth.json > env >
~/.claude/settings.json（config.json 是部署显式真源；auth login 是
交互凭据；env 是进程级覆盖）。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from loadn import loadn_home
from loadn.util import get_logger

log = get_logger(__name__)


def auth_path() -> Path:
    return loadn_home() / "auth.json"


def read_auth() -> dict:
    try:
        data = json.loads(auth_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_auth(data: dict) -> None:
    """0600 原子写（tmp → chmod → rename——半写文件不落明文世界可读态）。"""
    p = auth_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, p)


def _mask(tok: str) -> str:
    return f"{tok[:3]}…{tok[-2:]}" if len(tok) > 8 else "****"


# ---------------------------------------------------------------- vault 同步
def _loadn_web_bin() -> str | None:
    found = shutil.which("loadn-web")
    if found:
        return found
    cand = Path(sys.executable).parent / "loadn-web"
    return str(cand) if cand.is_file() else None


def vault_key(provider: str) -> str:
    return f"llm-{provider.strip().lower()}"


def sync_vault(provider: str, token: str) -> bool:
    """best-effort 写平台 vault（密码字段）。失败/缺席=只提示不阻断。"""
    bin_ = _loadn_web_bin()
    if not bin_:
        return False
    try:
        r = subprocess.run(
            [bin_, "r", "account", "--platform", vault_key(provider),
             "--set", f"password={token}"],
            capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("vault 同步失败（凭据仅在引擎侧 auth.json）：%r", e)
        return False


def drop_vault(provider: str) -> bool:
    bin_ = _loadn_web_bin()
    if not bin_:
        return False
    try:
        r = subprocess.run(
            [bin_, "r", "account", "--platform", vault_key(provider),
             "--del"],
            capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------- 子命令
def cmd_login(provider: str, base_url: str, *, refresh: bool = False) -> int:
    provider = provider.strip().lower()
    verb = "刷新" if refresh else "配置"
    try:
        token = getpass.getpass(
            f"{provider} 的 API token（输入不回显）：").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return 1
    if not token:
        print("token 为空——未写入")
        return 1
    data = read_auth()
    data.setdefault("providers", {})[provider] = {
        "auth_token": token,
        **({"base_url": base_url.strip()} if base_url.strip() else {}),
        "updated_at": time.strftime("%F %T"),
    }
    write_auth(data)
    print(f"已{verb} {provider}（{_mask(token)}）→ {auth_path()}（0600）")
    if sync_vault(provider, token):
        print(f"已同步平台 vault（{vault_key(provider)}，AES-256-GCM）")
    else:
        print("（未同步平台 vault：无 loadn-web 或同步失败——凭据仅在"
              "引擎侧 auth.json）")
    return 0


def cmd_logout(provider: str) -> int:
    provider = provider.strip().lower()
    data = read_auth()
    if provider not in data.get("providers", {}):
        print(f"（{provider} 未配置）")
        return 1
    del data["providers"][provider]
    write_auth(data)
    print(f"已删除 {provider}（{auth_path()}）")
    drop_vault(provider)
    return 0


def cmd_status() -> int:
    provs = read_auth().get("providers", {})
    if not provs:
        print("（无已配置 provider。添加：loadn auth login anthropic）")
        return 0
    for name, c in sorted(provs.items()):
        tok = str(c.get("auth_token") or "")
        url = f" · {c['base_url']}" if c.get("base_url") else ""
        print(f"  {name:14} {_mask(tok) if tok else '(无 token)'}"
              f"{url}  （{c.get('updated_at', '?')}）")
    return 0


def cmd_check(provider: str, credentials: bool) -> int:
    """解析当前生效凭据（与 provider_config 同链——auth.json 已接入）。

    --credentials 导出 JSON（auth_token 明文——脚本消费；等价 pi
    auth check --credentials）。
    """
    from loadn.providers import provider_config
    provider = provider.strip().lower()
    cfg = provider_config()
    token = cfg.get("api_key") or ""
    base = cfg.get("base_url") or ""
    if credentials:
        print(json.dumps({"provider": cfg.get("provider") or provider,
                          "auth_token": token,
                          **({"base_url": base} if base else {})},
                         ensure_ascii=False))
        return 0 if token else 1
    if not token:
        print(f"（{provider} 无生效凭据——loadn auth login {provider}）")
        return 1
    print(f"{cfg.get('provider') or provider}: {_mask(token)}"
          + (f" · {base}" if base else ""))
    return 0


# ---------------------------------------------------------------- 入口
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="loadn auth",
                                 description="provider 凭据管理（token 入"
                                 " auth.json 0600 + 平台 vault）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("login", "配置凭据（交互输入 token）"),
                        ("refresh", "刷新凭据（重输 token）")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("provider")
        p.add_argument("--base-url", default="",
                       help="订阅型/网关端点（token-plan 形态）")
    p = sub.add_parser("logout", help="删除凭据")
    p.add_argument("provider")
    sub.add_parser("status", help="已配置一览（token 打码）")
    p = sub.add_parser("check", help="解析当前生效凭据")
    p.add_argument("provider")
    p.add_argument("--credentials", action="store_true",
                   help="导出 JSON（auth_token 明文——脚本/CI 用）")
    args = ap.parse_args(argv)
    if args.cmd == "login":
        return cmd_login(args.provider, args.base_url)
    if args.cmd == "refresh":
        return cmd_login(args.provider, args.base_url, refresh=True)
    if args.cmd == "logout":
        return cmd_logout(args.provider)
    if args.cmd == "status":
        return cmd_status()
    return cmd_check(args.provider, args.credentials)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
