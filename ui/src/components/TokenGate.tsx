import { useEffect, useState } from 'react';
import { api, setToken } from '../api/client';

/** 八轮（多用户）：账号密码登录门（替代裸 token 输入）。
 *
 * 形态探测 /api/auth/status：
 * - needs_setup → 安装向导（建首个管理员账号；存量数据自动归并到该号）
 * - 有账号体系且 API 401 → 登录表单（用户名+密码 → httpOnly cookie）
 * - 已登录 → 右下角用户徽标（登出）
 * token 输入保留为「高级」折叠（API/CLI 场景与旧部署兼容）。
 *
 * 宽限期（无 token 配置）与 token 通道行为不变——cookie 是叠加通道。 */
interface AuthStatus {
  needs_setup: boolean; auth_required?: boolean; logged_in: boolean;
  user: { id: number; username: string; role: string } | null;
}

const box: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 999, display: 'flex',
  alignItems: 'center', justifyContent: 'center',
  background: 'rgba(0,0,0,.45)', backdropFilter: 'blur(2px)',
};
const card: React.CSSProperties = {
  background: 'var(--panel,#fff)', borderRadius: 14, padding: 28,
  width: 'min(92vw,380px)', boxShadow: '0 18px 50px rgba(0,0,0,.3)',
  display: 'flex', flexDirection: 'column', gap: 12,
};
const input: React.CSSProperties = {
  padding: '10px 12px', borderRadius: 8, fontSize: 'var(--fs-xl)',
  border: '1px solid rgba(127,127,127,.35)', background: 'transparent',
  color: 'inherit', outline: 'none',
};
const btn: React.CSSProperties = {
  padding: '10px 0', borderRadius: 8, fontSize: 'var(--fs-xl)', fontWeight: 600,
  border: 'none', background: 'var(--accent)', color: '#fff',
};

export default function TokenGate() {
  const [st, setSt] = useState<AuthStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'login' | 'setup'>('login');
  const [user, setUser] = useState('');
  const [pass, setPass] = useState('');
  const [pass2, setPass2] = useState('');
  const [err, setErr] = useState('');
  const [advanced, setAdvanced] = useState(false);
  const [tok, setTok] = useState('');

  const probe = async () => {
    try {
      const d = await api<AuthStatus>('/api/auth/status');
      setSt(d);
      if (d.needs_setup) { setMode('setup'); setOpen(true); }
      else if (d.auth_required) { setMode('login'); setOpen(true); }
    } catch { /* 探测失败保持沉默（旧版兼容） */ }
  };
  useEffect(() => {
    void probe();
    const on401 = () => {
      if (!st?.needs_setup) { setMode('login'); setOpen(true); void probe(); }
    };
    window.addEventListener('wd-unauthorized', on401);
    return () => window.removeEventListener('wd-unauthorized', on401);
  }, []);

  const submit = async () => {
    setErr('');
    try {
      if (mode === 'setup') {
        if (pass !== pass2) { setErr('两次密码不一致'); return; }
        await api('/api/auth/setup', {
          method: 'POST', body: JSON.stringify(
            { username: user.trim(), password: pass }) });
      } else {
        await api('/api/auth/login', {
          method: 'POST', body: JSON.stringify(
            { username: user.trim(), password: pass }) });
      }
      location.reload();          // cookie 已 Set——整页重建 fetch/SSE 通道
    } catch (e) {
      const msg = await (e instanceof Error ? e.message : String(e));
      setErr(String(msg));
    }
  };

  // 已登录的用户身份展示在侧栏底部（sidebar-foot）——这里只管登录门
  if (!open) return null;
  return (
    <div style={box}>
      <div style={card}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>
          {mode === 'setup' ? '创建管理员账号' : '登录'}
        </div>
        {mode === 'setup' && (
          <div style={{ fontSize: 'var(--fs-lg)', opacity: 0.75, lineHeight: 1.6 }}>
            首次使用：创建你的账号（密码至少 8 位）。已有的任务/数据会自动
            归并到这个账号下。
          </div>
        )}
        <input autoFocus placeholder="用户名" value={user}
               onChange={e => setUser(e.target.value)} style={input} />
        <input type="password" placeholder="密码" value={pass}
               onChange={e => setPass(e.target.value)} style={input}
               onKeyDown={e => { if (e.key === 'Enter' && mode === 'login') void submit(); }} />
        {mode === 'setup' && (
          <input type="password" placeholder="确认密码" value={pass2}
                 onChange={e => setPass2(e.target.value)} style={input}
                 onKeyDown={e => { if (e.key === 'Enter') void submit(); }} />
        )}
        {err ? <div style={{ color: '#e5484d', fontSize: 'var(--fs-lg)' }}>{err}</div> : null}
        <button disabled={!user.trim() || !pass}
                onClick={() => void submit()} style={{
                  ...btn, opacity: user.trim() && pass ? 1 : 0.5,
                  cursor: user.trim() && pass ? 'pointer' : 'default',
                }}>{mode === 'setup' ? '创建并登录' : '登录'}</button>
        {!st?.needs_setup && (
          <>
            <button className="link" style={{ fontSize: 'var(--fs-md)', border: 'none', background: 'none' }}
                    onClick={() => setAdvanced(a => !a)}>
              {advanced ? '收起' : '使用 API token（CLI/旧部署）'}
            </button>
            {advanced && (
              <>
                <input placeholder="粘贴 token" value={tok}
                       onChange={e => setTok(e.target.value)} style={input} />
                <button disabled={!tok.trim()} style={{ ...btn, opacity: tok.trim() ? 1 : 0.5 }}
                        onClick={() => { setToken(tok.trim()); location.reload(); }}>
                  用 token 连接
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
