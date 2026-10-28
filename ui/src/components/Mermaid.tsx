import { useEffect, useId, useRef, useState } from 'react';

/**
 * mermaid 图表渲染（v0.7.60）：
 * - 懒加载：首个 mermaid 块出现才动态 import('mermaid')（~340KB gzip，
 *   不进主 chunk）；
 * - 语法错误优雅降级：显示原文 + 一行错误（不白屏不吞块）；
 * - 主题跟随：html[data-theme=dark] 时用 dark 基底（透明背景，前景随主题）。
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
