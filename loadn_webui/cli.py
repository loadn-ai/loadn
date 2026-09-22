"""CLI：`workdaddy serve` 启动 API 服务；`workdaddy doctor` 自检；`workdaddy r` 资源；
`workdaddy schedule` 定时调度；`workdaddy verify` 凭证巡检。"""
from __future__ import annotations

import argparse
import asyncio


def _build_schedule_parser(sub) -> None:
    p = sub.add_parser("schedule", aliases=["sched"],
                       help="定时调度：到点向会话自动投递消息（冷却/重考/到期巡检）")
    ssub = p.add_subparsers(dest="scmd", required=True)

    p = ssub.add_parser("add", help="新建定时任务")
    p.add_argument("--sid", default="", help="目标会话 id（--kind=new_session 时不需要）")
    p.add_argument("--kind", default="", choices=["", "message", "new_session"],
                   help="message=投递到现有会话（默认）；new_session=到点新建会话")
    p.add_argument("--at", default="", help='绝对时刻（本地时区），如 "2026-09-15 20:30"')
    p.add_argument("--in", dest="in_", default="", help="相对时长，如 90m / 2h / 3d / 1h30m")
    p.add_argument("--cron", default="", help='5 段 cron（本地时区），如 "0 20 * * *"（每天 20:00）')
    p.add_argument("--every", default="", help="递归间隔（如 30m；默认单次触发）")
    p.add_argument("--max-fires", type=int, default=0,
                   help="递归触发次数上限（默认 20；单次任务恒为 1）")
    p.add_argument("--label", default="", help="用途标签（如 FAO 冷却重考）")
    p.add_argument("--prompt", required=True, help="到点投递给会话的指令")
    p.add_argument("--title", default="", help="new_session：新会话标题（默认用 label）")
    p.add_argument("--profile", default="", help="new_session：profile 名（默认 auto 匹配）")
    p.add_argument("--engine", default="", help="new_session：引擎（claude/hahaness/opencode；默认跟随配置）")

    p = ssub.add_parser("list", help="列出定时任务")
    p.add_argument("--sid", default="", help="只看该会话")

    for name, help_ in (("pause", "暂停"), ("resume", "恢复"), ("del", "删除")):
        p = ssub.add_parser(name, help=help_ + "定时任务")
        p.add_argument("id", type=int)


def _parse_duration(s: str) -> int:
    """'90m'/'2h'/'1h30m' → 秒（schedule add --every 用）。"""
    import re
    total = 0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([smhd])", s.lower()):
        total += float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    if total < 60:
        raise ValueError(f"无法解析间隔 {s!r}（最短 60s）")
    return int(total)


def _fmt_secs(s: int) -> str:
    if s % 86400 == 0:
        return f"{s // 86400}d"
    if s % 3600 == 0:
        return f"{s // 3600}h"
    if s % 60 == 0:
        return f"{s // 60}m"
    return f"{s}s"


def _print_jobs(jobs) -> None:
    from .cron import describe
    if not jobs:
        print("（无定时任务）")
        return
    for j in jobs:
        kind = j["kind"] or "message"
        if j["cron"]:
            trig = describe(j["cron"])
        elif j["every_s"]:
            trig = f"每 {_fmt_secs(j['every_s'])}"
        else:
            trig = "单次"
        target = (j["session_id"] or f"新建会话 {j['title'] or j['label'] or ''}"
                  f"{('@' + j['engine']) if j['engine'] else ''}")
        note = f"，下次 {j['due_at']}" if j["status"] == "active" else ""
        print(f"[{j['id']}] {j['status']:6s} {kind} · {target} · {j['label'] or '(无标签)'}"
              f" · {trig} {j['fires']}/{j['max_fires']}{note}")
        print(f"     prompt: {j['prompt'][:100]}")


