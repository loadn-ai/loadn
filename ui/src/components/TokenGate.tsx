import { useEffect, useState } from 'react';
import { token, setToken } from '../api/client';

/** W0 token 门：任何 API 401/403（token 缺失/过期/轮换）时弹出输入框。
 * 保存后整页刷新——所有已建立的 fetch/SSE 通道按新 token 重建。 */
export default function TokenGate() {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState('');

  useEffect(() => {
    const on401 = () => {
      if (!token()) return;           // 宽限期内未配 token 的 403 不弹（旧服务兼容）
      setOpen(true);
    };
    window.addEventListener('wd-unauthorized', on401);
    return () => window.removeEventListener('wd-unauthorized', on401);
  }, []);

  if (!open) return null;
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 999, display: 'flex',
      alignItems: 'center', justifyContent: 'center',
      background: 'rgba(0,0,0,.45)', backdropFilter: 'blur(2px)',
    }}>
      <div style={{
        background: 'var(--panel,#fff)', borderRadius: 14, padding: 28,
        width: 'min(92vw,380px)', boxShadow: '0 18px 50px rgba(0,0,0,.3)',
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>需要 API Token</div>
        <div style={{ fontSize: 13, opacity: 0.75, lineHeight: 1.6 }}>
          认证未通过（token 缺失或已轮换）。在服务器上执行
          <code style={{ background: 'rgba(127,127,127,.15)', padding: '2px 6px',
                         borderRadius: 4, margin: '0 4px' }}>wd token show</code>
          查看，或用带 <code>?token=…</code> 的链接打开。
        </div>
        <input
          autoFocus type="password" placeholder="粘贴 token" value={val}
          onChange={e => setVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && val.trim()) {
            setToken(val.trim()); location.reload();
          } }}
          style={{
            padding: '10px 12px', borderRadius: 8, fontSize: 14,
            border: '1px solid rgba(127,127,127,.35)', background: 'transparent',
            color: 'inherit', outline: 'none',
          }}
        />
        <button
          disabled={!val.trim()}
          onClick={() => { setToken(val.trim()); location.reload(); }}
          style={{
            padding: '10px 0', borderRadius: 8, fontSize: 14, fontWeight: 600,
            border: 'none', cursor: val.trim() ? 'pointer' : 'default',
            background: 'var(--accent,#4f6bf0)', color: '#fff',
            opacity: val.trim() ? 1 : 0.5,
          }}
        >
          保存并重连
        </button>
      </div>
    </div>
  );
}
