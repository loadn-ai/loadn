import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { mdComponents } from '../lib/mdCode';

/**
 * md 预览 + 左侧悬浮目录（v0.7.62 → v0.7.63 侧栏化）：
 * - 布局：左侧 sticky 目录栏 + 右侧正文（文档站经典两栏）；
 * - 容器 ≥720px 常驻侧栏；更窄自动收成左缘小圆钮，点开浮层目录；
 * - 标题提取 h1-h4（跳过代码围栏伪标题）；DOM 后处理赋锚点 id（幂等）；
 * - scrollspy：滚动高亮当前章节（rAF 节流；顶部默认首节）。
 */

type TocItem = { id: string; level: number; text: string };

function extractToc(text: string): TocItem[] {
  const out: TocItem[] = [];
  let fence = false;
  let i = 0;
  for (const line of text.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) { fence = !fence; continue; }
    if (fence) continue;
    const m = /^(#{1,4})\s+(.+?)\s*#*\s*$/.exec(line);
    if (m) out.push({ id: `fp-toc-${i++}`, level: m[1].length, text: m[2] });
  }
  return out;
}

export default function MdToc({ text }: { text: string }) {
  const items = useMemo(() => extractToc(text), [text]);
  const [active, setActive] = useState('');
  const [floatOpen, setFloatOpen] = useState(false);   // 窄容器浮层目录开合
  const [wide, setWide] = useState(true);              // 容器宽度态
  const wrapRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const showToc = items.length >= 4;

  // 容器宽度自适应：≥720px 常驻侧栏；更窄收成左缘圆钮（浮层展开）
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !showToc) return;
    const ro = new ResizeObserver((es) => {
      const w = es[0]?.contentRect.width ?? 999;
      setWide(w >= 720);
      if (w >= 720) setFloatOpen(false);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [showToc]);

  // 标题锚点后处理（幂等；不用 heading renderer 闭包计数——重渲染不复位）
  useEffect(() => {
    const box = bodyRef.current;
    if (!box) return;
    const heads = box.querySelectorAll('.md h1, .md h2, .md h3, .md h4');
    items.forEach((it, i) => heads[i]?.setAttribute('id', it.id));
  }, [items, text]);

  // scrollspy：滚动容器探测 + rAF 节流
  useEffect(() => {
    if (!items.length) return;
    let scroller: HTMLElement | null = bodyRef.current?.closest('.fp-body') ?? null;
    if (!scroller) scroller = bodyRef.current?.parentElement ?? null;
    if (!scroller) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const tops = items
          .map((it) => {
            const el = document.getElementById(it.id);
            return el ? { id: it.id, top: el.getBoundingClientRect().top } : null;
          })
          .filter(Boolean) as { id: string; top: number }[];
        let cur = tops[0]?.id ?? '';   // 顶部默认首节
        for (const t of tops) if (t.top <= 120) cur = t.id;
        setActive(cur);
      });
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => {
      scroller?.removeEventListener('scroll', onScroll);
      cancelAnimationFrame(raf);
    };
  }, [items]);

  const jump = useCallback((id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setFloatOpen(false);
  }, []);

  const list = (
    <nav className="fp-toc-list">
      {items.map((it) => (
        <a key={it.id}
           className={`fp-toc-item lv${it.level}${active === it.id ? ' act' : ''}`}
           href={`#${it.id}`}
           onClick={(e) => { e.preventDefault(); jump(it.id); }}
           title={it.text}>
          {it.text}
        </a>
      ))}
    </nav>
  );

  if (!showToc) {
    return (
      <div ref={bodyRef} className="md fp-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{text}</ReactMarkdown>
      </div>
    );
  }

  return (
    <div className="fp-tocwrap" ref={wrapRef}>
      {wide ? (
        <aside className="fp-toc-side" aria-label="目录">
          <div className="fp-toc-sidehead">目录<span className="fp-toc-n">{items.length}</span></div>
          {list}
        </aside>
      ) : (
        <>
          <button className={`fp-toc-fab${floatOpen ? ' on' : ''}`}
                  title="目录" aria-label="目录"
                  onClick={() => setFloatOpen(o => !o)}>☰</button>
          {floatOpen && (
            <div className="fp-toc-float" role="dialog" aria-label="目录浮层">
              <div className="fp-toc-sidehead">
                目录<span className="fp-toc-n">{items.length}</span>
                <button className="fp-toc-x" onClick={() => setFloatOpen(false)}>✕</button>
              </div>
              {list}
            </div>
          )}
        </>
      )}
      <div ref={bodyRef} className="md fp-md fp-tocmain">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{text}</ReactMarkdown>
      </div>
    </div>
  );
}
