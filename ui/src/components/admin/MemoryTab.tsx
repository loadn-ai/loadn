// 记忆管理页（P6）：域 tab + 左条目列表 + 右编辑器（源码/预览双模式）+
// 历史侧栏（版本查看/恢复）。存储经 /api/memory（后端过 memorystore：
// 护栏重跑 + 保存即 commit + 删除留史可恢复）。
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../../api/client';
import { Plus } from '../icons';

interface Entry { id: string; summary: string; content?: string; origin_session: string; created_at: string }
interface Ver { hash: string; date: string; subject: string }

export default function MemoryTab() {
  const [domains, setDomains] = useState<string[]>([]);
  const [domain, setDomain] = useState('user');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [sel, setSel] = useState<Entry | null>(null);
  const [text, setText] = useState('');
  const [summary, setSummary] = useState('');
  const [preview, setPreview] = useState(false);
  const [hist, setHist] = useState<Ver[]>([]);
  const [verText, setVerText] = useState('');
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState('');

  async function reloadDomains() {
    const d = await api<{ domains: string[] }>('/api/memory/domains');
    setDomains(d.domains);
  }
  async function reloadEntries(dom = domain) {
    try {
      const d = await api<{ entries: Entry[] }>(`/api/memory/entries?domain=${dom}`);
      setEntries(d.entries.reverse());   // 新在前
    } catch { setEntries([]); }
  }
  useEffect(() => { void reloadDomains(); }, []);
  useEffect(() => { setSel(null); void reloadEntries(domain); }, [domain]);

  function pick(e: Entry) {
    setSel(e); setSummary(e.summary); setPreview(false); setVerText('');
    void (async () => {
      const d = await api<{ text: string }>(
        `/api/memory/file?domain=${domain}&id=${e.id}`);
      const body = d.text.replace(/^---\n[\s\S]*?\n---\n/, '').trim();
      setText(body);
      const h = await api<{ history: Ver[] }>(
        `/api/memory/history?domain=${domain}&id=${e.id}`);
      setHist(h.history);
    })();
  }

  async function save() {
    if (!sel) return;
    try {
      await api('/api/memory/file', { method: 'PUT', body: JSON.stringify({
        domain, id: sel.id, content: text, summary }) });
      setMsg('已保存（一次 commit，历史可回溯）');
      pick(sel); void reloadEntries();
    } catch (e) { setMsg(`保存被拒：${String(e)}`); }   // 护栏拒绝原因直显
  }

  async function del(e: Entry) {
    if (!confirm(`删除记忆「${e.summary}」？git 历史保留（可经历史恢复）。`)) return;
    try {
      await api(`/api/memory/entry?domain=${domain}&id=${e.id}`, { method: 'DELETE' });
      setSel(null); setMsg(`已删除 ${e.summary}（历史在，可恢复）`);
      void reloadEntries();
    } catch (err) { setMsg(`删除失败：${String(err)}`); }
  }

  async function restore(v: Ver) {
    if (!sel) return;
    try {
      await api('/api/memory/restore', { method: 'POST', body: JSON.stringify({
        domain, id: sel.id, ref: v.hash }) });
      setMsg(`已恢复到 ${v.hash}（${v.subject}）`);
      pick(sel);
    } catch (e) { setMsg(`恢复失败：${String(e)}`); }
  }

  async function showVer(v: Ver) {
    const d = await api<{ text: string }>(
      `/api/memory/version?domain=${domain}&id=${sel?.id}&ref=${v.hash}`);
    setVerText(d.text.replace(/^---\n[\s\S]*?\n---\n/, '').trim());
  }

  return (
    <div className="admin-body">
      <div className="admin-toolbar">
        {domains.map(d => (
          <button key={d} className={`btn sm ${d === domain ? 'primary' : 'ghost'}`}
            onClick={() => setDomain(d)}>{d === 'user' ? '用户级（跨项目）' : d}</button>
        ))}
        <button className="btn sm" onClick={() => setCreating(v => !v)}>
          <Plus size={13} /> 新建
        </button>
        {msg && <span className="admin-msg">{msg}</span>}
      </div>
      {creating && <NewEntryForm domain={domain} onDone={() => {
        setCreating(false); void reloadEntries();
      }} />}
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <div className="hub-results" style={{ flex: 1 }}>
          {entries.map(e => (
            <div key={e.id} className={`hub-card slim${sel?.id === e.id ? ' on' : ''}`}
              onClick={() => pick(e)} role="button">
              <b>{e.summary}</b>
              <div className="sk-foot">
                <span className="sk-time">{e.origin_session.slice(0, 18)} · {e.created_at?.slice(5, 16)}</span>
                <span className="sk-actions">
                  <button className="link danger-link" onClick={ev => {
                    ev.stopPropagation(); void del(e);
                  }}>删除</button>
                </span>
              </div>
            </div>
          ))}
          {!entries.length && <div className="panel-empty">（该域暂无记忆）</div>}
        </div>
        <div style={{ flex: 2 }}>
          {sel ? (
            <>
              <input value={summary} onChange={e => setSummary(e.target.value)}
                placeholder="摘要（注入索引用）" />
              <div className="admin-toolbar">
                <button className={`btn sm ${preview ? 'ghost' : 'primary'}`}
                  onClick={() => setPreview(false)}>源码</button>
                <button className={`btn sm ${preview ? 'primary' : 'ghost'}`}
                  onClick={() => setPreview(true)}>预览</button>
                <button className="btn sm primary" onClick={() => void save()}>保存</button>
              </div>
              {preview
                ? <div className="md-preview"><ReactMarkdown>{text}</ReactMarkdown></div>
                : <textarea className="mono" rows={14} value={text}
                    onChange={e => setText(e.target.value)} />}
            </>
          ) : <div className="panel-empty">左侧选择一条记忆查看/编辑</div>}
        </div>
        <div className="hub-results" style={{ flex: 1 }}>
          <b>历史</b>
          {hist.map(v => (
            <div key={v.hash} className="hub-card slim">
              <div className="mono" style={{ fontSize: 11 }}>{v.hash} · {v.date}</div>
              <div style={{ fontSize: 12 }}>{v.subject}</div>
              <span className="sk-actions">
                <button className="link" onClick={() => void showVer(v)}>查看</button>
                <button className="link" onClick={() => void restore(v)}>恢复</button>
              </span>
            </div>
          ))}
          {sel && !hist.length && <div className="panel-empty">（无 git 历史——旧条目或 git 降级）</div>}
          {verText && <pre className="mono" style={{
            fontSize: 11, whiteSpace: 'pre-wrap', background: 'var(--bg2, #f6f6f6)',
            padding: 8, borderRadius: 6 }}>{verText}</pre>}
        </div>
      </div>
    </div>
  );
}


function NewEntryForm({ domain, onDone }: { domain: string; onDone: () => void }) {
  const [summary, setSummary] = useState('');
  const [content, setContent] = useState('');
  const [err, setErr] = useState('');
  return (
    <div className="new-skill-form">
      <input placeholder="摘要（≤20 字，注入索引按它检索）" value={summary}
        onChange={e => setSummary(e.target.value)} />
      <textarea className="mono" rows={4} placeholder="记忆正文（蜜罐/凭证护栏同守）"
        value={content} onChange={e => setContent(e.target.value)} />
      <div className="modal-foot">
        <button className="btn ghost sm" onClick={onDone}>取消</button>
        <button className="btn primary sm" disabled={!summary.trim() || !content.trim()}
          onClick={() => void (async () => {
            try {
              await api('/api/memory/file', { method: 'POST', body: JSON.stringify({
                domain, summary: summary.trim(), content }) });
              onDone();
            } catch (e) { setErr(String(e)); }
          })()}>创建</button>
      </div>
      {err && <div className="admin-err">{err}</div>}
    </div>
  );
}
