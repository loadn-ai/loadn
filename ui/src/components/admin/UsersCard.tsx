import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { toast } from '../../stores/toasts';

/** 多用户批2：用户管理卡（admin）——列表/建号/角色/启停/重置密码。
 *  禁用即时踢下线（服务端删会话）；不能禁用/降级自己（防锁死管理面）。
 *  AC-5.10d（P2-4）：行内 last_login + 搜索 + 前端分页（20/页）。 */
interface UserRow {
  id: number; username: string; role: string; display_name?: string | null;
  disabled: number; created_at?: string; last_login_at?: string | null;
  active_sessions: number;
}

const PAGE = 20;

export default function UsersCard() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [me, setMe] = useState<{ id: number; username: string; role: string } | null>(null);
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [adding, setAdding] = useState({ username: '', password: '', role: 'user' });
  const [resetPw, setResetPw] = useState<{ uid: number; pw: string } | null>(null);
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);

  const load = async () => {
    try {
      const d = await api<{ users: UserRow[] }>('/api/auth/users');
      setUsers(d.users);
    } catch (e) {
      setMsg({ t: `加载失败：${e instanceof Error ? e.message : e}`, err: true });
    }
  };
  useEffect(() => {
    void load();
    api<{ id: number; username: string; role: string }>('/api/auth/me')
      .then(setMe).catch(() => setMe(null));
  }, []);

  const patch = async (uid: number, body: Record<string, unknown>, what: string) => {
    try {
      await api(`/api/auth/users/${uid}`, { method: 'PATCH',
        body: JSON.stringify(body) });
      toast(what + ' ✓');
      await load();
    } catch (e) {
      toast(`${what} 失败：${e instanceof Error ? e.message : e}`, false);
    }
  };

  if (me !== null && me.role !== 'admin') return (
    <div className="setting-card">
      <h4>用户与账号</h4>
      <div className="muted">用户管理需要管理员权限。</div>
    </div>
  );
  const filtered = users.filter(u => !q.trim()
    || u.username.toLowerCase().includes(q.trim().toLowerCase())
    || (u.display_name ?? '').toLowerCase().includes(q.trim().toLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const cur = Math.min(page, pages);
  const show = filtered.slice((cur - 1) * PAGE, cur * PAGE);
  return (
    <div className="setting-card">
      <h4>用户与账号</h4>
      <div className="muted" style={{ fontSize: 12 }}>
        账号密码登录（token 通道继续可用于 CLI/旧部署）。属主隔离：普通用户
        只见自己的任务/项目/调度/webhook；admin 全见。
      </div>
      <div className="admin-toolbar" style={{ marginBottom: 6 }}>
        <input placeholder="搜索用户名/昵称…" value={q} style={{ width: 200 }}
               onChange={e => { setQ(e.target.value); setPage(1); }} />
        <span className="muted" style={{ fontSize: 12 }}>
          共 {users.length} 个账号{q.trim() ? ` · 命中 ${filtered.length}` : ''}
        </span>
      </div>
      {show.map(u => (
        <div key={u.id} className="setting-row"
             style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <span>
            <b>{u.username}</b>
            {u.display_name ? <span className="muted">（{u.display_name}）</span> : null}
            <span className="muted"> · {u.role === 'admin' ? '管理员' : '用户'}</span>
            {u.disabled ? <span className="chip">已禁用</span> : null}
            {u.active_sessions > 0
              ? <span className="muted"> · {u.active_sessions} 活跃会话</span> : null}
            <span className="muted"> · {u.last_login_at
              ? `最近登录 ${u.last_login_at.slice(5, 16).replace('T', ' ')}`
              : '未登录过'}</span>
            {me?.id === u.id ? <span className="muted"> · 这是我</span> : null}
          </span>
          <span>
            {u.role === 'admin'
              ? <button className="btn ghost sm" title="降为普通用户"
                        onClick={() => {
                          if (!confirm(`将 ${u.username} 降为普通用户？其管理面权限立即收回（当前打开的管理页下次进入按普通用户裁剪）。`)) return;
                          void patch(u.id, { role: 'user' }, '降级');
                        }}>降为用户</button>
              : <button className="btn ghost sm" title="升为管理员"
                        onClick={() => {
                          if (!confirm(`将 ${u.username} 升为管理员？其可管理全部用户/凭证/平台配置。`)) return;
                          void patch(u.id, { role: 'admin' }, '升级');
                        }}>升为管理员</button>}
            {u.disabled
              ? <button className="btn ghost sm"
                        onClick={() => void patch(u.id, { disabled: false }, '启用')}>启用</button>
              : <button className="btn ghost sm danger-link"
                        title="禁用并踢下线全部会话"
                        onClick={() => {
                          if (!confirm(`禁用 ${u.username}？其全部登录会话立即失效。`)) return;
                          void patch(u.id, { disabled: true }, '禁用');
                        }}>禁用</button>}
            <button className="btn ghost sm" onClick={() =>
              setResetPw(resetPw?.uid === u.id ? null : { uid: u.id, pw: '' })}>重置密码</button>
            {resetPw?.uid === u.id && (
              <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                <input type="password" placeholder="新密码（≥8 位）" value={resetPw.pw}
                  onChange={e => setResetPw({ uid: u.id, pw: e.target.value })} />
                <button className="btn sm primary" disabled={resetPw.pw.length < 8}
                  title="重置后其会话全部失效"
                  onClick={() => {
                    void patch(u.id, { password: resetPw.pw }, '重置密码');
                    setResetPw(null);
                  }}>确认</button>
                <button className="btn sm" onClick={() => setResetPw(null)}>取消</button>
              </span>
            )}
          </span>
        </div>
      ))}
      {pages > 1 && (
        <div className="admin-toolbar" style={{ justifyContent: 'center' }}>
          <button className="btn sm" disabled={cur <= 1} onClick={() => setPage(cur - 1)}>‹ 上一页</button>
          <span className="muted" style={{ fontSize: 12 }}>第 {cur} / {pages} 页</span>
          <button className="btn sm" disabled={cur >= pages} onClick={() => setPage(cur + 1)}>下一页 ›</button>
        </div>
      )}
      <div className="setting-row" style={{ gap: 6 }}>
        <input placeholder="新用户名" value={adding.username}
               onChange={e => setAdding(a => ({ ...a, username: e.target.value }))} />
        <input placeholder="初始密码（≥8 位）" type="password" value={adding.password}
               onChange={e => setAdding(a => ({ ...a, password: e.target.value }))} />
        <select value={adding.role}
                onChange={e => setAdding(a => ({ ...a, role: e.target.value }))}>
          <option value="user">用户</option>
          <option value="admin">管理员</option>
        </select>
        <button className="btn sm" disabled={!adding.username.trim()
          || adding.password.length < 8}
          onClick={async () => {
            try {
              await api('/api/auth/users', { method: 'POST',
                body: JSON.stringify(adding) });
              setAdding(a => ({ ...a, username: '', password: '' }));
              toast('建号 ✓');
              await load();
            } catch (e) {
              toast(`建号失败：${e instanceof Error ? e.message : e}`, false);
            }
          }}>建号</button>
      </div>
      {msg ? <div className={msg.err ? 'form-msg err' : 'form-msg'}>{msg.t}</div> : null}
    </div>
  );
}
