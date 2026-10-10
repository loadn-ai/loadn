// 任务管理页（全量任务=会话）：聚合指标（轮数/消息/产物/token/费用/磁盘
// 占用）+ 多维筛选排序 + 批量操作（移动到分类/置顶/收藏、归档、恢复、
// 删除、彻底删除）。批量走既有单任务端点逐个执行（无批量 API——服务端
// 保持单任务语义，前端聚合进度）。
import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import { useStore } from '../../stores/sessions';
import { Archive, Undo, Trash, Pin, Star } from '../icons';

interface TaskRow {
  id: string; title: string; status: string; engine: string | null;
  profile: string | null; project_id: string | null;
  project_title: string | null; category_id: number | null;
  pinned: boolean; starred: boolean;
  n_turns: number; n_messages: number; n_artifacts: number;
  tokens: number; cost_usd: number; size_bytes: number;
  running: boolean; created_at: string | null; updated_at: string | null;
}

const PAGE = 50;

function fmtBytes(b: number): string {
  if (b >= 1 << 30) return `${(b / (1 << 30)).toFixed(1)}G`;
  if (b >= 1 << 20) return `${(b / (1 << 20)).toFixed(1)}M`;
  if (b >= 1 << 10) return `${(b / (1 << 10)).toFixed(0)}K`;
  return `${b}B`;
}

