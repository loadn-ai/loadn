// 管理页命令面板（AC-5.9 IA-5）：Ctrl+K 唤起，tab 名/分组名过滤，
// ↑↓ 选择 Enter 跳转——12+ tab 的键盘直达（配合 tab 栏 ARIA 方向键）。
import { useEffect, useRef, useState } from 'react';
import type { AdminTab } from './shared';

interface TabDef { id: AdminTab; label: string; group: string }

export default function CommandPalette({ tabs, onPick, onClose }: {
  tabs: TabDef[]; onPick: (t: AdminTab) => void; onClose: () => void }) {
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const qs = q.trim().toLowerCase();
  const filtered = qs
    ? tabs.filter(t => t.label.toLowerCase().includes(qs)
      || t.id.includes(qs) || t.group.toLowerCase().includes(qs))
    : tabs;

  useEffect(() => { inputRef.current?.focus(); }, []);

  const pick = (t: TabDef) => { onPick(t.id); onClose(); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setIdx(i => Math.min(i + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx(i => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (filtered[idx]) pick(filtered[idx]); }
    else if (e.key === 'Escape') { e.preventDefault(); onClose(); }
  };

  return (
    <div className="cmdk-overlay" onClick={onClose}>
      <div className="cmdk" onClick={e => e.stopPropagation()} onKeyDown={onKey}>
        <input ref={inputRef} value={q}
          onChange={e => { setQ(e.target.value); setIdx(0); }}   // 过滤变化回到首项
          placeholder={`切换到…（${tabs.length} 个面板 · ↑↓ 选择 · Enter 跳转 · Esc 关）`} />
        <div className="cmdk-list">
          {filtered.map((t, i) => (
            <div key={t.id} className={`cmdk-item${i === idx ? ' on' : ''}`}
              onMouseEnter={() => setIdx(i)} onClick={() => pick(t)}>
              <b>{t.label}</b>
              <span className="muted">{t.group}</span>
              {i === idx && <span className="cmdk-hint">Enter</span>}
            </div>
          ))}
          {!filtered.length && <div className="cmdk-item muted">（无匹配面板）</div>}
        </div>
      </div>
    </div>
  );
}
