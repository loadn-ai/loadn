// 全局确认对话框（BD-2）——单例挂 App 根，stores/confirm 的 askConfirm() 驱动。
// Esc/遮罩点击 = 取消；danger 红 OK 键；open 时锁背景滚动从简（遮罩已挡交互）。
import { useEffect, useRef } from 'react';
import { useConfirm } from '../stores/confirm';

export default function ConfirmDialog() {
  const open = useConfirm(s => s.open);
  const opts = useConfirm(s => s.opts);
  const settle = useConfirm(s => s.settle);
  const okRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    okRef.current?.focus();   // 焦点默认在确认键（回车即确认，与原生 confirm 肌肉记忆一致）
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') settle(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, settle]);

  if (!open || !opts) return null;
  return (
    <div className="modal-mask"
         onMouseDown={e => { if (e.target === e.currentTarget) settle(false); }}>
      <div className="modal" style={{ maxWidth: 380 }} role="alertdialog"
           aria-modal="true" aria-label={opts.title}>
        <h3>{opts.title}</h3>
        {opts.body && (
          <div style={{ fontSize: 'var(--fs-md)', color: 'var(--text-dim)',
                        lineHeight: 1.6, whiteSpace: 'pre-wrap', margin: '-6px 0 4px' }}>
            {opts.body}
          </div>
        )}
        <div className="modal-foot" style={{ marginTop: 18 }}>
          <button className="btn ghost" onClick={() => settle(false)}>取消</button>
          <button ref={okRef}
                  className={opts.danger ? 'btn danger' : 'btn primary'}
                  onClick={() => settle(true)}>{opts.okText ?? '确认'}</button>
        </div>
      </div>
    </div>
  );
}
