// 审计与动作页（原动作台账 P8）：它做了什么、做成了什么——工具动作/审批/拦截
// 三源聚合（运营台账）+ 防篡改审计账本双源统一视图（AC-2.4）。
// 被拦截待审批 → 「打开会话审批」直达会话（审批卡在消息流里）。
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useStore } from '../../stores/sessions';

interface Act {
  ts: string; sid: string; kind: string; source: string;
  title: string; status: string; duration_s: number | null;
  ref: { approval_id: number } | null;
}
interface AuditEvent { id: number; ts: string; type: string; detail_json: string }
type Row =
  | { src: 'ops'; ts: string; act: Act }
  | { src: 'audit'; ts: string; ev: AuditEvent };

const KIND_LABEL: Record<string, string> = {
  bash: 'bash', file: '文件', net: '网络', tool: '工具', approval: '审批',
};
const STATUS_LABEL: Record<string, string> = {
  done: '完成', error: '失败', pending: '待审批', denied: '已否决', blocked: '被拦截',
};
const STATUS_CLASS: Record<string, string> = {
  done: 'local', error: 'off-tag', pending: 'ext', denied: 'off-tag',
  blocked: 'off-tag',
};
const SRC_LABEL = { ops: '台账', audit: '审计' } as const;

// BC-1（AC-2.4 backlog）：当前过滤+已加载窗口导出 CSV（前端 blob，带 BOM 兼容
// Excel 中文）；审计/台账双源同格式，列：时间/来源/类型状态/内容/会话或ID
function exportCsv(rows: Row[]) {
  const esc = (v: unknown) => {
    const s = String(v ?? '').replace(/"/g, '""');
    return /[",\n]/.test(s) ? `"${s}"` : s;
  };
  const lines = ['时间,来源,类型/状态,内容,会话/ID'];
  for (const r of rows) {
    if (r.src === 'ops') {
      lines.push([r.act.ts, '台账',
        `${KIND_LABEL[r.act.kind] ?? r.act.kind}/${STATUS_LABEL[r.act.status] ?? r.act.status}`,
        r.act.title, r.act.sid].map(esc).join(','));
    } else {
      lines.push([r.ev.ts, '审计', r.ev.type, auditTitle(r.ev), `#${r.ev.id}`].map(esc).join(','));
    }
  }
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `activity-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function auditTitle(ev: AuditEvent): string {
  try {
    const d = JSON.parse(ev.detail_json || '{}');
    const head = d.action ? `${d.action} ` : '';
    const body = d.platform ?? d.host ?? d.path ?? d.name ?? d.sid?.slice(0, 12) ?? '';
    const extra = d.fields ? `（${Array.isArray(d.fields) ? d.fields.join('/') : d.fields}）` : '';
    return `${head}${body}${extra}`.trim() || ev.type;
  } catch { return ev.type; }
}

export default function ActivityTab() {
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [src, setSrc] = useState<'ops' | 'audit' | 'both'>('ops');
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<Act[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [more, setMore] = useState(false);
  const [err, setErr] = useState('');
  const openSession = useStore(s => s.openSession);

  async function reload(o = 0) {
    setErr('');
    const jobs: Promise<void>[] = [];
    if (src !== 'audit') {
      const q = new URLSearchParams();
      if (kind) q.set('kind', kind);
      if (status) q.set('status', status);
      q.set('limit', '50'); q.set('offset', String(o));
      jobs.push(api<{ items: Act[]; has_more: boolean }>(`/api/activity?${q.toString()}`)
        .then(d => { setItems(o === 0 ? d.items : [...items, ...d.items]); setMore(d.has_more); setOffset(o); })
        .catch(e => { setErr(`台账加载失败：${String(e)}`); }));
    }
    if (src !== 'ops') {
      jobs.push(api<{ events: AuditEvent[] }>('/api/admin/audit?n=100')
        .then(d => setAudit(d.events))
        .catch(e => { setErr(`审计账本加载失败：${String(e)}`); }));
    }
    await Promise.all(jobs);
  }
  useEffect(() => { void reload(0); }, [kind, status, src]);   // eslint-disable-line

  const rows: Row[] = src === 'audit'
    ? audit.map(ev => ({ src: 'audit', ts: ev.ts, ev }))
    : src === 'ops'
      ? items.map(a => ({ src: 'ops', ts: a.ts, act: a }))
      : [...items.map(a => ({ src: 'ops' as const, ts: a.ts, act: a })),
         ...audit.map(ev => ({ src: 'audit' as const, ts: ev.ts, ev }))]
          .sort((x, y) => y.ts.localeCompare(x.ts));

  return (
    <div className="admin-body">
      <div className="admin-toolbar">
        <select value={src} onChange={e => setSrc(e.target.value as 'ops' | 'audit' | 'both')}>
          <option value="ops">运营台账</option>
          <option value="audit">安全审计账本</option>
          <option value="both">双源合并</option>
        </select>
        {src !== 'audit' && <>
          <select value={kind} onChange={e => setKind(e.target.value)}>
            <option value="">全部类型</option>
            {Object.entries(KIND_LABEL).map(([v, l]) =>
              <option key={v} value={v}>{l}</option>)}
          </select>
          <select value={status} onChange={e => setStatus(e.target.value)}>
            <option value="">全部状态</option>
            {Object.entries(STATUS_LABEL).map(([v, l]) =>
              <option key={v} value={v}>{l}</option>)}
          </select>
        </>}
        <span className="muted">
          台账=运营视图（工具动作/审批/拦截）· 审计=防篡改哈希链账本（AC-2.4 合并）
        </span>
        <button className="btn sm" title="导出当前过滤+已加载窗口为 CSV（含双源合并）"
                onClick={() => exportCsv(rows)}>导出 CSV</button>
      </div>
      {err && <div className="admin-msg err" style={{ marginBottom: 10 }}>{err}
        <button className="link" style={{ marginLeft: 8 }} onClick={() => void reload(0)}>重试</button>
      </div>}
      <div className="admin-list">
        {rows.map((r, i) => r.src === 'ops' ? (
          <div key={i} className="hub-card slim">
            <div className="sk-head">
              <span className={`sk-src ${STATUS_CLASS[r.act.status] ?? ''}`}>
                {STATUS_LABEL[r.act.status] ?? r.act.status}
              </span>
              <b>{KIND_LABEL[r.act.kind] ?? r.act.kind}</b>
              <span className="sk-src">{SRC_LABEL[r.src]}</span>
              <span className="sk-time">{r.act.ts?.slice(5, 19).replace('T', ' ')}</span>
            </div>
            <div style={{ fontSize: 13 }}>{r.act.title}</div>
            <div className="sk-foot">
              <span className="sk-time">{r.act.sid?.slice(0, 20)}</span>
              <span className="sk-actions">
                <button className="link" onClick={() => {
                  location.hash = '';
                  void openSession(r.act.sid);
                }}>{r.act.status === 'pending' && r.act.ref?.approval_id
                  ? '打开会话审批 →'
                  : r.act.sid === useStore.getState().currentSid ? '查看会话' : '打开会话'}</button>
              </span>
            </div>
          </div>
        ) : (
          <div key={i} className="hub-card slim">
            <div className="sk-head">
              <span className="sk-src ext">审计</span>
              <b className="mono" style={{ fontSize: 12 }}>{r.ev.type}</b>
              <span className="sk-src">{SRC_LABEL[r.src]}</span>
              <span className="sk-time">{r.ev.ts?.slice(5, 19).replace('T', ' ')}</span>
            </div>
            <div style={{ fontSize: 13 }} title={r.ev.detail_json}>{auditTitle(r.ev)}</div>
            <div className="sk-foot">
              <span className="sk-time mono">#{r.ev.id}</span>
            </div>
          </div>
        ))}
        {!rows.length && !err && <div className="panel-empty">（该过滤下无记录）</div>}
      </div>
      {more && src !== 'audit' && (
        <div className="admin-toolbar">
          <button className="btn ghost sm" onClick={() => void reload(offset + 50)}>
            加载更多
          </button>
        </div>
      )}
    </div>
  );
}
