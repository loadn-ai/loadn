"""md → 自包含 html：内嵌 CSS + 本地图片 base64 内联，零外部依赖单文件。

用 python-markdown（已装，extensions: tables/fenced_code/toc）。
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


def convert(md_text: str, title: str = "Document",
            base_dir: Path | None = None) -> str:
    md2, _ = _inline_images(md_text, base_dir)
    body = _md.markdown(md2, extensions=_EXTENSIONS)
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{title}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
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
