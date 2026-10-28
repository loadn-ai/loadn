// md 渲染引导（v0.7.68）：mermaid 动态加载——npmmirror（阿里，国内稳）
// 优先，失败回退站内 /share/assets/mermaid.min.js（gzip 预压缩）。
// exporter 仅在文档含 ```mermaid 块时才引用本文件（无图文档零 JS）。
(function () {
  var CDN = 'https://registry.npmmirror.com/mermaid/12.2.0/files/dist/mermaid.min.js';
  var LOCAL = '/share/assets/mermaid.min.js';
  function load(url, onerr) {
    var s = document.createElement('script');
    s.src = url; s.onload = boot; s.onerror = onerr;
    document.head.appendChild(s);
  }
  function boot() {
    if (!window.mermaid) return;
    window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict',
                                theme: 'default' });
    var blocks = document.querySelectorAll('pre code.language-mermaid');
    var chain = Promise.resolve();
    blocks.forEach(function (code, i) {
      chain = chain.then(function () {
        return window.mermaid.render('mm-' + i, code.textContent.trim())
          .then(function (r) {
            var box = document.createElement('div');
            box.className = 'mmd-box';
            box.innerHTML = r.svg;
            code.parentElement.replaceWith(box);
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
          })
          .catch(function () { /* 语法错：保留代码块 */ });
      });
    });
  }
  load(CDN, function () { load(LOCAL, function () { /* 双失败：静默降级 */ }); });
})();