function fmtTokensShort(t: number): string {
  if (t >= 1e9) return `${(t / 1e9).toFixed(2)}B`;
  if (t >= 1e6) return `${(t / 1e6).toFixed(1)}M`;
  if (t >= 1e3) return `${(t / 1e3).toFixed(0)}K`;
  return String(t);
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

export default function TasksTab() {
  const [rows, setRows] = useState<TaskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('all');
  const [projectId, setProjectId] = useState('');
  const [categoryId, setCategoryId] = useState(0);
  const [engineSel, setEngineSel] = useState('');
  const [sort, setSort] = useState('updated');
  const [dir, setDir] = useState<'asc' | 'desc'>('desc');
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState('');
  const [limit, setLimit] = useState(PAGE);

  const projects = useStore(s => s.projects);
  const categories = useStore(s => s.categories);
  const openSession = useStore(s => s.openSession);

  async function reload() {
    setLoading(true);
    try {
      const p = new URLSearchParams({ status, sort, dir });
      if (q.trim()) p.set('q', q.trim());
      if (projectId) p.set('project_id', projectId);
      if (categoryId) p.set('category_id', String(categoryId));
      if (engineSel) p.set('engine', engineSel);
      const d = await api<{ tasks: TaskRow[] }>(`/api/admin/tasks?${p}`);
      setRows(d.tasks);
    } catch (e) {
      alert(`加载失败：${e instanceof Error ? e.message : e}`);
    } finally { setLoading(false); }
  }
  useEffect(() => { void reload(); }, []);   // eslint-disable-line
  // 筛选变化去抖重拉（q 输入不打接口风暴）
  useEffect(() => {
    const t = setTimeout(() => void reload(), 300);
    return () => clearTimeout(t);
  }, [q, status, projectId, categoryId, engineSel, sort, dir]);   // eslint-disable-line

  const engines = useMemo(
    () => [...new Set(rows.map(r => r.engine).filter(Boolean))] as string[],
    [rows]);
  const shown = rows.slice(0, limit);
  const totals = useMemo(() => ({
    n: rows.length,
    turns: rows.reduce((a, r) => a + r.n_turns, 0),
    tokens: rows.reduce((a, r) => a + r.tokens, 0),
    cost: rows.reduce((a, r) => a + r.cost_usd, 0),
    size: rows.reduce((a, r) => a + r.size_bytes, 0),
    running: rows.filter(r => r.running).length,
  }), [rows]);
  const selRows = rows.filter(r => sel.has(r.id));

  const toggle = (id: string) => setSel(s => {
    const n = new Set(s);
    n.has(id) ? n.delete(id) : n.add(id);
    return n;
  });
  const allShownSel = shown.length > 0 && shown.every(r => sel.has(r.id));

  /** 批量执行：逐个走既有单任务端点；结束后重拉 */
  async function bulk(label: string, fn: (id: string) => Promise<unknown>,
                      confirmText?: string) {
    if (!selRows.length) return;
    if (confirmText && !confirm(confirmText.replace('{n}', String(selRows.length)))) return;
    setBusy(`${label} 0/${selRows.length}`);
    let done = 0, failed = 0;
    for (const r of selRows) {
      try { await fn(r.id); done++; }
      catch { failed++; }
      setBusy(`${label} ${++done + failed - 1}/${selRows.length}`);
    }
    setBusy('');
    setSel(new Set());
    if (failed) alert(`${label}：${done} 成功，${failed} 失败`);
    await reload();
    void useStore.getState().loadSessions();
  }

  const move = (dest: 'pinned' | 'starred' | 'recent' | { cat: number }) => {
    const patch = dest === 'pinned' ? { pinned: 1, starred: 0, category_id: null }
      : dest === 'starred' ? { pinned: 0, starred: 1, category_id: null }
      : dest === 'recent' ? { pinned: 0, starred: 0, category_id: null }
      : { pinned: 0, starred: 0, category_id: dest.cat };
    void bulk('移动', (id) => api(`/api/sessions/${encodeURIComponent(id)}`, {
      method: 'PATCH', body: JSON.stringify(patch) }));
  };

  const th = (key: string, label: string, w?: string) => (
    <th style={w ? { width: w } : undefined}
        className={sort === key ? 'sorted' : ''}
        onClick={() => { setSort(key); setDir(sort === key && dir === 'desc' ? 'asc' : 'desc'); }}>
      {label}{sort === key ? (dir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  );

  return (
    <div className="admin-body">
      <div className="admin-toolbar">
        <input value={q} placeholder="搜索标题/项目…" maxLength={80}
               onChange={e => setQ(e.target.value)} style={{ width: 180 }} />
        <select value={status} onChange={e => setStatus(e.target.value)}>
          <option value="all">全部状态</option>
          <option value="active">进行中</option>
          <option value="archived">已归档</option>
        </select>
        <select value={projectId} onChange={e => setProjectId(e.target.value)}>
          <option value="">全部项目</option>
          <option value="__none">（无项目）</option>
          {projects.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
        </select>
        <select value={categoryId || ''} onChange={e => setCategoryId(Number(e.target.value) || 0)}>
          <option value={0}>全部分类</option>
          {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select value={engineSel} onChange={e => setEngineSel(e.target.value)}>
          <option value="">全部引擎</option>
          {engines.map(e => <option key={e} value={e}>{e}</option>)}
        </select>
        <span className="muted" style={{ marginLeft: 'auto' }}>
          {totals.n} 个任务 · {totals.turns} 轮 · {fmtTokensShort(totals.tokens)} tok
          · ${totals.cost.toFixed(2)} · {fmtBytes(totals.size)}
          {totals.running > 0 && ` · ${totals.running} 个在跑`}
        </span>
      </div>

      {selRows.length > 0 && (
        <div className="admin-toolbar bulk-bar">
          <b>已选 {selRows.length} 项：</b>
          <button className="btn sm" onClick={() => move('pinned')}><Pin size={11} /> 置顶</button>
          <button className="btn sm" onClick={() => move('starred')}><Star size={11} /> 收藏</button>
          <select onChange={e => { const v = Number(e.target.value); if (v) move({ cat: v }); e.target.value = ''; }}
                  defaultValue="">
            <option value="">移入分类…</option>
            {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn sm" onClick={() => move('recent')}>移回最近</button>
          <button className="btn sm" onClick={() => void bulk('归档', (id) =>
            api(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }))}>
            <Archive size={11} /> 归档
          </button>
          <button className="btn sm" onClick={() => void bulk('恢复', (id) =>
            api(`/api/sessions/${encodeURIComponent(id)}`, {
              method: 'PATCH', body: JSON.stringify({ status: 'active' }) }))}>
            <Undo size={11} /> 恢复
          </button>
          <button className="btn danger sm" onClick={() => void bulk('彻底删除', (id) =>
            api(`/api/sessions/${encodeURIComponent(id)}?purge=true`, { method: 'DELETE' }),
            `彻底删除 {n} 个任务？\n工作区文件一并删除，不可恢复。`)}
            title="删除 DB 行 + 工作区（不可恢复）">
            <Trash size={11} /> 彻底删除
          </button>
          {busy && <span className="muted">{busy}…</span>}
          <button className="link" style={{ marginLeft: 'auto' }}
                  onClick={() => setSel(new Set())}>取消选择</button>
        </div>
      )}

      <div className="tasks-table-wrap">
        <table className="tasks-table">
          <thead>
            <tr>
              <th style={{ width: 28 }}>
                <input type="checkbox" checked={allShownSel}
                       onChange={e => setSel(() => {
                         if (e.target.checked)
                           return new Set([...sel, ...shown.map(r => r.id)]);
                         const n = new Set(sel);
                         shown.forEach(r => n.delete(r.id));
                         return n;
                       })} />
              </th>
              {th('title', '任务')}
              <th>项目 / 分类</th>
              {th('turns', '轮数')}
              <th>消息</th>
              {th('tokens', 'tokens')}
              {th('cost', '费用')}
              {th('size', '占用空间')}
              <th>引擎</th>
              {th('created', '创建时间')}
              {th('updated', '最近更新')}
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={12} className="muted" style={{ padding: 20 }}>加载中…</td></tr>}
            {!loading && shown.length === 0 && (
              <tr><td colSpan={12} className="muted" style={{ padding: 20 }}>没有匹配的任务</td></tr>)}
            {shown.map(r => (
              <tr key={r.id} className={sel.has(r.id) ? 'sel' : ''}>
                <td><input type="checkbox" checked={sel.has(r.id)}
                           onChange={() => toggle(r.id)} /></td>
                <td className="tt-title" title={r.id}>
                  <a className="link" onClick={() => { location.hash = '';
                    void openSession(r.id); }}>{r.title}</a>
                  {r.running && <span className="row-status run" style={{ display: 'inline-flex', marginLeft: 6 }} />}
                  {r.pinned ? <span style={{ marginLeft: 4 }}><Pin size={10} className="row-flag pin" /></span> : null}
                  {r.starred ? <span style={{ marginLeft: 4 }}><Star size={10} filled className="row-flag star" /></span> : null}
                </td>
                <td className="muted">
                  {r.project_title ?? '—'}
                  {r.category_id
                    ? ` / ${categories.find(c => c.id === r.category_id)?.name ?? '#' + r.category_id}` : ''}
                </td>
                <td>{r.n_turns}</td>
                <td>{r.n_messages}</td>
                <td>{r.tokens ? fmtTokensShort(r.tokens) : '—'}</td>
                <td>{r.cost_usd ? `$${r.cost_usd.toFixed(2)}` : '—'}</td>
                <td>{r.size_bytes ? fmtBytes(r.size_bytes) : '—'}</td>
                <td className="muted">{r.engine ?? '—'}</td>
                <td className="muted">{fmtDate(r.created_at)}</td>
                <td className="muted">{fmtDate(r.updated_at)}</td>
                <td>
                  {r.status === 'archived'
                    ? <span className="art-src">已归档</span>
                    : <span className="art-src">进行中</span>}
                  {r.n_artifacts > 0 && <span className="muted" style={{ marginLeft: 4 }}>· {r.n_artifacts} 产物</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!loading && rows.length > limit && (
        <button className="btn ghost sm" style={{ marginTop: 8 }}
                onClick={() => setLimit(l => l + PAGE)}>
          更多（还有 {rows.length - limit} 个）
        </button>
      )}
      <div className="muted" style={{ marginTop: 8, fontSize: 'var(--fs-md)' }}>
        磁盘占用含工作区全部文件（node_modules/chrome 等重目录除外），缓存 10 分钟。
        彻底删除含工作区；归档可恢复。
      </div>
    </div>
  );
}
