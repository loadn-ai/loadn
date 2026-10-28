import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { mdComponents } from '../lib/mdCode';

/**
 * md 预览 + 目录导航（v0.7.62）：
 * - 标题提取：行首 #{1,4}（跳过 ``` 围栏内的伪标题）；
 * - TOC 卡：文档顶部可折叠（标题 ≥4 个才显示），缩进层级，点击平滑滚动；
 * - scrollspy：滚动时高亮当前章节（rAF 节流，滚动容器自动探测）。
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
  const [open, setOpen] = useState(items.length >= 4);
  const [active, setActive] = useState('');
  const bodyRef = useRef<HTMLDivElement>(null);

  // 滚动容器探测（.fp-body 或更外层滚动祖先）+ scrollspy
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
        let cur = tops[0]?.id ?? '';   // 顶部（TOC 卡占位使首节可低于阈值）默认第一节
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
  }, []);

  // 标题锚点后处理（不用 heading renderer 闭包计数——重渲染时计数器不复位，
  // scrollspy setState 触发重渲后 id 全 undefined，实测点击跳转断链）：
  // DOM 层按序赋 id，天然幂等。
  useEffect(() => {
    const box = bodyRef.current;
    if (!box || !items.length) return;
    const heads = box.querySelectorAll('.md h1, .md h2, .md h3, .md h4');
    items.forEach((it, i) => heads[i]?.setAttribute('id', it.id));
  }, [items, text]);

  const comps = mdComponents;

  return (
    <div ref={bodyRef}>
      {items.length >= 4 && (
        <div className={`fp-toc${open ? ' on' : ''}`}>
          <button className="fp-toc-head" onClick={() => setOpen(o => !o)}>
            <span className="fp-toc-title">目录</span>
            <span className="fp-toc-n">{items.length} 节</span>
            <span className="fp-toc-arrow">{open ? '▾' : '▸'}</span>
          </button>
          {open && (
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
          )}
        </div>
      )}
      <div className="md fp-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={comps}>{text}</ReactMarkdown>
      </div>
    </div>
  );
}
