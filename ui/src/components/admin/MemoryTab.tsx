// 记忆管理页（P6）：域 tab + 左条目列表 + 右编辑器（源码/预览双模式）+
// 历史侧栏（版本查看/恢复）。存储经 /api/memory（后端过 memorystore：
// 护栏重跑 + 保存即 commit + 删除留史可恢复）。
import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../../api/client';
import { useAdminDirty } from '../../stores/adminDirty';
import { Plus } from '../icons';

interface Entry { id: string; summary: string; content?: string; origin_session: string; created_at: string; draft?: boolean }
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
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  // AC-5.3 竞态守卫：快速切条目/切域/点版本时旧响应后到覆盖新状态——
  // 各异步面独立递增序号，响应回程序号不匹配即丢弃
  const pickSeq = useRef(0);
  const entriesSeq = useRef(0);
  const verSeq = useRef(0);
  // AC-5.3 dirty：编辑器态 vs 加载快照（切条目/切域 confirm + 上报管理页登记）
  const [orig, setOrig] = useState({ text: '', summary: '' });
  const dirty = !!sel && (text !== orig.text || summary !== orig.summary);
  useEffect(() => {
    useAdminDirty.getState().setDirty('memory', dirty);
    return () => useAdminDirty.getState().setDirty('memory', false);
  }, [dirty]);

  async function reloadDomains() {
    try {
      const d = await api<{ domains: string[] }>('/api/memory/domains');
      setDomains(d.domains);
    } catch (e) { setMsg({ t: `域列表加载失败：${String(e)}`, err: true }); }
  }
  async function reloadEntries(dom = domain) {
    const seq = ++entriesSeq.current;
    try {
      const d = await api<{ entries: Entry[] }>(`/api/memory/entries?domain=${dom}`);
      if (seq !== entriesSeq.current) return;   // 已切到别的域——丢弃旧响应
      setEntries(d.entries.reverse());   // 新在前
    } catch (e) {
      if (seq !== entriesSeq.current) return;
      setEntries([]); setMsg({ t: `记忆列表加载失败：${String(e)}`, err: true });
    }
  }
  useEffect(() => { void reloadDomains(); }, []);
  useEffect(() => { setSel(null); void reloadEntries(domain); }, [domain]);

  function pick(e: Entry) {
    if (dirty && e.id !== sel?.id
      && !confirm('当前条目有未保存修改，切换将丢弃——确认？')) return;
    setSel(e); setSummary(e.summary); setPreview(false); setVerText('');
    const seq = ++pickSeq.current;
    void (async () => {
      try {
        const d = await api<{ text: string }>(
          `/api/memory/file?domain=${domain}&id=${e.id}`);
        if (seq !== pickSeq.current) return;   // 已选中别的条目
        const body = d.text.replace(/^---\n[\s\S]*?\n---\n/, '').trim();
        setText(body);
        setOrig({ text: body, summary: e.summary });   // dirty 基线
        const h = await api<{ history: Ver[] }>(
          `/api/memory/history?domain=${domain}&id=${e.id}`);
        if (seq !== pickSeq.current) return;
        setHist(h.history);
      } catch (er) {
        if (seq === pickSeq.current)
          setMsg({ t: `条目加载失败：${String(er)}`, err: true });
      }
    })();
  }

  async function save() {
    if (!sel) return;
    setBusy(true);
    try {
      await api('/api/memory/file', { method: 'PUT', body: JSON.stringify({
        domain, id: sel.id, content: text, summary }) });
      setMsg({ t: '已保存（一次 commit，历史可回溯）' });
      pick(sel); void reloadEntries();
    } catch (e) { setMsg({ t: `保存被拒：${String(e)}`, err: true }); }   // 护栏拒绝原因直显
    finally { setBusy(false); }
  }

  async function del(e: Entry) {
    if (!confirm(`删除记忆「${e.summary}」？git 历史保留（可经历史恢复）。`)) return;
    try {
      await api(`/api/memory/entry?domain=${domain}&id=${e.id}`, { method: 'DELETE' });
      setSel(null); setMsg({ t: `已删除 ${e.summary}（历史在，可恢复）` });
      void reloadEntries();
    } catch (err) { setMsg({ t: `删除失败：${String(err)}`, err: true }); }
  }

  async function restore(v: Ver) {
    if (!sel) return;
    try {
      await api('/api/memory/restore', { method: 'POST', body: JSON.stringify({
        domain, id: sel.id, ref: v.hash }) });
      setMsg({ t: `已恢复到 ${v.hash}（${v.subject}）` });
      pick(sel);
    } catch (e) { setMsg({ t: `恢复失败：${String(e)}`, err: true }); }
  }

  async function showVer(v: Ver) {
    const seq = ++verSeq.current;
    try {
      const d = await api<{ text: string }>(
        `/api/memory/version?domain=${domain}&id=${sel?.id}&ref=${v.hash}`);
      if (seq !== verSeq.current) return;   // 已点看别的版本
      setVerText(d.text.replace(/^---\n[\s\S]*?\n---\n/, '').trim());
    } catch (e) {
      if (seq === verSeq.current)
        setMsg({ t: `历史版本加载失败：${String(e)}`, err: true });
    }
  }

  return (
    <div className="admin-body">
      <div className="admin-toolbar">
        {domains.map(d => (
          <button key={d} className={`btn sm ${d === domain ? 'primary' : 'ghost'}`}
            onClick={() => {
              if (d !== domain && dirty
                && !confirm('当前条目有未保存修改，切域将丢弃——确认？')) return;
              setDomain(d);
            }}>{d === 'user' ? '用户级（跨项目）' : d}</button>
        ))}
        <button className="btn sm" onClick={() => setCreating(v => !v)}>
          <Plus size={13} /> 新建
        </button>
        {msg && <span className={msg.err ? 'admin-msg err' : 'admin-msg'}>{msg.t}</span>}
      </div>
      {creating && <NewEntryForm domain={domain} onDone={() => {
        setCreating(false); void reloadEntries();
      }} />}
      <div className="mem-cols">
        <div className="hub-results" style={{ flex: 1 }}>
          {entries.map(e => (
            <div key={e.id} className={`hub-card slim${sel?.id === e.id ? ' on' : ''}`}
              onClick={() => pick(e)} role="button">
              <b>{e.summary}{e.draft
                ? <span className="chip" title="压缩后反思的候选教训——保存编辑即转正">待确认</span>
                : null}</b>
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
                <button className="btn sm primary" disabled={!dirty || busy}
                  onClick={() => void save()}>{busy ? '保存中…' : '保存'}</button>
                {dirty && <span className="chip warn" title="有未保存的改动">● 未保存</span>}
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
        <button className="btn ghost sm" onClick={() => {
          if ((summary.trim() || content.trim())
            && !confirm('已填内容将丢弃，确认取消？')) return;
          onDone();
        }}>取消</button>
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
