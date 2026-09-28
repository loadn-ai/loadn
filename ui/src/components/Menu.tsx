// 弹出菜单（侧栏行「…」共用）：portal 到 body 的 fixed 定位，锚点矩形贴边翻折。
// 支持二级面板（点「移动到」换页 + 返回头）与内联输入项（新建分类即移入）。
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ComponentType } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight } from './icons';

export type MenuEntry =
  | { kind?: 'item'; key: string; label: string;
      icon?: ComponentType<{ size?: number }>;
      danger?: boolean; check?: boolean;
      onClick?: () => void; sub?: MenuEntry[]; subTitle?: string }
  | { kind: 'divider'; key: string }
  | { kind: 'input'; key: string; placeholder: string;
      onSubmit: (v: string) => void };

const MENU_W = 216;
const VH_PAD = 8;

export default function PopupMenu({ anchor, items, onClose }: {
  anchor: HTMLElement; items: MenuEntry[]; onClose: () => void;
}) {
  const [panel, setPanel] = useState<{ title: string; items: MenuEntry[] } | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number; up: boolean } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // 锚点矩形只在挂载瞬间取一次：「…」按钮 :hover 才显示（display:none 时 rect
  // 全零），换二级面板时鼠标早已离开行，live 重读会拿到零矩形 → 菜单飞到
  // 视口左上角。冻结初值，换页只按新高度微调
  const rectRef = useRef(anchor.getBoundingClientRect());

  // 定位：右对齐锚点，越界左移；高度撑出视口则向上翻折（渲染后量高）。
  // 面板换页（菜单变高/变矮）：保持原位，只做贴底微调——不重新翻折，
  // 否则菜单会整个跳走。同值返回 prev 让 React 跳出，防死循环
  useLayoutEffect(() => {
    const r = rectRef.current;
    const vw = window.innerWidth, vh = window.innerHeight;
    const h = ref.current?.offsetHeight ?? 0;
    setPos(prev => {
      const x = Math.min(Math.max(r.right - MENU_W, VH_PAD), vw - MENU_W - VH_PAD);
      if (prev) {
        const y = Math.min(prev.y, Math.max(VH_PAD, vh - h - VH_PAD));
        return prev.x === x && prev.y === y ? prev : { x, y, up: prev.up };
      }
      let y = r.bottom + 4, up = false;
      if (y + h > vh - VH_PAD) { y = Math.max(VH_PAD, r.top - h - 4); up = true; }
      return { x, y, up };
    });
  }, [panel]);

  // 输入项聚焦必须 preventScroll：autoFocus 会把焦点元素滚进视图，连带
  // 滚动页面里不相关的滚动容器（实测 .chat-stream 跟着滚）——曾把刚打开的
  // 二级面板当场顶掉
  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, [panel]);

  // 关闭：外部按压 / Esc / 锚点祖先链滚动（侧栏列表滚动=菜单脱锚该关）/
  // 窗口变化。**不挂全局捕获**：主区聊天流等无关滚动器自己滚不该关菜单
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    const onScroll = () => onClose();
    const chain: (Element | Window)[] = [window];
    for (let el: Element | null = anchor; el; el = el.parentElement) chain.push(el);
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey);
    for (const t of chain) t.addEventListener('scroll', onScroll);
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey);
      for (const t of chain) t.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onClose);
    };
  }, [anchor, onClose]);

  const shown = panel?.items ?? items;

  return createPortal(
    <div className="wd-menu" ref={ref} style={pos
      ? { left: pos.x, top: pos.y, width: MENU_W, maxHeight: `calc(100vh - ${2 * VH_PAD}px)` }
      : { visibility: 'hidden' }}>
      {panel && (
        <button className="wd-menu-back" onClick={() => setPanel(null)}>
          <ChevronLeft size={13} /> {panel.title}
        </button>
      )}
      {shown.map(it => {
        if (it.kind === 'divider') return <div key={it.key} className="wd-menu-sep" />;
        if (it.kind === 'input') return (
          <input key={it.key} className="wd-menu-input" ref={inputRef} maxLength={40}
                 placeholder={it.placeholder}
                 onClick={e => e.stopPropagation()}
                 onKeyDown={e => {
                   if (e.key === 'Enter') {
                     const v = e.currentTarget.value.trim();
                     if (v) { it.onSubmit(v); onClose(); }
                   }
                 }} />
        );
        const Icon = it.icon;
        return (
          <button key={it.key} className={`wd-menu-item ${it.danger ? 'danger' : ''}`}
                  onClick={() => {
                    if (it.sub) setPanel({ title: it.subTitle ?? it.label, items: it.sub });
                    else { it.onClick?.(); onClose(); }
                  }}>
            {Icon && <Icon size={14} />}
            <span className="wd-menu-label">{it.label}</span>
            {it.sub ? <ChevronRight size={13} className="wd-menu-sub" />
              : it.check ? <span className="wd-menu-check" /> : null}
          </button>
        );
      })}
    </div>,
    document.body);
}