def _run_schedule(args) -> int:
    from . import db as db_mod
    from .config import ensure_dirs
    from .cron import next_run_iso, parse_cron
    from .scheduler import parse_when
    ensure_dirs()

    if args.scmd == "add":
        kind = args.kind or ("new_session" if not args.sid else "message")
        every_s = None
        cron = None
        max_fires = args.max_fires
        if args.cron:
            try:
                parse_cron(args.cron)
            except ValueError as e:
                print(f"错误: {e}", file=sys.stderr)
                return 1
            cron = args.cron
            due_at = next_run_iso(cron)
            if not max_fires:
                max_fires = 20
        else:
            if not args.at and not args.in_:
                print("错误: 需要 --at / --in / --cron 之一", file=sys.stderr)
                return 1
            due_at = parse_when(at=args.at, in_=args.in_)
        if args.every:
            every_s = _parse_duration(args.every)
            if not max_fires:
                max_fires = 20
        fields: dict = {}
        if kind == "new_session":
            fields = {"kind": "new_session", "session_id": None,
                      "title": args.title or None,
                      "profile": args.profile or None, "engine": args.engine or None}
        else:
            if not args.sid:
                print("错误: message 任务需要 --sid", file=sys.stderr)
                return 1
            with db_mod.conn() as c:
                if not db_mod.get_session(c, args.sid):
                    print(f"错误: 会话不存在 {args.sid}", file=sys.stderr)
                    return 1
            fields = {"kind": "message", "session_id": args.sid}
        with db_mod.conn() as c:
            jid = db_mod.create_job(
                c, label=args.label or None, prompt=args.prompt, due_at=due_at,
                every_s=every_s, cron=cron, max_fires=max_fires or 1, **fields)
            job = db_mod.get_job(c, jid)
        _print_jobs([job])
        return 0

    if args.scmd == "list":
        with db_mod.conn() as c:
            jobs = db_mod.list_jobs(c, args.sid or None)
        _print_jobs(jobs)
        return 0

    with db_mod.conn() as c:
        if args.scmd == "del":
            if not db_mod.delete_job(c, args.id):
                print(f"错误: 任务 {args.id} 不存在", file=sys.stderr)
                return 1
            print(f"已删除任务 {args.id}")
            return 0
        if db_mod.get_job(c, args.id) is None:
            print(f"错误: 任务 {args.id} 不存在", file=sys.stderr)
            return 1
        status = "paused" if args.scmd == "pause" else "active"
        db_mod.update_job(c, args.id, status=status)
        print(f"任务 {args.id} → {status}")
    return 0
import sys


