#!/usr/bin/env python3
"""网页抓取兜底（agent 专用）：走本机 CDP 真实浏览器（vendor 自前身项目，契约不变）。

用法: fetch_page.py <url> [--wait 5] [--out <path>] [--html-out <path>]

WebFetch 被反爬拦截/内容截断时使用。结果 markdown 默认落
$WORKDADDY_HOME/var/pages_cache/<sha1>.md，stdout 打印 JSON（ok/path/title/chars，
缓存命中带 cached:true）。--html-out 额外落渲染后完整 HTML（resources.fetch_page
的 CDP 兜底用它再做主内容提取）。CDP 不可用时返回明确错误（agent 应回退
WebFetch 或放弃该来源，不要硬编）。

playwright 所在解释器与本脚本 shebang 不同时，设 LOADN_FETCH_PYTHON 指向
该解释器（缺失 playwright 且已设此 env 时自动重执行；缓存命中路径无需
playwright，任何解释器可跑）。
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    import playwright  # noqa: F401
except ImportError:
    _py = os.environ.get("LOADN_FETCH_PYTHON")
    if _py and Path(_py).exists() and _py != sys.executable:
        os.execv(_py, [_py, *sys.argv])

CDP_URL_DEFAULT = os.environ.get("WORKDADDY_CDP_URL", "http://127.0.0.1:9222")
# 沙箱 CDP（/cdp）需要 Bearer；本机 9222 不需要——env 留空即无认证
CDP_TOKEN = os.environ.get("WORKDADDY_CDP_TOKEN", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--wait", type=float, default=5.0)
    ap.add_argument("--out")
    ap.add_argument("--html-out", dest="html_out",
                    help="渲染后 outerHTML 落盘路径（resources.fetch_page 用）")
    args = ap.parse_args()

    home = Path(os.environ.get("WORKDADDY_HOME", Path(__file__).resolve().parent.parent))
    cache_dir = home / "var" / "pages_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(args.url.encode()).hexdigest()[:16]
    out = Path(args.out) if args.out else cache_dir / f"{key}.md"
    if out.exists() and out.stat().st_size > 500:
        print(json.dumps({"ok": True, "path": str(out), "cached": True}))
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(json.dumps({"ok": False, "reason": "playwright not installed in venv"}))
        return 2

    try:
        with sync_playwright() as p:
            kw = {"headers": {"Authorization": f"Bearer {CDP_TOKEN}"}} if CDP_TOKEN else {}
            browser = p.chromium.connect_over_cdp(CDP_URL_DEFAULT, **kw)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(int(args.wait * 1000))
            if args.html_out:
                html = page.evaluate(
                    "() => document.documentElement.outerHTML")
                Path(args.html_out).write_text(html, encoding="utf-8")
            # 尽量移除噪音节点再取正文
            page.evaluate(
                "document.querySelectorAll('script,style,noscript,svg,header nav')"
                ".forEach(e=>e.remove())")
            text = page.evaluate(
                "() => (document.body ? document.body.innerText : '')")
            title = page.title()
            page.close()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"sandbox_cdp_error: {e}",
                          "hint": "检查 AIO sandbox 是否运行 (docker ps | grep sandbox)"}))
        return 3

    md = f"# {title}\n\nSource: {args.url}\n\n{text}"
    out.write_text(md, encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(out), "title": title,
                      "chars": len(text)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
