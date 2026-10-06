// 动作台账页（P8）：它做了什么、做成了什么——工具动作/审批/拦截三源聚合。
// 被拦截待审批 → 「去审批」直达会话（审批卡在消息流里）。
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useStore } from '../../stores/sessions';

interface Act {
  ts: string; sid: string; kind: string; source: string;
  title: string; status: string; duration_s: number | null;
  ref: { approval_id: number } | null;
}

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

export default function ActivityTab() {
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<Act[]>([]);
  const [more, setMore] = useState(false);
  const openSession = useStore(s => s.openSession);

  async function reload(o = 0) {
    const q = new URLSearchParams();
    if (kind) q.set('kind', kind);
    if (status) q.set('status', status);
    q.set('limit', '50'); q.set('offset', String(o));
    const d = await api<{ items: Act[]; has_more: boolean }>(
      `/api/activity?${q.toString()}`);
    setItems(o === 0 ? d.items : [...items, ...d.items]);
    setMore(d.has_more);
    setOffset(o);
  }
  useEffect(() => { void reload(0); }, [kind, status]);   // eslint-disable-line

  return (
    <div className="admin-body">
      <div className="admin-toolbar">
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
        <span className="muted">工具动作/审批/拦截 三源聚合（最近 400 条消息窗口）</span>
      </div>
      <div className="hub-results">
        {items.map((a, i) => (
          <div key={i} className="hub-card slim">
            <div className="sk-head">
              <span className={`sk-src ${STATUS_CLASS[a.status] ?? ''}`}>
                {STATUS_LABEL[a.status] ?? a.status}
              </span>
              <b>{KIND_LABEL[a.kind] ?? a.kind}</b>
              <span className="sk-time">{a.ts?.slice(5, 19).replace('T', ' ')}</span>
            </div>
            <div style={{ fontSize: 13 }}>{a.title}</div>
            <div className="sk-foot">
              <span className="sk-time">{a.sid?.slice(0, 20)}</span>
              <span className="sk-actions">
                <button className="link" onClick={() => {
                  location.hash = '';
                  void openSession(a.sid);
                }}>{a.sid === useStore.getState().currentSid ? '查看会话' : '打开会话'}</button>
                {a.status === 'pending' && a.ref?.approval_id && (
                  <button className="link" onClick={() => {
                    location.hash = '';
                    void openSession(a.sid);   // 审批卡在会话消息流内
                  }}>去审批 →</button>
                )}
              </span>
            </div>
          </div>
        ))}
        {!items.length && <div className="panel-empty">（该过滤下无动作记录）</div>}
      </div>
      {more && (
        <div className="admin-toolbar">
          <button className="btn ghost sm" onClick={() => void reload(offset + 50)}>
            加载更多
          </button>
        </div>
      )}
    </div>
  );
}