def _build_r_parser(sub) -> None:
    p_r = sub.add_parser("r", aliases=["res"],
                         help="外部资源 CLI（ocr/vlm/search/fetch/sms/mail/captcha/ping）")
    rsub = p_r.add_subparsers(dest="rcmd", required=True)

    p = rsub.add_parser("ping", help="探测全部资源连通性")
    p.add_argument("--only", default="", help="逗号分隔，如 ocr,sandbox")
    p.add_argument("--json", action="store_true")

    p = rsub.add_parser("ocr", help="图片/pdf/office → 文本")
    p.add_argument("file")
    p.add_argument("--vlm", default="false",
                   help="false=RapidOCR 秒级（清晰文本）；auto=VLM 分钟级（扫描件/多栏/手写）")
    p.add_argument("--timeout", type=float, default=None, help="轮询上限秒，默认 vlm300/普通90")
    p.add_argument("--pages", type=int, default=0, help="最多解析页数")
    p.add_argument("--prompt", default="", help="VLM 模式下的额外指令")
    p.add_argument("--extract", default="", help='结构化抽取 JSON，如 \'{"fields":["订单号"]}\'')
    p.add_argument("--out", default="", help="结果写入文件（推荐 notes/xxx.md）")
    p.add_argument("--json", action="store_true")

    p = rsub.add_parser("vlm", help="问视觉模型（本地图片或 URL）")
    p.add_argument("prompt")
    p.add_argument("--img", action="append", default=[], help="图片路径/URL，可多次")
    p.add_argument("--model", default=None)
    p.add_argument("--max-tokens", type=int, default=512)

    p = rsub.add_parser("search", help="网页搜索（默认 auto：智谱优先，博查兜底）")
    p.add_argument("query")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--via", default="auto", choices=["auto", "zhipu", "bocha"],
                   help="auto=有智谱 key 走智谱否则博查")
    p.add_argument("--se", default="", choices=["", "search_std", "search_pro",
                                                "search_pro_sogou", "search_pro_quark"],
                   help="智谱引擎（默认配置 resources.zhipu_engine）")
    p.add_argument("--fresh", default="", help="时间范围，如 oneDay/oneWeek/oneYear")
    p.add_argument("--domain", default="", help="限定域名（智谱，如 www.sohu.com）")
    p.add_argument("--json", action="store_true")

    p = rsub.add_parser("fetch", help="抓网页正文（主内容提取+关键图 OCR，CDP 兜底）")
    p.add_argument("url")
    p.add_argument("--wait", type=float, default=5.0, help="CDP 渲染后额外等待秒数")
    p.add_argument("--force", action="store_true", help="忽略缓存重抓")
    p.add_argument("--proxy", action="store_true", help="走配置的 clash 代理（墙外站）")
    p.add_argument("--ocr", default="auto", choices=["auto", "off", "vlm"],
                   help="auto=主内容图 RapidOCR 秒级（默认）；vlm=扫描件/表格"
                        "（分钟级）；off=不 OCR")
    p.add_argument("--out", default="", help="正文写入文件（推荐 notes/xxx.md）")
    p.add_argument("--json", action="store_true")

    p = rsub.add_parser("sms", help="查询/等待真机短信")
    p.add_argument("--kw", default="", help="按关键字过滤（如 验证码）")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--wait", action="store_true", help="等待匹配 kw 的新短信")
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--json", action="store_true")

    p = rsub.add_parser("mail", help="平台邮箱：最近邮件/等验证码；--to 切换发信")
    p.add_argument("--kw", default="", help="按关键字过滤（如 验证码 / 发件方）")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--wait", action="store_true", help="等待匹配 kw 的新邮件")
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--box", default="",
                   help="选邮箱：地址或唯一子串（如 163/qq/king），默认主邮箱")
    p.add_argument("--folder", default="INBOX",
                   help="IMAP 文件夹（验证码邮件可能进 Junk/垃圾邮件）")
    p.add_argument("--json", action="store_true")
    p.add_argument("--to", default="", help="收件地址（给定即发信模式）")
    p.add_argument("--subject", default="", help="发信主题")
    p.add_argument("--body", default="", help="发信正文")

    p = rsub.add_parser("captcha", help="2captcha 过验证码 → token")
    p.add_argument("--img", default="", help="图形码图片路径")
    p.add_argument("--sitekey", default="", help="reCAPTCHA sitekey")
    p.add_argument("--pageurl", default="", help="reCAPTCHA 所在页 URL")
    p.add_argument("--timeout", type=float, default=180.0)

    p = rsub.add_parser("account", help="账号保险库：平台账号凭证单一真源")
    p.add_argument("--list", action="store_true", help="列出全部账号（密码打码）")
    p.add_argument("--platform", default="", help="平台名（如 google / hubspot）")
    p.add_argument("--field", default="",
                   help="取单字段明文（password/recovery/username/...），供脚本 $(...) 取用")
    p.add_argument("--set", action="append", default=[],
                   metavar="K=V", help="写入字段（可多次，如 --set password=x --set email=y@z）")
    p.add_argument("--del", dest="delete", action="store_true", help="删除该平台条目")
    p.add_argument("--reveal", action="store_true", help="全量视图（密码明文，慎在会话记录留痕）")

    p = rsub.add_parser("browser", help="浏览器通道矩阵（channels.yaml 单一真源）")
    p.add_argument("bcmd", choices=["status", "up", "down", "restart", "snapshot",
                                    "restore", "cdp"],
                    help="status=一览；up/down/restart <通道>；snapshot <通道> <平台>；restore <平台> <通道>")
    p.add_argument("args", nargs="*", help="通道名/平台名（按 bcmd）")

    p = rsub.add_parser("notify", help="发一条运维通知到用户手机（bark/Server酱/TG）")
    p.add_argument("title")
    p.add_argument("--body", default="")

    p = rsub.add_parser("file", help="沙箱↔宿主文件直通道（替代 tmpfiles/base64 中转）")
    fsub = p.add_subparsers(dest="fcmd", required=True)
    p = fsub.add_parser("push", help="把本机文件直传进会话 workspace")
    p.add_argument("path", help="本机文件路径")
    p.add_argument("--sid", default="", help="目标会话（默认 $WORKDADDY_SESSION_ID）")
    p.add_argument("--to", default="artifacts/",
                   help="目标子目录：artifacts/ notes/ work/ inputs/")
    p.add_argument("--api", default="", help="宿主 API 地址（默认本机 8792；容器内传 http://宿主IP:8792）")
    p = fsub.add_parser("pull", help="把会话 workspace 文件拉到本机")
    p.add_argument("path", help="workspace 相对路径（如 artifacts/report.md）")
    p.add_argument("--sid", default="", help="来源会话（默认 $WORKDADDY_SESSION_ID）")
    p.add_argument("--out", default="", help="本机落盘路径（默认当前目录同名文件）")
    p.add_argument("--api", default="", help="宿主 API 地址（默认本机 8792）")


