// 流量面板（egress 白名单/临时授权/拒绝历史）——从 AdminPanel 拆出（v0.6.12）
import { useEffect, useState } from 'react';
import { api } from '../../api/client';

export default function EgressPanel() {
  // 产品视角：用户关心「数据流向了哪些域、各多少次、有没有被拒」，
  // 平铺流水只是原料——聚合成域维度，行可展开看时间点
  const [events, setEvents] = useState<{ ts: string; host: string; decision: string }[]>([]);
  const [mode, setMode] = useState('');
  const [allow, setAllow] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const load = async () => {
    try {
      const d = await api<{ events: { ts: string; host: string; decision: string }[]; mode: string; allow: string[] }>(
        '/api/admin/egress?n=100');
      setEvents(d.events); setMode(d.mode); setAllow(d.allow ?? []);
    } catch { /* 忽略 */ }
  };
  useEffect(() => { void load(); const t = setInterval(() => void load(), 15000); return () => clearInterval(t); }, []);

  const GW = 'llm-gw.internal';
  const byHost = new Map<string, { n: number; denied: number; last: string; times: string[] }>();
  for (const e of events) {
    const h = e.host || '?';
    const cur = byHost.get(h) ?? { n: 0, denied: 0, last: '', times: [] };
    cur.n += 1;
    if (!String(e.decision).startsWith('allow')) cur.denied += 1;
    cur.last = e.ts;
    cur.times.push((e.ts || '').slice(11, 19));
    byHost.set(h, cur);
  }
  const gw = byHost.get(GW);
  byHost.delete(GW);
  const hosts = [...byHost.entries()].sort((a, b) => b[1].n - a[1].n);
  const deniedTotal = [...byHost.values()].reduce((s2, v) => s2 + v.denied, 0);
  const external = hosts.filter(([, v]) => v.denied === 0).length;

  return (
    <div className="pad">
      <div style={{ display: 'flex', gap: 16, marginBottom: 12, flexWrap: 'wrap', fontSize: 13 }}>
        <span>模式 <b style={{ color: mode === 'enforce' ? undefined : '#d4a017' }}>{mode || '-'}</b></span>
        <span>窗口内请求 <b>{events.length}</b></span>
        <span>放行域 <b style={{ color: '#3aa675' }}>{external}</b></span>
        <span>被拒 <b style={{ color: deniedTotal ? 'var(--accent,#e5484d)' : undefined }}>{deniedTotal}</b></span>
        {gw && <span className="muted">另有模型调用 {gw.n} 次（走内部网关，不外发）</span>}
      </div>
      <div className="muted" style={{ marginBottom: 10, fontSize: 12, lineHeight: 1.6 }}>
        所有对外请求经出口代理审计；模型对话经内部安全网关转发，
        真实 API 凭证不出控制域。点域名行查看请求时间点。
      </div>
      <table className="kv-table" style={{ width: '100%' }}>
        <thead><tr><th>域名</th><th style={{ width: 70 }}>次数</th><th style={{ width: 90 }}>判定</th><th style={{ width: 90 }}>最近</th></tr></thead>
        <tbody>
          {hosts.length === 0 && (
            <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 16 }}>
              （窗口内没有对外请求——模型调用不算外发）
            </td></tr>
          )}
          {hosts.map(([h, v]) => (
            <>
              <tr key={h} style={{ cursor: 'pointer' }} onClick={() => setExpanded(expanded === h ? null : h)}>
                <td>
                  <span style={{ marginRight: 6, display: 'inline-block', transition: '.15s', transform: expanded === h ? 'rotate(90deg)' : undefined }}>›</span>
                  {h}
                </td>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{v.n}</td>
                <td style={{ color: v.denied ? 'var(--accent,#e5484d)' : '#3aa675' }}>
                  {v.denied ? `拒绝 ${v.denied}/${v.n}` : '放行'}
                </td>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{v.last.slice(11, 19)}</td>
              </tr>
              {expanded === h && (
                <tr key={h + '-x'}>
                  <td colSpan={4} className="muted" style={{ fontSize: 12, padding: '4px 12px' }}>
                    {v.times.slice(0, 20).join(' · ')}{v.times.length > 20 ? ' …' : ''}
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
      {allow.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>白名单（其余域名一律拒绝）</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {allow.map(a => (
              <span key={a} className="muted" style={{ fontSize: 12, border: '1px solid var(--border,#333)', borderRadius: 999, padding: '2px 10px' }}>
                {a}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

