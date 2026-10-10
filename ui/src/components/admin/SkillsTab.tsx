// Skills 管理页（列表/上传/翻译/安装）——从 AdminPanel 拆出（v0.6.12）
import { useEffect, useRef, useState } from 'react';
import { api, withToken } from '../../api/client';
import { fetchZhDesc } from '../../api/zh';
import { toast } from '../../stores/toasts';
import SkillEditor from '../SkillEditor';
import InstallDialog from '../InstallDialog';
import { Plus, Upload, Globe } from '../icons';
import { withQ } from './shared';
import type { SkillItem } from './shared';

export default function SkillsTab() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [zh, setZh] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function reload() {
    try {
      const d = await api<{ skills: SkillItem[] }>('/api/skills');
      setSkills(d.skills);
      // 英文描述 → 后端翻译（kv 缓存）补中文简介；失败回退原文
      void fetchZhDesc(d.skills).then(r => {
        if (r === null) toast('翻译服务不可用，技能简介暂显示原文', false);   // E6 降级可见
        else setZh(r);
      });
    } catch (e) { setMsg({ t: `加载失败：${String(e)}`, err: true }); }
  }
  useEffect(() => { void reload(); }, []);

  async function del(s: SkillItem) {
    if (!confirm(`删除 skill「${s.name}」？整目录移除，不可恢复。`)) return;
    try {
      await api(`/api/skills/${encodeURIComponent(s.name)}?force=true`, { method: 'DELETE' });
      toast(`已删除 ${s.name}`);
    } catch (e) { toast(`删除失败：${String(e)}`, false); }
    void reload();
  }

  async function toggle(s: SkillItem) {
    try {
      await api(`/api/skills/${encodeURIComponent(s.name)}/toggle`, {
        method: 'POST', body: JSON.stringify({ disabled: !s.disabled }) });
      toast(s.disabled ? `已启用 ${s.name}（新会话生效，引用它的 active 会话已补挂）`
                      : `已禁用 ${s.name}（新会话不挂载，active 会话下一 turn 失效）`);
    } catch (e) { toast(`操作失败：${String(e)}`, false); }
    void reload();
  }

  async function uploadZip(f: File) {
    const fd = new FormData();
    fd.append('file', f);
    toast(`安装 ${f.name} 中…`);
    try {
      const d = await api<{ installed: string[]; skipped?: string[]}>(
        withQ('/api/skills/upload'), { method: 'POST', body: fd });
      toast(`已安装：${d.installed.join(', ')}${d.skipped?.length ? '（跳过 ' + d.skipped.join('; ') + '）' : ''}`);
    } catch (e) { toast(`安装失败：${String(e)}`, false); }
    void reload();
  }

  return (
    <div className="admin-body">
      {editing !== null
        ? <SkillEditor name={editing} onBack={() => { setEditing(null); void reload(); }} />
        : installing
          ? <InstallDialog onClose={() => { setInstalling(false); void reload(); }} />
          : <>
            <div className="admin-toolbar">
              <button className="btn sm" onClick={() => setCreating(true)}><Plus size={13} /> 新建</button>
              <button className="btn sm" onClick={() => fileRef.current?.click()}><Upload size={13} /> 上传 zip</button>
              <input ref={fileRef} type="file" accept=".zip" hidden
                onChange={e => { const f = e.target.files?.[0]; if (f) void uploadZip(f); e.target.value = ''; }} />
              <button className="btn sm primary" onClick={() => setInstalling(true)}><Globe size={13} /> 市场安装</button>
              {msg && <span className={msg.err ? 'admin-msg err' : 'admin-msg'}>{msg.t}</span>}
            </div>
            {creating && <NewSkillForm onDone={() => { setCreating(false); void reload(); }} />}
            <div className="skill-grid">
              {skills.map(s => (
                <div key={s.name} className={`skill-card${s.disabled ? ' off' : ''}`}>
                  <div className="sk-head">
                    <b>{s.name}</b>
                    {s.disabled && <span className="sk-src off-tag">已禁用</span>}
                    <span className={`sk-src ${s.source ? 'ext' : 'local'}`}>
                      {s.source
                        ? (s.source.repo ?? s.source.via)
                          + (s.lock_ok === false ? ' ⚠锁校验失败' : ' 🔒')
                        : '本地'}
                    </span>
                  </div>
                  <div className="sk-desc" title={zh[s.name] ? s.description : undefined}>
                    {zh[s.name]
                      ? <><span className="zh-tag" title={s.description}>译</span>{zh[s.name]}</>
                      : (s.description || '（无描述）')}
                  </div>
                  <div className="sk-foot">
                    <span className="sk-time">{s.mtime}</span>
                    <span className="sk-actions">
                      <button className="link" onClick={() => void toggle(s)}>{s.disabled ? '启用' : '禁用'}</button>
                      <button className="link" onClick={() => setEditing(s.name)}>编辑</button>
                      {/* 导出 agentskills.io 兼容 zip：frontmatter 规范化、剥内部元数据 */}
                      <a className="link" download={`${s.name}.zip`}
                        href={withToken(`/api/skills/${encodeURIComponent(s.name)}/export`)}>导出</a>
                      <button className="link danger-link" onClick={() => void del(s)}>删除</button>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </>}
    </div>
  );
}



function NewSkillForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [err, setErr] = useState('');
  return (
    <div className="new-skill-form">
      <input placeholder="skill 名称（小写字母/数字/._-）" value={name} onChange={e => setName(e.target.value)} />
      <input placeholder="一句话描述（agent 按此判断何时使用）" value={desc} onChange={e => setDesc(e.target.value)} />
      <div className="modal-foot">
        <button className="btn ghost sm" onClick={onDone}>取消</button>
        <button className="btn primary sm" disabled={!name.trim()} onClick={() => void (async () => {
          try {
            await api('/api/skills', { method: 'POST', body: JSON.stringify({ name: name.trim(), description: desc.trim() }) });
            onDone();
          } catch (e) { setErr(String(e)); }
        })()}>创建</button>
      </div>
      {err && <div className="admin-err">{err}</div>}
    </div>
  );
}

/** 外观：亮/暗主题（原侧栏「暗色」按钮并入管理页后的落点） */
