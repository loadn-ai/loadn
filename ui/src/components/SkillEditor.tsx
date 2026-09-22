// skill 编辑器：左文件树 + 右编辑区（md 预览 toggle）
import { useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import { api } from '../api/client';

interface FileEntry { path: string; size: number }

export default function SkillEditor({ name, onBack }: { name: string; onBack: () => void }) {
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [cur, setCur] = useState('SKILL.md');
  const [content, setContent] = useState('');
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState(false);
  const [msg, setMsg] = useState('');
  const [ready, setReady] = useState(false);

  async function loadMeta() {
    const d = await api<{ files: FileEntry[] }>(`/api/skills/${encodeURIComponent(name)}`);
    setFiles(d.files);
  }
  async function openFile(p: string) {
    if (dirty && !confirm('未保存的修改将丢失，继续？')) return;
    const d = await api<{ content: string }>(
      `/api/skills/${encodeURIComponent(name)}/file?path=${encodeURIComponent(p)}`);
    setCur(p); setContent(d.content); setDirty(false); setPreview(false); setMsg('');
    setReady(true);
  }
  useEffect(() => { void loadMeta(); void openFile('SKILL.md'); }, [name]);

  async function save() {
    try {
      const d = await api<{ warning: string | null }>(
        `/api/skills/${encodeURIComponent(name)}/file`, {
        method: 'PUT', body: JSON.stringify({ path: cur, content }),
      });
      setDirty(false);
      setMsg(d.warning ? `⚠ ${d.warning}` : '已保存（经 symlink 实时生效，下一 turn 可见）');
      void loadMeta();
    } catch (e) { setMsg(String(e)); }
  }

  async function newFile() {
    const p = prompt('新文件相对路径（例：scripts/run.py）');
    if (!p) return;
    try {
      await api(`/api/skills/${encodeURIComponent(name)}/file`, {
        method: 'POST', body: JSON.stringify({ path: p, content: '' }),
      });
      void loadMeta();
      setCur(p); setContent(''); setDirty(false);
    } catch (e) { setMsg(String(e)); }
  }

  async function delFile() {
    if (cur === 'SKILL.md') { setMsg('SKILL.md 不能删'); return; }
    if (!confirm(`删除 ${cur}？`)) return;
    try {
      await api(`/api/skills/${encodeURIComponent(name)}/file?path=${encodeURIComponent(cur)}`,
        { method: 'DELETE' });
      void loadMeta();
      void openFile('SKILL.md');
    } catch (e) { setMsg(String(e)); }
  }

  return (
    <div className="skill-editor">
      <div className="se-side">
        <div className="se-files-head">
          <button className="link" onClick={onBack}>← 返回</button>
          <span className="se-name">{name}</span>
          <button className="link" onClick={() => void newFile()}>＋文件</button>
        </div>
        <div className="file-tree">
          {files.map(f => (
            <div key={f.path} className={`fn ${f.path === cur ? 'cur' : ''}`}
              onClick={() => void openFile(f.path)}>
              {f.path === cur ? '▸ ' : ''}{f.path}
              <span className="fn-size">{f.size >= 0 ? `${Math.round(f.size / 1024)}K` : 'dir'}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="se-main">
        <div className="se-toolbar">
          <b className="mono">{cur}</b>
          {dirty && <span className="sk-dirty">● 未保存</span>}
          <span className="se-grow" />
          {cur.endsWith('.md') && (
            <button className="btn ghost sm" onClick={() => setPreview(!preview)}>{preview ? '编辑' : '预览'}</button>
          )}
          <button className="btn ghost sm" onClick={() => void delFile()}>删文件</button>
          <button className="btn primary sm" disabled={!dirty} onClick={() => void save()}>保存</button>
        </div>
        {preview
          ? <div className="md se-preview"><Markdown>{content}</Markdown></div>
          : <textarea className="se-text mono" value={content} spellCheck={false} disabled={!ready}
            onChange={e => { setContent(e.target.value); setDirty(true); }} />}
        {msg && <div className="se-msg">{msg}</div>}
      </div>
    </div>
  );
}
