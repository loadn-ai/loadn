// 安全中心（管理中心「安全」tab）：产品视角的 W0-W6 姿态呈现。
// 设计原则：每张卡回答三问——这是什么 / 现在什么状态 / 异常了我该干什么；
// 审计流说人话（类型中文化+详情字段化），内部网关噪声聚合不刷屏。
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useStore } from '../stores/sessions';

interface Posture {
  sandbox: { mode: string; bwrap: number; direct: number; window: number };
  policy: { approval_enforce: string };
  egress: { mode: string; allow_count: number };
  canary: { locked_sessions: { sid: string; reason: string }[]; kill_all: boolean };
  vault: { platforms: number };
  audit: { last_id: number; last_ts: string; anchors: number };
}

interface AuditEvent {
  id: number; ts: string; type: string;
  sid: string | null; detail_json: string;
}

/** 审计类型 → 中文 + 过滤器项（''=全部，'__llm'=内部网关） */
const TYPE_ZH: Record<string, string> = {
  egress_request: '外发请求', snapshot: '任务启动',
  approval_request: '审批请求', approval_decision: '审批决定',
  permission_decision: '权限决定', skill_scan: 'Skill 扫描',
  canary_hit: '蜜罐命中', kill_switch: '熔断', anomaly: '异常',
  sandbox_violation: '沙箱违规', rollback: '回滚', policy_change: '策略变更',
};
const FILTERS: { key: string; label: string }[] = [
  { key: '', label: '全部' },
  { key: '__llm', label: '模型调用' },
  { key: 'egress_request', label: '外发请求' },
  { key: 'snapshot', label: '任务启动' },
  { key: 'approval_request', label: '审批' },
  { key: 'permission_decision', label: '权限' },
  { key: 'canary_hit', label: '蜜罐命中' },
  { key: 'kill_switch', label: '熔断' },
  { key: 'anomaly', label: '异常' },
];

function parse(d: string): Record<string, any> {
  try { return JSON.parse(d); } catch { return {}; }
}

/** 事件 → 一句人话（未知结构回落 k=v 拼接） */
function humanize(e: AuditEvent): string {
  const d = parse(e.detail_json);
  switch (e.type) {
    case 'egress_request': {
      if (d.host === 'llm-gw.internal') return '经安全网关调用模型';
      const verdict = String(d.decision || '').startsWith('allow')
        ? '放行' : '拒绝（不在白名单）';
      return `${d.host} · ${verdict}`;
    }
    case 'snapshot':
      return `${d.engine ?? '?'} 引擎 · ${d.mode === 'bwrap' ? '沙箱内运行' : '⚠ 未隔离直跑'}`;
    case 'approval_request':
      return `${d.action ?? d.summary ?? '敏感操作'} 等待确认码`;
    case 'approval_decision':
      return `${d.action ?? '敏感操作'} · ${d.decision === 'approved' ? '已批准' : '已拒绝'}`;
    case 'canary_hit':
      return `诱饵凭证被盗用（${d.subject ?? '?'}）——已自动熔断`;
    case 'kill_switch':
      return d.action === 'clear' ? '解除全局熔断' : `触发熔断（停 ${d.stopped ?? '?'} 个任务）`;
    default: {
      const parts = Object.entries(d).filter(([, v]) => typeof v !== 'object')
        .slice(0, 3).map(([k, v]) => `${k}=${String(v).slice(0, 24)}`);
      return parts.join(' ') || '-';
    }
  }
}

/** 连续的内部网关事件折叠成一行（防 llm-gw 刷屏） */
function aggregate(events: AuditEvent[]): (AuditEvent & { count?: number })[] {
  const out: (AuditEvent & { count?: number })[] = [];
  for (const e of events) {
    const d = parse(e.detail_json);
    const isLlm = e.type === 'egress_request' && d.host === 'llm-gw.internal';
    const prev = out[out.length - 1];
    if (isLlm && prev && prev.count && parse(prev.detail_json).host === 'llm-gw.internal') {
      prev.count += 1;                     // 相邻才合并（保留时间线语义）
    } else {
      out.push(isLlm ? { ...e, count: 1 } : e);
    }
  }
  return out;
}

function Dot({ ok, warn }: { ok: boolean; warn?: boolean }) {
  const color = ok ? (warn ? '#d4a017' : '#3aa675') : '#888';
  return <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: color, marginRight: 6, flexShrink: 0 }} />;
}

const card = { border: '1px solid var(--border,#333)', borderRadius: 8, padding: '10px 12px' };

