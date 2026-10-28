
def test_inject_toc_and_adaptive_width():
    """v0.7.64：分享页静态 TOC（≥4 节）+ 宽度自适应。"""
    from loadn_webui.exporter.md_to_html import convert
    md = "\n".join(f"# 第{i}章 标题" for i in range(1, 6)) + "\n\n正文段落。"
    html = convert(md, title="t")
    assert 'class="has-toc"' in html
    assert '<aside class="doc-toc"' in html and "目录 · 5 节" in html
    assert html.count('id="sec-') == 5 and 'href="#sec-4"' in html
    assert "max-width: min(1180px, 94vw)" in html          # 宽度自适应
    assert "<main>" in html                                 # 两栏让位结构

    # <4 节：无 TOC、无 has-toc
    html2 = convert("# 仅一节\n\n内容\n\n## 二\n\n## 三\n\n正文")
    assert '<aside class="doc-toc"' not in html2 and 'class="has-toc"' not in html2

    # 消毒回归：注入的 id 在 sanitize 后（不被剥），恶意内容仍被清
    html3 = convert("# A\n\n<script>alert(1)</script>\n\n# B\n\n# C\n\n# D\n\n# E")
    assert "<script" not in html3
    assert html3.count('id="sec-') >= 5
