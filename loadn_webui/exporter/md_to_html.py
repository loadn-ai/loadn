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
       max-width: min(1180px, 94vw); margin: 0 auto; padding: 48px 32px 96px;
       font-size: 16px; background: #fff; }
/* 宽度自适应（v0.7.64）：小屏跟随视口、大屏 1180px 封顶不空旷；
   带 TOC 两栏时正文让位侧栏后仍保有阅读宽度 */
body.has-toc { max-width: min(1480px, 96vw); display: flex; gap: 24px;
                align-items: flex-start; padding: 32px 3vw 96px; }
body.has-toc > main { flex: 1 1 0; min-width: 0; max-width: min(1120px, 100%); margin: 0 auto; }
.doc-toc { position: sticky; top: 24px; flex: 0 0 218px; align-self: flex-start;
           max-height: calc(100vh - 48px); overflow-y: auto;
           border: 1px solid #e5e7eb; border-radius: 10px;
           background: #f8fafc; padding: 12px 10px; font-size: 13px; }
.doc-toc-head { font-weight: 650; letter-spacing: .04em; padding: 2px 8px 8px; color: #1f2328; }
.doc-toc a { display: block; padding: 3px 8px; border-radius: 6px; color: #1f2328;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.doc-toc a.lv2 { padding-left: 18px; } .doc-toc a.lv3 { padding-left: 30px; }
.doc-toc a.lv4 { padding-left: 42px; color: #6b7280; }
.doc-toc a:hover { background: rgba(9,105,218,.08); text-decoration: none; }
.doc-toc a:target, .doc-toc a:focus { background: rgba(9,105,218,.14); }
@media (max-width: 900px) {
  body.has-toc { display: block; padding: 24px 16px 80px; }
  .doc-toc { position: static; max-height: none; margin-bottom: 18px; }
}
h1,h2,h3,h4 { line-height: 1.35; font-weight: 650; margin: 1.6em 0 .7em;
               scroll-margin-top: 16px; }
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


def _inject_toc(body: str) -> tuple[str, str]:
    """消毒后后处理（v0.7.64）：按序给 h1-h4 注入 id + 生成左侧静态 TOC。

    零 JS（分享页 CSP sandbox 禁脚本）：锚点跳转原生+sticky CSS；
    标题 <4 个不生成（与前端 MdToc 阈值一致）。返回 (正文, toc_html)——
    toc_html 空串=无 TOC。注入在 _sanitize_html 之后，不受白名单影响。
    """
    heads: list[tuple[int, str]] = []   # (level, 纯文本)
    idx = 0
    # 兼容带属性形态（toc extension 已注入 id="1" 等数字锚）——统一重写
    tag_re = re.compile(r"<h([1-4])([^>]*)>(.*?)</h\1>", re.S)

    def _repl(m: re.Match) -> str:
        nonlocal idx
        lvl, inner = int(m.group(1)), m.group(3)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        hid = f"sec-{idx}"
        heads.append((lvl, text))
        idx += 1
        return f'<h{lvl} id="{hid}">{inner}</h{lvl}>'

    body = tag_re.sub(_repl, body)
    if len(heads) < 4:
        return body, ""
    items = "\n".join(
        f'<a class="lv{lvl}" href="#sec-{i}" title="{_escape(t)}">{_escape(t)}</a>'
        for i, (lvl, t) in enumerate(heads))
    toc = (f'<aside class="doc-toc" aria-label="目录">'
           f'<div class="doc-toc-head">目录 · {len(heads)} 节</div>\n{items}\n</aside>')
    return body, toc


_MERMAID_BOOT = """
<script src="/assets/mermaid.min.js"></script>
<script>
// 预览变体（v0.7.64）：```mermaid 块渲染成流程图 + CJK 裁字高度补偿
// （与前端 Mermaid.tsx 同语义；分享页不注入——CSP sandbox 禁脚本）。
window.addEventListener('load', async () => {
  try {
    await mermaid.initialize({ startOnLoad: false, securityLevel: 'strict',
                               theme: 'default' });
    const blocks = document.querySelectorAll('pre code.language-mermaid');
    for (let i = 0; i < blocks.length; i++) {
      const code = blocks[i];
      const pre = code.parentElement;
      try {
        const { svg } = await mermaid.render('mm-' + i, code.textContent.trim());
        const box = document.createElement('div');
        box.className = 'mmd-box';
        box.innerHTML = svg;
        pre.replaceWith(box);
        // CJK 裁字：量 div 实高撑大 foreignObject + 同步父 rect
        for (const fo of box.querySelectorAll('foreignObject')) {
          const div = fo.querySelector('div, span, p');
          if (!div) continue;
          const need = Math.ceil(div.getBoundingClientRect().height);
          const have = Math.ceil(fo.getBoundingClientRect().height);
          if (need > have) {
            const grow = need - have;
            fo.setAttribute('height', String(Number(fo.getAttribute('height') || 0) + grow));
            const rect = (fo.closest('.node') || fo.parentElement)
                          ?.querySelector?.('rect');
            if (rect) {
              rect.setAttribute('y', String(Number(rect.getAttribute('y') || 0) - grow / 2));
              rect.setAttribute('height', String(Number(rect.getAttribute('height') || 0) + grow));
            }
          }
        }
      } catch (e) { /* 语法错：保留原代码块 */ }
    }
  } catch (e) { /* mermaid 加载失败：静默降级为代码块 */ }
});
</script>
"""


def convert(md_text: str, title: str = "Document",
            base_dir: Path | None = None, *, mermaid: bool = False) -> str:
    """md → 自包含 html。mermaid=True（预览变体）注入渲染脚本（需要页面
    允许脚本+站内 /assets/mermaid.min.js）；分享页保持零 JS。"""
    md2, _ = _inline_images(md_text, base_dir)
    md2 = _strip_remote_images(md2)
    body = _md.markdown(md2, extensions=_EXTENSIONS)
    body = _sanitize_html(body)
    body, toc = _inject_toc(body)
    body_cls = ' class="has-toc"' if toc else ""
    inner = (f"{toc}\n<main>\n{body}\n</main>" if toc
             else body)
    boot = _MERMAID_BOOT if mermaid else ""
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{_escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body{body_cls}>\n"
        f"{inner}\n</body>\n</html>\n{boot}")


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
