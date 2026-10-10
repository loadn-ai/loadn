// P9 渠道卡：Telegram bot token（入 vault）+ 白名单 + 启停 +
// 轮询状态/最近错误 + getMe 健康探测。WhatsApp/Signal 预留位未实现。
// AC-5.10e（P2-3）：归位 Webhooks tab（对话入口与事件入口同域）——
// 因 Webhooks 面普通用户可见，卡内自守卫：cookie 普通用户不渲染（token 单用户模式全开）。
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { toast } from '../../stores/toasts';
import { useStore } from '../../stores/sessions';
import { User } from '../icons';

interface ChanCfg { telegram_enabled: boolean; telegram_allow: string[]; token_set: boolean }
interface ChanStat { running: boolean; last_ok: string; last_error: string; processed: number; backoff_s: number }
interface ChanBind { chat_id: string; session_id: string; last_turn_id: number; created_at: string; owner_id?: number | null; owner_name?: string | null }

export default function ChannelsCard() {
  const [cfg, setCfg] = useState<ChanCfg | null>(null);
  const [stat, setStat] = useState<ChanStat | null>(null);
  const [allow, setAllow] = useState('');
  const [token, setToken] = useState('');
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [cfgErr, setCfgErr] = useState('');
  const [binds, setBinds] = useState<ChanBind[]>([]);
  const openSession = useStore(s => s.openSession);
  // AC-5.10e：随面归位加 role 守卫（null=token 单用户模式或未返回→渲染，后端 403 兑底）
  const [role, setRole] = useState<string | null>(null);
  useEffect(() => {
    void api<{ logged_in: boolean; user: { role: string } | null }>('/api/auth/status')
      .then(d => setRole(d.logged_in && d.user ? d.user.role : null))
      .catch(() => setRole(null));
  }, []);

  async function reload() {
    try {
      const d = await api<{ config: ChanCfg; status: ChanStat; bindings: ChanBind[] }>(
        '/api/admin/channels');
      setCfg(d.config); setStat(d.status); setBinds(d.bindings || []);
      setAllow((d.config.telegram_allow || []).join(', '));
      setCfgErr('');
    } catch (e) { setCfgErr(`渠道面板加载失败：${String(e)}`); }  // 不可达≠无配置，卡壳显示错误
  }
  useEffect(() => { void reload(); }, []);
  // 守卫须在全部 hooks 之后（rules-of-hooks：条件 return 不得插在 hook 间）
  if (role !== null && role !== 'admin') return null;   // 普通用户：渠道管理不可见

  async function save() {
    try {
      await api('/api/admin/channels', { method: 'PUT', body: JSON.stringify({
        telegram_enabled: cfg?.telegram_enabled ?? false,
        telegram_allow: allow.split(',').map(s => s.trim()).filter(Boolean),
      }) });
      if (token.trim()) {
        await api('/api/admin/channels/token', { method: 'PUT',
          body: JSON.stringify({ token: token.trim() }) });
        setToken('');
      }
      toast('已保存（启停即时生效；白名单即时生效）');
      void reload();
    } catch (e) { toast(`保存失败：${String(e)}`, false); }
  }

  async function probe() {
    setMsg({ t: '探测中…' });
    try {
      const d = await api<{ ok: boolean; bot?: string; error?: string }>(
        '/api/admin/channels/probe');
      setMsg(d.ok ? { t: `✅ @${d.bot} 连通` } : { t: `❌ ${d.error}`, err: true });   // 探测过程/结果原地展示（含"探测中…"过渡态）
    } catch (e) { setMsg({ t: `探测失败：${String(e)}`, err: true }); }
  }

  if (!cfg) return cfgErr ? (
    <div className="setting-card">
      <h4>渠道（Telegram 双向对话）</h4>
      <div className="form-msg err">{cfgErr}</div>
    </div>
  ) : null;
  return (
    <div className="setting-card">
      <h4>渠道（Telegram 双向对话）</h4>
      <div className="setting-row">
        <label>启用</label>
        <input type="checkbox" checked={cfg.telegram_enabled}
          onChange={e => setCfg({ ...cfg, telegram_enabled: e.target.checked })} />
        <span className="muted">白名单 chat_id 收发；token 只进 vault</span>
      </div>
      <div className="setting-row">
        <label>bot token</label>
        <input className="mono" type="password" placeholder={cfg.token_set ? '（已存，留空=不改）' : '123456:ABC-…'}
          value={token} onChange={e => setToken(e.target.value)} />
        <button className="res-btn" onClick={() => void probe()}>探测 getMe</button>
      </div>
      <div className="setting-row">
        <label>白名单 chat_id</label>
        <input className="mono" placeholder="123456789, 987654321（逗号分隔）"
          value={allow} onChange={e => setAllow(e.target.value)} />
      </div>
      <div className="setting-row">
        <button className="btn sm primary" onClick={() => void save()}>保存</button>
        {msg && <span className={msg.err ? 'admin-msg err' : 'admin-msg'}>{msg.t}</span>}
      </div>
      {stat && (
        <div className="muted" style={{ fontSize: 'var(--fs-md)' }}>
          轮询：{stat.running ? '运行中' : '停止'} · 已处理 {stat.processed} 条
          {stat.last_ok ? ` · 最近成功 ${stat.last_ok.slice(5, 19).replace('T', ' ')}` : ''}
          {stat.last_error ? ` · 最近错误：${stat.last_error}` : ''}
          {stat.backoff_s ? ` · 退避 ${stat.backoff_s}s` : ''}
        </div>
      )}
      {binds.length > 0 && (
        <div className="setting-row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
          <label>会话绑定（{binds.length}）</label>
          {binds.map(b => (
            <div key={b.chat_id} className="row"
                 style={{ justifyContent: 'space-between', fontSize: 'var(--fs-md)' }}>
              <a className="link" onClick={() => void openSession(b.session_id)}
                 title={b.session_id}>
                chat {b.chat_id} → {b.session_id.slice(0, 18)}
              </a>
              <span>
                <span className="muted">游标 #{b.last_turn_id} · </span>
                <a className="link" title="把该 chat 经渠道建的会话归属此用户（admin）"
                   onClick={async () => {
                     const name = prompt(`chat ${b.chat_id} 归属哪个用户？（留空=无主，输用户名）`,
                                         b.owner_name ?? '');
                     if (name === null) return;
                     try {
                       await api(`/api/admin/channels/bindings/${b.chat_id}`,
                         { method: 'PUT', body: JSON.stringify({ owner: name }) });
                       await reload();
                     } catch (e) {
                       toast(`设置失败：${e instanceof Error ? e.message : e}`, false);
                     }
                   }}><User size={12} style={{ verticalAlign: -2 }} /> {b.owner_name || '认领'}</a>
              </span>
            </div>
          ))}
          <span className="muted">解绑在 Telegram 侧发 /unbind；认领后该
            chat 经渠道新建的会话归属该用户</span>
        </div>
      )}
    </div>
  );
}