export default function SecurityPanel({ onClose }: { onClose: () => void }) {
  const [posture, setPosture] = useState<Posture | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState('');
  const [verify, setVerify] = useState<{ ok: boolean; text: string } | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [armed, setArmed] = useState(false);        // 熔断两段式确认
  const [busy, setBusy] = useState('');
  const armTimer = useRef<number | null>(null);
  const openSession = useStore(s => s.openSession);

  const load = async () => {
    try {
      setPosture(await api<Posture>('/api/admin/security'));
      setEvents((await api<{ events: AuditEvent[] }>('/api/admin/audit?n=60')).events);
    } catch { /* 忽略（403 会走 TokenGate） */ }
  };
  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 10000);
    return () => { clearInterval(t); if (armTimer.current) clearTimeout(armTimer.current); };
  }, []);

  const doVerify = async () => {
    setVerifying(true); setVerify(null);
    try {
      const d = await api<{ problems: string[] }>('/api/admin/audit/verify', { method: 'POST' });
      setVerify(d.problems.length === 0
        ? { ok: true, text: `账本完整：哈希链逐行一致，${posture?.audit.anchors ?? '?'} 个日锚点全部命中` }
        : { ok: false, text: `发现 ${d.problems.length} 处异常：\n${d.problems.slice(0, 5).join('\n')}` });
    } catch (e) {
      setVerify({ ok: false, text: `校验请求失败：${e}\n（若提示 admin required，输入 token 后重试）` });
    } finally { setVerifying(false); }
  };

  const doKillAll = async () => {
    if (!armed) {                          // 第一段：进入确认态，5s 超时回退
      setArmed(true);
      armTimer.current = window.setTimeout(() => setArmed(false), 5000);
      return;
    }
    if (armTimer.current) clearTimeout(armTimer.current);
    setArmed(false); setBusy('正在熔断…');
    try {
      const d = await api<{ stopped_turns: number }>('/api/admin/kill-all', { method: 'POST' });
      setVerify(null);
      alert(`已熔断：停止 ${d.stopped_turns} 个任务，调度暂停，新任务被拒。\n排查完成后点「解除熔断」恢复。`);
    } catch (e) { alert(`熔断失败: ${e}`); }
    finally { setBusy(''); void load(); }
  };

  const doClear = async () => {
    setBusy('正在恢复…');
    try {
      const d = await api<{ unlocked: string[]; kept_locked: { sid: string }[] }>('/api/admin/kill-all/clear', { method: 'POST' });
      const kept = d.kept_locked?.length
        ? `\n注意：${d.kept_locked.length} 个会话因蜜罐命中保持锁定（真实警报，需人工核后解锁）` : '';
      alert(`已恢复：调度继续，解锁 ${d.unlocked?.length ?? 0} 个会话。${kept}`);
    } catch (e) { alert(`恢复失败: ${e}`); }
    finally { setBusy(''); void load(); }
  };

  const jump = (sid: string | null) => {
    if (!sid) return;
    onClose();
    void openSession(sid);
  };

  if (!posture) return <div className="pad muted">加载中…</div>;

  const sandboxOk = posture.sandbox.mode === 'bwrap';
  const policyOk = posture.policy.approval_enforce === 'enforce';
  const policyWarn = posture.policy.approval_enforce === 'warn';
  const egressOk = posture.egress.mode === 'enforce';
  const egressWarn = posture.egress.mode === 'warn';
  const cards = [
    {
      name: '沙箱隔离', ok: sandboxOk, icon: '📦',
      top: sandboxOk ? `已隔离 · 近 ${posture.sandbox.window} 个任务全覆盖`
        : '未启用（直跑）',
      sub: 'AI 执行的命令被关在隔离环境里，碰不到系统其它文件与真实网络。',
      hint: sandboxOk ? undefined : '在 config.yaml 的 security.sandbox 设为 bwrap 后重启。',
    },
    {
      name: '敏感操作审批', ok: policyOk, warn: policyWarn, icon: '🔐',
      top: policyOk ? '强制（高危操作须输确认码）' : policyWarn ? '仅告警（不拦截）' : posture.policy.approval_enforce,
      sub: '发邮件、动账号、付款类操作执行前需要你输确认码放行，AI 无法自行通过。',
      hint: policyOk ? undefined : '当前不拦截：在 config.yaml security.approval_enforce 设为 enforce。',
    },
    {
      name: '网络出口管控', ok: egressOk, warn: egressWarn, icon: '🌐',
      top: `${egressOk ? '强制' : '告警'} · 白名单 ${posture.egress.allow_count} 个域`,
      sub: 'AI 的所有对外请求经过代理，白名单之外的域名一律拒绝；模型调用走内部网关，凭证不进沙箱。',
    },
    {
      name: '凭证保险库', ok: posture.vault.platforms > 0, icon: '🗝️',
      top: `${posture.vault.platforms} 组账号 · AES-GCM 加密`,
      sub: '各类账号密码加密落盘，明文不出现在代码、配置和日志里。',
      hint: posture.vault.platforms > 0 ? undefined : '尚无凭证入库：用 loadn-web r account --set 添加。',
    },
    {
      name: '审计账本', ok: true, icon: '🧾',
      top: `防篡改 · 已记 ${posture.audit.last_id} 条 · ${posture.audit.anchors} 个锚点`,
      sub: '敏感操作全部入链式账本，任何人（包括管理员）改一行都会被校验发现。',
    },
    {
      name: '蜜罐诱饵', ok: !posture.canary.kill_all, icon: '🪤',
      top: posture.canary.kill_all ? '全局熔断激活中！'
        : posture.canary.locked_sessions.length
          ? `${posture.canary.locked_sessions.length} 个会话触发警报`
          : '哨兵正常 · 未命中',
      sub: '每个会话埋了假凭证：提示词注入或人为盗用一旦使用，立刻熔断该会话。',
      danger: posture.canary.kill_all || posture.canary.locked_sessions.length > 0,
    },
  ];

  const isLlmEvent = (e: AuditEvent) =>
    e.type === 'egress_request' && parse(e.detail_json).host === 'llm-gw.internal';
  const visible = events.filter(e => {
    if (filter === '__llm') return isLlmEvent(e);          // 明细：逐条看
    if (filter === 'approval_request')
      return e.type === 'approval_request' || e.type === 'approval_decision';
    if (filter) return e.type === filter;
    return true;                                           // 全部：网关行走聚合
  });
  const rows: (AuditEvent & { count?: number })[] =
    filter === '__llm' ? visible : aggregate(visible);

  return (
    <div className="pad">
      {/* ---- 姿态卡 ---- */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(290px, 1fr))', gap: 10, marginBottom: 14 }}>
        {cards.map(c => (
          <div key={c.name} style={{ ...card, borderColor: c.danger ? 'var(--accent,#e5484d)' : undefined }}>
            <div style={{ display: 'flex', alignItems: 'center', fontWeight: 600 }}>
              <Dot ok={c.ok} warn={c.warn} /><span style={{ marginRight: 6 }}>{c.icon}</span>{c.name}
            </div>
            <div style={{ fontSize: 13, marginTop: 4, color: c.ok || c.danger ? undefined : 'var(--muted)' }}>{c.top}</div>
            <div className="muted" style={{ fontSize: 12, marginTop: 6, lineHeight: 1.5 }}>{c.sub}</div>
            {c.hint && <div style={{ fontSize: 12, marginTop: 6, color: '#d4a017' }}>→ {c.hint}</div>}
          </div>
        ))}
      </div>

      {/* ---- 操作区 ---- */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', flexWrap: 'wrap', marginBottom: 14 }}>
        <button onClick={doVerify} disabled={verifying}>
          {verifying ? '校验中…' : '校验账本是否被篡改'}
        </button>
        {posture.canary.kill_all ? (
          <button style={{ borderColor: 'var(--accent,#e5484d)', color: 'var(--accent,#e5484d)' }} onClick={doClear} disabled={!!busy}>
            {busy || '解除熔断，恢复运行'}
          </button>
        ) : (
          <button
            style={armed
              ? { background: 'var(--accent,#e5484d)', color: '#fff' }
              : { borderColor: 'var(--accent,#e5484d)', color: 'var(--accent,#e5484d)' }}
            onClick={doKillAll} disabled={!!busy}>
            {busy || (armed ? '⚠ 再点一次确认熔断（5 秒内）' : '全局紧急停止')}
          </button>
        )}
        {verify && (
          <div style={{
            flex: 1, minWidth: 260, padding: '8px 12px', borderRadius: 8, fontSize: 13,
            whiteSpace: 'pre-wrap', lineHeight: 1.5,
            border: `1px solid ${verify.ok ? '#3aa675' : 'var(--accent,#e5484d)'}`,
            color: verify.ok ? '#3aa675' : 'var(--accent,#e5484d)',
          }}>{verify.text}</div>
        )}
      </div>
      {posture.canary.kill_all && (
        <div style={{ ...card, borderColor: 'var(--accent,#e5484d)', marginBottom: 14, fontSize: 13 } as never}>
          ⚠ <b>全局熔断激活中</b>：调度已暂停、新任务被拒、原有任务已停止。这是紧急刹车，
          排查完原因后点上方「解除熔断」恢复。
        </div>
      )}

      {/* ---- 审计事件流 ---- */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13 }}>操作记录</b>
        <span className="muted" style={{ fontSize: 12 }}>
          全部敏感动作的防篡改流水（{posture.audit.last_id} 条中的最近一段）
        </span>
        <select value={filter} onChange={e => setFilter(e.target.value)}
          style={{ marginLeft: 'auto', padding: '4px 8px', borderRadius: 6 }}>
          {FILTERS.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
      </div>
      <table className="kv-table" style={{ width: '100%' }}>
        <thead><tr><th style={{ width: 96 }}>时间</th><th style={{ width: 90 }}>类型</th><th style={{ width: 130 }}>会话</th><th>内容</th></tr></thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 16 }}>
              （该类型暂无记录）
            </td></tr>
          )}
          {rows.map(r => {
            const d = parse(r.detail_json);
            const isLlm = r.type === 'egress_request' && d.host === 'llm-gw.internal';
            return (
              <tr key={r.id} style={isLlm ? { opacity: 0.6 } : undefined}>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(r.ts || '').slice(5, 19).replace('T', ' ')}</td>
                <td>{isLlm ? '模型调用' : (TYPE_ZH[r.type] ?? r.type)}</td>
                <td>
                  {r.sid ? (
                    <a onClick={() => jump(r.sid)} style={{ cursor: 'pointer' }}
                      title="打开该会话">{r.sid.slice(0, 18)}…</a>
                  ) : '—'}
                </td>
                <td>{r.count ? humanize(r) + ` × ${r.count}` : humanize(r)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