async def _run_r(args) -> int:
    import json as json_mod

    from . import policy as policy_mod
    from . import resources

    # W1-a 执行点 B：资源 CLI 统一网关（L0 红线 + 网络类目标域；策略在模型之外）
    gw = policy_mod.cli_gateway([str(a) for a in sys.argv[1:]], args.rcmd)
    if not gw.ok:
        print(f"✗ 策略拦截：{gw}", file=sys.stderr)
        return 2

    if args.rcmd == "ping":
        only = [x for x in args.only.split(",") if x] or None
        res = await resources.ping_all(only)
        if args.json:
            print(json_mod.dumps(res, ensure_ascii=False, indent=2))
        else:
            for name, r in res.items():
                ms = f" {r.get('ms')}ms" if "ms" in r else ""
                print(f"{name:14s} {'OK  ' if r['ok'] else 'FAIL'}{ms} {r.get('msg', '')}")
        return 0 if all(r["ok"] for r in res.values()) else 1

    if args.rcmd == "ocr":
        out = await resources.ocr_parse(
            args.file, vlm=args.vlm, max_pages=args.pages,
            prompt=args.prompt, extract=args.extract, poll_timeout=args.timeout)
        if args.json:
            print(json_mod.dumps(out, ensure_ascii=False))
        elif args.out:
            from pathlib import Path
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(out["text"])
            print(f"[{out['method']}] {out['time_s']}s → {args.out}（{len(out['text'])} 字符）")
        else:
            print(f"[{out['method']}] {out['time_s']}s")
            print(out["text"])
        return 0

    if args.rcmd == "vlm":
        print(await resources.vlm_ask(args.img, args.prompt,
                                      model=args.model, max_tokens=args.max_tokens))
        return 0

    if args.rcmd == "search":
        from .config import CONFIG
        via = args.via
        if via == "auto":
            via = "zhipu" if CONFIG.resources.zhipu_key else "bocha"
        if via == "zhipu":
            hits = await resources.zhipu_search(
                args.query, n=args.n, engine=args.se or None,
                recency=args.fresh or None, domain=args.domain or None)
        else:
            if args.se or args.domain:
                print("注意: --se/--domain 仅智谱生效，已忽略", file=sys.stderr)
            hits = await resources.bocha_search(args.query, n=args.n,
                                                freshness=args.fresh or None)
        if args.json:
            print(json_mod.dumps(hits, ensure_ascii=False, indent=2))
        else:
            for i, h in enumerate(hits, 1):
                print(f"{i}. {h['name']}")
                print(f"   {h['url']}")
                if h["snippet"]:
                    print(f"   {h['snippet'][:160]}")
        return 0

    if args.rcmd == "fetch":
        out = await resources.fetch_page(args.url, force=args.force, wait=args.wait,
                                         proxy=args.proxy, ocr=args.ocr)
        if args.out:
            from pathlib import Path
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(out["text"], encoding="utf-8")
            out = {**out, "out": args.out}
        if args.json:
            print(json_mod.dumps(out, ensure_ascii=False, indent=2))
        else:
            cached = "（缓存）" if out.get("cached") else ""
            imgs = f" · 图 OCR×{len(out.get('images') or [])}" if out.get("images") else ""
            print(f"[{out['method']}] {out['title']} · {out['chars']} 字符{imgs}{cached}"
                  f" → {out.get('out') or out['path']}")
            print(out["text"][:800])
        return 0

    if args.rcmd == "sms":
        if args.wait:
            it = await resources.sms_wait(args.kw, timeout_s=args.timeout)
            if args.json:
                print(json_mod.dumps(it, ensure_ascii=False))
            else:
                print(f"{it.get('receive_time')} {it.get('sender')}\n{it.get('body')}")
            return 0
        d = await resources.sms_recent(args.kw, n=args.n)
        items = d.get("items") or []
        if args.json:
            print(json_mod.dumps(d, ensure_ascii=False, indent=2))
        else:
            for it in items:
                print(f"{it.get('receive_time')} [{it.get('sender')}] {it.get('body')}")
        return 0

    if args.rcmd == "mail":
        def _print_mail(it: dict) -> None:
            codes = f" · codes={it['codes']}" if it.get("codes") else ""
            print(f"{it.get('receive_time')} [{it.get('from')}] "
                  f"{it.get('subject')}{codes}")
            print(f"    {(it.get('body') or '')[:400]}")
            for u in it.get("links") or []:
                print(f"    link: {u}")
        if args.to:
            out = await resources.mail_send(args.to, subject=args.subject,
                                            body=args.body, box=args.box)
            print(json_mod.dumps(out, ensure_ascii=False) if args.json
                  else f"已发送 {out['from']} → {out['to']}")
            return 0
        if args.wait:
            it = await resources.mail_wait(args.kw, timeout_s=args.timeout,
                                           box=args.box, folder=args.folder)
            if args.json:
                print(json_mod.dumps(it, ensure_ascii=False))
            else:
                _print_mail(it)
            return 0
        d = await resources.mail_recent(args.kw, n=args.n, box=args.box,
                                        folder=args.folder)
        if args.json:
            print(json_mod.dumps(d, ensure_ascii=False, indent=2))
        else:
            for it in d.get("items") or []:
                _print_mail(it)
        return 0

    if args.rcmd == "captcha":
        token = await resources.captcha_solve(
            image=args.img or None, sitekey=args.sitekey, pageurl=args.pageurl,
            timeout_s=args.timeout)
        print(token)
        return 0

    if args.rcmd == "notify":
        from . import notify
        ok = await notify.send(args.title, args.body)
        print("已推送" if ok else "未推送（未配置 provider 或事件关闭；看 config notify 段）")
        return 0 if ok else 1

    if args.rcmd == "browser":
        import subprocess as sp
        from pathlib import Path as PathMod
        script = PathMod(__file__).resolve().parent.parent / "skills" / \
            "browser-stack" / "channels.py"
        if not script.exists():
            print(f"错误: {script} 不存在", file=sys.stderr)
            return 1
        return sp.run([sys.executable, str(script), args.bcmd, *args.args]).returncode

    if args.rcmd == "file":
        import os as os_mod
        from pathlib import Path as PathMod

        from .config import CONFIG
        sid = args.sid or os_mod.environ.get("WORKDADDY_SESSION_ID", "")
        if not sid:
            print("错误: 缺少 --sid（或设 WORKDADDY_SESSION_ID）", file=sys.stderr)
            return 1
        api = args.api or f"http://127.0.0.1:{CONFIG.server.port}"
        # 本机直连用配置 token；容器/远程场景经 WORKDADDY_API_TOKEN 显式注入
        tok = CONFIG.server.token or os_mod.environ.get("WORKDADDY_API_TOKEN", "")
        import httpx
        if args.fcmd == "push":
            p = PathMod(args.path)
            if not p.exists():
                print(f"错误: 文件不存在 {p}", file=sys.stderr)
                return 1
            with open(p, "rb") as f:
                r = httpx.post(f"{api}/api/sessions/{sid}/ingest",
                               params={"to": args.to, **({"token": tok} if tok else {})},
                               files={"file": (p.name, f)}, timeout=300.0)
            if r.status_code != 200:
                print(f"错误: 直传失败 {r.status_code} {r.text[:200]}", file=sys.stderr)
                return 1
            d = r.json()
            print(f"已直传 {d['path']}（{d['kb']}KB）")
            return 0
        out = PathMod(args.out or PathMod(args.path).name)
        r = httpx.get(f"{api}/api/sessions/{sid}/file",
                      params={"path": args.path, "raw": "true",
                              **({"token": tok} if tok else {})}, timeout=300.0)
        if r.status_code != 200:
            print(f"错误: 拉取失败 {r.status_code} {r.text[:200]}", file=sys.stderr)
            return 1
        out.write_bytes(r.content)
        print(f"已拉取 → {out}（{len(r.content) // 1024}KB）")
        return 0

    if args.rcmd == "account":
        from . import vault
        if args.list or not args.platform:
            rows = vault.list_platforms()
            if not rows:
                print("（保险库为空。写入：wd r account --platform x --set password=…）")
                return 0
            for r in rows:
                pwd = "有密码" if r["has_password"] else "无密码"
                st = f" · {r['status']}" if r["status"] else ""
                print(f"{r['platform']:20s} {r['username'] or '(未记用户名)':30s} "
                      f"[{pwd}]{st}")
            return 0
        if args.delete:
            print("已删除" if vault.delete(args.platform) else "条目不存在")
            return 0 if vault.get(args.platform) is None else 1
        if args.set:
            fields = {}
            for kv in args.set:
                k, _, v = kv.partition("=")
                fields[k.strip()] = v
            vault.put(args.platform, **fields)
            print(f"已写入 {args.platform}（{', '.join(sorted(fields))}）")
            return 0
        if args.field:
            v = vault.field(args.platform, args.field)
            if v is None:
                print(f"错误: {args.platform}.{args.field} 不存在", file=sys.stderr)
                return 1
            print(v)   # 单值输出：脚本 $(wd r account --platform x --field password)
            return 0
        d = vault.view(args.platform, reveal=args.reveal)
        if d is None:
            print(f"错误: 无 {args.platform} 条目", file=sys.stderr)
            return 1
        print(json_mod.dumps(d, ensure_ascii=False, indent=2))
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loadn-web")
    sub = ap.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="启动 API 服务")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)

    sub.add_parser("doctor", help="环境自检")
    sub.add_parser("engines", help="引擎清单与状态（bin/version/能力位）")
    sub.add_parser("scan", help="重扫全部会话产物")
    p_bf = sub.add_parser("backfill-costs", help="回填历史 turns.models_json（var/logs/calls）")
    p_bf.add_argument("--force", action="store_true", help="覆盖已回填的 turns")
    _build_schedule_parser(sub)
    _build_r_parser(sub)

    p_v = sub.add_parser("verify", help="凭证资产巡检：验证 URL 回访 + 到期预警")
    p_v.add_argument("--sid", required=True, help="会话 id（读其 artifacts/credentials.json）")
    p_v.add_argument("--days", type=int, default=30, help="到期预警窗口（默认 30 天）")
    p_v.add_argument("--proxy", action="store_true", help="验证页走 clash 代理")
    p_v.add_argument("--offline", action="store_true", help="只查到期/schema，不回访")
    p_v.add_argument("--out", default="", help="报告落盘（默认 notes/verify-report.md）")

    p_t = sub.add_parser("token", help="API token 管理（W0）")
    p_t.add_argument("action", choices=["show", "rotate"])
    sub.add_parser("policy-check",
                   help="策略钩子执行体：stdin 传 {tool_name,tool_input}，"
                        "exit 2=block（W1 执行点 A 物化用）")
    p_aud = sub.add_parser("audit", help="审计账本（W6.1 最小版）")
    p_aud.add_argument("action", choices=["tail"])
    p_aud.add_argument("-n", type=int, default=20)
    p_aud.add_argument("--type", default="")

    args = ap.parse_args(argv)
    if args.cmd == "token":
        import time as time_mod

        from .config import CONFIG, PATHS, resolve_runtime_token
        had_config_token = bool(CONFIG.server.token)   # resolve 前判定来源
        token = resolve_runtime_token()
        grace_until = CONFIG.server.token_grace_until
        src = "config.yaml server.token" if had_config_token else "var/server_token"
        if args.action == "show":
            print(f"token:   {token}")
            print(f"来源:    {src}（0600）")
            if grace_until > 0:
                left = max(0.0, (grace_until - time_mod.time()) / 86400)
                print(f"宽限期:  剩 {left:.1f} 天（到期后未认证请求 401）")
            else:
                print("宽限期:  无（立即强制认证）")
            print("通道:    Authorization: Bearer / X-Workdaddy-Token 头 / ?token=（兼容）"
                  " / SSE ?ticket=")
        else:
            import secrets as secrets_mod
            p = PATHS["var"] / "server_token"
            new = secrets_mod.token_urlsafe(32)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(new + "\n")
            import os as os_mod
            os_mod.chmod(tmp, 0o600)
            tmp.replace(p)
            print(f"已轮换 → {p}")
            print("重启服务生效：sudo systemctl restart workdaddy")
        return 0
    if args.cmd == "policy-check":
        from .policy import policy_check_hook
        return policy_check_hook(sys.stdin.read())
    if args.cmd == "audit":
        from . import audit as audit_mod
        for row in audit_mod.tail(args.n, args.type or None):
            print(f"{row['id']:>5} {row['ts']} {row['type']:20s} "
                  f"{row['detail_json'][:110]}")
        return 0
    if args.cmd == "serve":
        import uvicorn

        from .api.app import app
        from .config import CONFIG, ensure_dirs
        ensure_dirs()
        uvicorn.run(app, host=args.host or CONFIG.server.host,
                    port=args.port or CONFIG.server.port, log_level="info")
        return 0
    if args.cmd == "engines":
        from . import engines as engines_mod
        print(f"default: {engines_mod.default_engine()}")
        for name, spec in engines_mod.ENGINES.items():
            h = spec.health()
            marks = []
            if not spec.max_turns_flag:
                marks.append("无 max-turns flag")
            if not spec.supports_transcript:
                marks.append("判死仅 stdout+硬超时")
            extra = f"（{'；'.join(marks)}）" if marks else ""
            print(f"{name:10s} {'ok  ' if h['ok'] else 'FAIL'} {h['version'][:40]:42s} "
                  f"{h['bin']} {extra}")
        return 0
    if args.cmd == "doctor":
        from . import engines as engines_mod
        from .config import PATHS
        ok = True
        print(f"root:      {PATHS['root']}")
        print(f"db:        {PATHS['db']}")
        for name, spec in engines_mod.ENGINES.items():
            h = spec.health()
            print(f"{name + ':':10s}{h['bin']}")
            print(f"{'version:':10s}{h['version'][:60] if h['ok'] else 'FAILED (' + str(h.get('error', 'unavailable')) + ')'}")
            if name == engines_mod.default_engine() and not h["ok"]:
                ok = False   # 只有默认引擎坏了才翻 FAIL（可选引擎只提示）
        import fastapi  # noqa: F401
        import uvicorn
        print("fastapi:   ok")
        import docx  # noqa: F401
        print("exporter:  ok (python-docx + markdown)")
        for k in ("profiles", "prompts", "skills"):
            p = PATHS[k]
            print(f"{k + ':':10s}{p} {'ok' if p.exists() else 'MISSING'}")
            if not p.exists():
                ok = False
        # 外部资源只作提示，不翻 PASS/FAIL（都是可选服务）
        print("— 外部资源（可选，不影响判定）—")
        try:
            from . import resources
            for name, r in asyncio.run(resources.ping_all()).items():
                ms = f" {r.get('ms')}ms" if "ms" in r else ""
                print(f"  {name:13s} {'ok ' if r['ok'] else 'FAIL'}{ms} {r.get('msg', '')}")
        except Exception as e:  # noqa: BLE001
            print(f"  资源探测异常: {e}")
        print("result:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    if args.cmd == "scan":
        from . import artifacts as art
        from . import db as db_mod
        from . import profile as profile_mod
        from . import workspace as ws_mod
        from .config import PATHS, ensure_dirs
        ensure_dirs()
        registry = profile_mod.load_registry()
        with db_mod.conn() as c:
            rows = db_mod.list_sessions(c, include_archived=False)
        for row in rows:
            n = art.scan_session(row["id"])
            prof = registry.get(row["profile"])
            if prof is not None and not row["project_id"]:
                # 回填 env/settings（禁用工具）与宪法——项目子任务跳过：
                # 共享宪法属项目，子任务改写会换掉兄弟正在用的文件
                ws_mod.write_settings(ws_mod.ws_of(row["id"]), row["id"], prof)
                ws_mod.rerender(row["id"])
            print(f"{row['id']}: {n} artifacts")
            for p in art.ledger_split_check(row["id"]):
                print(f"  ⚠️ {p}")
        return 0
    if args.cmd == "backfill-costs":
        from . import backfill
        from .config import ensure_dirs
        ensure_dirs()
        stats = backfill.run(force=args.force)
        print(f"扫描 {stats['scanned']} 个 .out（含 modelUsage {stats['with_model_usage']}）"
              f"→ 回填 {stats['backfilled']}，已存在跳过 {stats['skipped_existing']}，"
              f"孤儿 {stats['orphan_files']}")
        return 0
    if args.cmd == "verify":
        from . import verify as verify_mod
        from .config import PATHS, ensure_dirs
        ensure_dirs()
        out = args.out or str(ws_mod.ws_of(args.sid) / "notes" / "verify-report.md")
        res = asyncio.run(verify_mod.run(
            args.sid, warn_days=args.days, proxy=args.proxy,
            online=not args.offline, out_path=out))
        print(res["report"])
        bad = len(res["schema_problems"]) + sum(
            1 for r in res["rows"]
            if (r["online"] is not None and not r["online"]["ok"]) or r["expiry"])
        print(f"\n巡检 {res['entries']} 条凭证（问题 {bad} 项）→ 报告 {out}")
        print("周期巡检：wd schedule add --sid " + args.sid +
              " --in 7d --every 7d --max-fires 100 --label 到期巡检"
              " --prompt \"跑 verify 巡检并汇报\"")
        return 0 if bad == 0 else 1
    if args.cmd in ("schedule", "sched"):
        try:
            return _run_schedule(args)
        except Exception as e:  # noqa: BLE001
            print(f"错误: {e}", file=sys.stderr)
            return 1
    if args.cmd in ("r", "res"):
        try:
            return asyncio.run(_run_r(args))
        except Exception as e:  # noqa: BLE001
            print(f"错误: {e}", file=sys.stderr)
            return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
