// P9 渠道卡（资源控制台）：Telegram bot token（入 vault）+ 白名单 + 启停 +
// 轮询状态/最近错误 + getMe 健康探测。WhatsApp/Signal 预留位未实现。
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useStore } from '../../stores/sessions';

interface ChanCfg { telegram_enabled: boolean; telegram_allow: string[]; token_set: boolean }
interface ChanStat { running: boolean; last_ok: string; last_error: string; processed: number; backoff_s: number }
interface ChanBind { chat_id: string; session_id: string; last_turn_id: number; created_at: string }

export default function ChannelsCard() {
  const [cfg, setCfg] = useState<ChanCfg | null>(null);
  const [stat, setStat] = useState<ChanStat | null>(null);
  const [allow, setAllow] = useState('');
  const [token, setToken] = useState('');
  const [msg, setMsg] = useState('');
  const [binds, setBinds] = useState<ChanBind[]>([]);
  const openSession = useStore(s => s.openSession);

  async function reload() {
    try {
      const d = await api<{ config: ChanCfg; status: ChanStat; bindings: ChanBind[] }>(
        '/api/admin/channels');
      setCfg(d.config); setStat(d.status); setBinds(d.bindings || []);
      setAllow((d.config.telegram_allow || []).join(', '));
    } catch { /* 面板不可达静默 */ }
  }
  useEffect(() => { void reload(); }, []);

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
      setMsg('已保存（启停即时生效；白名单即时生效）');
      void reload();
    } catch (e) { setMsg(`保存失败：${String(e)}`); }
  }

  async function probe() {
    setMsg('探测中…');
    const d = await api<{ ok: boolean; bot?: string; error?: string }>(
      '/api/admin/channels/probe');
    setMsg(d.ok ? `✅ @${d.bot} 连通` : `❌ ${d.error}`);
  }

  if (!cfg) return null;
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
        <span className="admin-msg">{msg}</span>
      </div>
      {stat && (
        <div className="muted" style={{ fontSize: 12 }}>
          轮询：{stat.running ? '运行中' : '停止'} · 已处理 {stat.processed} 条
          {stat.last_ok ? ` · 最近成功 ${stat.last_ok.slice(11, 19)}` : ''}
          {stat.last_error ? ` · 最近错误：${stat.last_error}` : ''}
          {stat.backoff_s ? ` · 退避 ${stat.backoff_s}s` : ''}
        </div>
      )}
      {binds.length > 0 && (
        <div className="setting-row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
          <label>会话绑定（{binds.length}）</label>
          {binds.map(b => (
            <div key={b.chat_id} className="row"
                 style={{ justifyContent: 'space-between', fontSize: 12 }}>
              <a className="link" onClick={() => void openSession(b.session_id)}
                 title={b.session_id}>
                chat {b.chat_id} → {b.session_id.slice(0, 18)}
              </a>
              <span className="muted">游标 turn #{b.last_turn_id}</span>
            </div>
          ))}
          <span className="muted">解绑在 Telegram 侧发 /unbind</span>
        </div>
      )}
    </div>
  );
}
