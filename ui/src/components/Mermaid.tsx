import { useEffect, useId, useRef, useState } from 'react';

/**
 * mermaid 图表渲染（v0.7.60）+ CJK 裁字修复（v0.7.61）：
 * - 懒加载：首个 mermaid 块出现才动态 import('mermaid')（~340KB gzip，
 *   不进主 chunk）；
 * - 语法错误优雅降级：显示原文 + 一行错误（不白屏不吞块）；
 * - 主题跟随：html[data-theme=dark] 时用 dark 基底；
 * - CJK 修复：mermaid 12 flowchart 的 foreignObject 对中文行高估值偏低，
 *   定高容器裁掉文字下半截（htmlLabels=false 亦被忽略，实证仍出 fo）。
 *   双修：CSS overflow visible（现代浏览器允许 fo 内容溢出显示）+
 *   渲染后量 div 实际高度撑大 fo 高度属性（同步增高父 rect）兑底。
 */
let mermaidReady: Promise<typeof import('mermaid').default> | null = null;
function loadMermaid() {
  if (!mermaidReady) {
    mermaidReady = import('mermaid').then((m) => {
      const dark = document.documentElement.dataset.theme === 'dark';
      m.default.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: dark ? 'dark' : 'default',
        fontFamily: 'inherit',
        // htmlLabels=false：节点文字走纯 SVG text——htmlLabels 的
        // foreignObject 对 CJK 行高估值偏低，定高容器裁掉中文下半截
        //（生产实证 536e 流程图方框文字被遮挡）；text 由浏览器自排无裁剪面。
        flowchart: { htmlLabels: false, useMaxWidth: true, padding: 14 },
      });
      return m.default;
    });
  }
  return mermaidReady;
}

export default function Mermaid({ code }: { code: string }) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '');
  const [svg, setSvg] = useState('');
  const [err, setErr] = useState('');
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    loadMermaid()
      .then((mermaid) =>
        mermaid.render(`mmd-${uid}`, code.trim()).then((r) => {
          if (alive) { setSvg(r.svg); setErr(''); }
        }),
      )
      .catch((e: unknown) => {
        if (alive) setErr(String((e as Error)?.message ?? e));
      });
    return () => { alive = false; };
  }, [code, uid]);

  // CJK 裁字后处理：SVG 注入 DOM 后量实际高度，撑大 fo（同步父 rect）。
  useEffect(() => {
    if (!svg || !boxRef.current) return;
    const t = window.setTimeout(() => {
      const box = boxRef.current;
      if (!box) return;
      for (const fo of box.querySelectorAll('foreignObject')) {
        const div = fo.querySelector('div, span, p');
        if (!div) continue;
        const need = Math.ceil(div.getBoundingClientRect().height);
        const have = Math.ceil(fo.getBoundingClientRect().height);
        if (need > have) {
          const grow = need - have;
          fo.setAttribute('height', String(
            Number(fo.getAttribute('height') ?? 0) + grow));
          // 父 rect（节点方框）同步增高：y 上移 grow/2 + 高度加 grow，
          // 文字在框内居中不贴底。
          const nodeG = (fo.closest('.node') ?? fo.parentElement);
          const rect = nodeG?.querySelector?.(':scope > rect, rect');
          if (rect) {
            const y = Number(rect.getAttribute('y') ?? 0) - grow / 2;
            const h = Number(rect.getAttribute('height') ?? 0) + grow;
            rect.setAttribute('y', String(y));
            rect.setAttribute('height', String(h));
          }
        }
      }
    }, 50);
    return () => window.clearTimeout(t);
  }, [svg]);

  if (err) {
    return (
      <div className="mmd-fallback">
        <div className="mmd-err">流程图语法错误：{err.slice(0, 160)}</div>
        <pre>{code}</pre>
      </div>
    );
  }
  return (
    <div
      ref={boxRef}
      className="mmd-box"
      // SVG 由本地 mermaid（securityLevel=strict）生成：过滤 script/事件绑定
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
