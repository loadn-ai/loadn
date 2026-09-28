"""md → 自包含 html：内嵌 CSS + 本地图片 base64 内联，零外部依赖单文件。

用 python-markdown（已装， extensions: tables/fenced_code/toc）。

W5.3（v1.1 §14-12：T4 升级——原管线零消毒）：产物内容不可信（可含注入的
脚本/事件属性/外链图片），导出前两道处理：
1. http(s) 外链图片 → 占位符「🖼 外链图片（domain）」——自动加载=IP/Referer
   泄漏；点击确认由预览层经代理（W5.1）。
2. HTML 白名单消毒（标准库 html.parser）：script/style/iframe/object 全剥、
   on* 事件属性全剥、href/src 仅放行 http(s)/data:image/# 相对——
   md 里混入的任意 raw HTML 不得直达输出（A4 用例锁定）。
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import markdown as _md

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: "Noto Sans SC","PingFang SC","Microsoft YaHei","Segoe UI",
       -apple-system, sans-serif; line-height: 1.75; color: #1f2328;
       max-width: 860px; margin: 0 auto; padding: 48px 32px 96px;
       font-size: 16px; background: #fff; }
h1,h2,h3,h4 { line-height: 1.35; font-weight: 650; margin: 1.6em 0 .7em; }
h1 { font-size: 1.9em; border-bottom: 2px solid #d8dee4; padding-bottom: .35em; }
h2 { font-size: 1.45em; border-bottom: 1px solid #eaeef2; padding-bottom: .3em; }
h3 { font-size: 1.2em; } h4 { font-size: 1.05em; }
p { margin: .8em 0; }
a { color: #0969da; text-decoration: none; } a:hover { text-decoration: underline; }
code { font-family: ui-monospace,"JetBrains Mono",Consolas,monospace;
       background: #f6f8fa; padding: .15em .4em; border-radius: 4px; font-size: .9em; }
pre { background: #f6f8fa; padding: 14px 16px; border-radius: 8px; overflow-x: auto;
      line-height: 1.5; }
pre code { background: none; padding: 0; font-size: .88em; }
blockquote { border-left: 4px solid #d0d7de; color: #57606a; margin: 1em 0;
             padding: .1em 1em; background: #f8fafc; }
table { border-collapse: collapse; margin: 1.2em 0; width: 100%; font-size: .93em; }
th,td { border: 1px solid #d8dee4; padding: 7px 12px; text-align: left; }
th { background: #f2f5f8; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
img { max-width: 100%; border-radius: 6px; }
hr { border: none; border-top: 1px solid #e1e4e8; margin: 2.2em 0; }
ul,ol { padding-left: 1.6em; } li { margin: .35em 0; }
@media print { body { padding: 12mm; max-width: none; } }
"""

_EXTENSIONS = ["tables", "fenced_code", "toc", "sane_lists", "smarty"]


def _escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _inline_images(md_text: str, base_dir: Path | None) -> tuple[str, list[str]]:
    """本地图片引用 → data URI（相对 base_dir 解析）；http(s) 不动。返回 (md, 内联清单)。"""
    inlined: list[str] = []

    def repl(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2).strip()
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        if base_dir is None:
            return m.group(0)
        p = (base_dir / src).resolve()
        if not p.is_file():
            return m.group(0)
        ext = p.suffix.lower().lstrip(".")
        ext = {"jpg": "jpeg", "svg": "svg+xml"}.get(ext, ext)
        try:
            b64 = base64.b64encode(p.read_bytes()).decode()
        except OSError:
            return m.group(0)
        inlined.append(src)
        return f"![{alt}](data:image/{ext};base64,{b64})"

    return _IMG_RE.sub(repl, md_text), inlined


# ---------------------------------------------------------------- W5.3

def _strip_remote_images(md_text: str) -> str:
    """http(s) 图片 → 占位符（md 语法形态与 html img 形态各一遍）。"""
    def _ph(src: str) -> str:
        host = re.match(r"https?://([^/\s]+)", src)
        dom = host.group(1) if host else src[:40]
        return f"[🖼 外链图片（{dom}）——已阻断自动加载]"

    md_text = _IMG_RE.sub(
        lambda m: _ph(m.group(2)) if m.group(2).startswith(("http://", "https://"))
        else m.group(0), md_text)
    return re.sub(
        r'<img[^>]*src="(https?://[^"]+)"[^>]*>',
        lambda m: f"<p>{_ph(m.group(1))}</p>", md_text)


_ALLOWED_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
                 "code", "pre", "em", "strong", "blockquote", "a", "table",
                 "thead", "tbody", "tr", "th", "td", "img", "div", "span",
                 "br", "hr", "details", "summary", "sup", "sub"}
_ALLOWED_ATTRS = {"href", "src", "class", "id", "alt", "colspan", "rowspan",
                  "title"}


def _sanitize_html(html: str) -> str:
    """白名单消毒（html.parser，零依赖）。非白名单标签剥壳留内容；on*/未知
    属性删除；href/src 协议白名单（http(s)/data:image/#/相对）。"""
    from html.parser import HTMLParser

    class _Clean(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out: list[str] = []
            self.skip_depth = 0          # script/style/iframe 整段丢

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "iframe", "object", "embed", "form"):
                self.skip_depth += 1
                return
            if self.skip_depth or tag not in _ALLOWED_TAGS:
                return
            keep = []
            for k, v in attrs:
                if k.startswith("on") or k not in _ALLOWED_ATTRS:
                    continue
                if k in ("href", "src") and v:
                    ok = (v.startswith(("http://", "https://", "data:image/",
                                        "#", "mailto:"))
                          or not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", v))
                    if not ok:
                        continue
                keep.append(f'{k}="{v}"')
            self.out.append(f"<{tag}{' ' + ' '.join(keep) if keep else ''}>")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "iframe", "object", "embed", "form"):
                if self.skip_depth:
                    self.skip_depth -= 1
                return
            if self.skip_depth or tag not in _ALLOWED_TAGS:
                return
            self.out.append(f"</{tag}>")

        def handle_data(self, data):
            if not self.skip_depth:
                self.out.append(data)

    c = _Clean()
    c.feed(html)
    c.close()
    return "".join(c.out)


def convert(md_text: str, title: str = "Document",
            base_dir: Path | None = None) -> str:
    md2, _ = _inline_images(md_text, base_dir)
    md2 = _strip_remote_images(md2)
    body = _md.markdown(md2, extensions=_EXTENSIONS)
    body = _sanitize_html(body)
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{_escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n")


def convert_file(src: Path, dst: Path | None = None) -> Path:
    dst = dst or src.with_suffix(".html")
    dst.write_text(convert(src.read_text(errors="replace"),
                           title=src.stem, base_dir=src.parent), encoding="utf-8")
    return dst


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="md → 自包含 html")
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    out = convert_file(Path(a.src), Path(a.out) if a.out else None)
    print(f"ok: {out}")
