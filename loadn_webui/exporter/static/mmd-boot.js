// md 预览/分享统一 mermaid 渲染（v0.7.65）：外置脚本使分享页 CSP 可用
// script-src 'self'（拒绝一切内联脚本——产物内容即使消毒遗漏也无法执行）。
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
  } catch (e) { /* mermaid 加载失败：静默降级 */ }
});
