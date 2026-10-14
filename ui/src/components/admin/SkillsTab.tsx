// Skills 管理页（列表/上传/翻译/安装）——从 AdminPanel 拆出（v0.6.12）
// v0.7.27 设计语言重构：kicker 页头+说明 banner+segmented 筛选+搜索+分层卡片（.skp-* 独立命名空间）
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, withToken } from '../../api/client';
import { fetchZhDesc } from '../../api/zh';
import { toast } from '../../stores/toasts';
import SkillEditor from '../SkillEditor';
import InstallDialog from '../InstallDialog';
import { Plus, Upload, Globe, Search, BookOpen } from '../icons';
import { withQ } from './shared';
import type { SkillItem } from './shared';
import { askConfirm } from '../../stores/confirm';

type Filter = 'all' | 'local' | 'ext' | 'off';

export default function SkillsTab() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [zh, setZh] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const [q, setQ] = useState('');
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
    if (!await askConfirm({ title: `删除 skill「${s.name}」？整目录移除，不可恢复。`, danger: true })) return;
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

  const counts = useMemo(() => ({
    all: skills.length,
    local: skills.filter(s => !s.source).length,
    ext: skills.filter(s => s.source).length,
    off: skills.filter(s => s.disabled).length,
  }), [skills]);

  const shown = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return skills.filter(s => {
      if (filter === 'local' && s.source) return false;
      if (filter === 'ext' && !s.source) return false;
      if (filter === 'off' && !s.disabled) return false;
      if (kw && !(s.name.toLowerCase().includes(kw)
        || (s.description || '').toLowerCase().includes(kw)
        || (zh[s.name] || '').toLowerCase().includes(kw))) return false;
      return true;
    });
  }, [skills, filter, q, zh]);

  const SEGS: [Filter, string][] = [['all', '全部'], ['local', '本地'], ['ext', '市场'], ['off', '已禁用']];

  return (
    <div className="admin-body">
      {editing !== null
        ? <SkillEditor name={editing} onBack={() => { setEditing(null); void reload(); }} />
        : installing
          ? <InstallDialog onClose={() => { setInstalling(false); void reload(); }} />
          : <>
            <div className="skp-head">
              <div className="skp-kicker"><span className="skp-kicker-dot" />技能库 · SKILLS</div>
              <h2 className="skp-title">技能</h2>
              <div className="skp-banner">
                <b>技能 = 可复用的操作知识。</b>每个技能是一个 <code>SKILL.md</code> 目录，新会话自动加载——
                agent 按描述判断何时使用。禁用后新会话不挂载（active 会话下一 turn 失效）；
                市场来源带供应链锁（🔒 防篡改）。
              </div>
            </div>

            <div className="skp-toolbar">
              <div className="skp-toolbar-l">
                <button className="btn sm primary" onClick={() => setCreating(true)}><Plus size={13} /> 新建技能</button>
                <button className="btn sm" onClick={() => fileRef.current?.click()}><Upload size={13} /> 上传 zip</button>
                <input ref={fileRef} type="file" accept=".zip" hidden
                  onChange={e => { const f = e.target.files?.[0]; if (f) void uploadZip(f); e.target.value = ''; }}  autoComplete="off"/>
                <button className="btn sm" onClick={() => setInstalling(true)}><Globe size={13} /> 市场安装</button>
                {msg && <span className={msg.err ? 'admin-msg err' : 'admin-msg'}>{msg.t}</span>}
              </div>
              <div className="skp-toolbar-r">
                <div className="skp-search">
                  <Search size={13} />
                  <input placeholder="搜索技能名/描述…" value={q}
                    onChange={e => setQ(e.target.value)} autoComplete="off" />
                </div>
              </div>
            </div>

            {creating && <NewSkillForm onDone={() => { setCreating(false); void reload(); }} />}

            <div className="skp-seg">
              {SEGS.map(([k, label]) => (
                <button key={k} className={`skp-seg-btn${filter === k ? ' on' : ''}`}
                  onClick={() => setFilter(k)}>
                  {label}<span className="skp-seg-n">{counts[k]}</span>
                </button>
              ))}
            </div>

            <div className="skp-grid">
              {shown.map(s => (
                <div key={s.name} className={`skp-card${s.disabled ? ' off' : ''}`}>
                  <div className="skp-row1">
                    <span className="skp-name" title={s.name}>{s.name}</span>
                    {s.disabled && <span className="skp-badge off-tag">已禁用</span>}
                    <span className={`skp-badge ${s.source ? 'ext' : 'local'}`}
                      title={s.source ? (s.source.repo ?? s.source.via) : '本机目录'}>
                      {s.source
                        ? (s.source.repo ?? s.source.via)
                          + (s.lock_ok === false ? ' ⚠锁校验失败' : ' 🔒')
                        : '本地'}
                    </span>
                  </div>
                  <div className="skp-desc" title={zh[s.name] ? s.description : undefined}>
                    {zh[s.name]
                      ? <><span className="skp-zh" title={s.description}>译</span>{zh[s.name]}</>
                      : (s.description || '（无描述）')}
                  </div>
                  <div className="skp-foot">
                    <span className="skp-time">{s.mtime}</span>
                    <span className="skp-acts">
                      <button className="skp-act" onClick={() => void toggle(s)}>{s.disabled ? '启用' : '禁用'}</button>
                      <button className="skp-act" onClick={() => setEditing(s.name)}>编辑</button>
                      {/* 导出 agentskills.io 兼容 zip：frontmatter 规范化、剥内部元数据 */}
                      <a className="skp-act" download={`${s.name}.zip`}
                        href={withToken(`/api/skills/${encodeURIComponent(s.name)}/export`)}>导出</a>
                      <button className="skp-act danger" onClick={() => void del(s)}>删除</button>
                    </span>
                  </div>
                </div>
              ))}
              {shown.length === 0 && (
                <div className="skp-empty">
                  <BookOpen size={28} />
                  <div className="skp-empty-title">
                    {skills.length === 0 ? '还没有技能' : '没有匹配的技能'}
                  </div>
                  <div className="skp-empty-sub">
                    {skills.length === 0
                      ? '技能让 agent 学会你的工作流：新建一个，或从市场安装。'
                      : '换个筛选或清空搜索试试。'}
                  </div>
                  {skills.length === 0 && (
                    <button className="btn sm primary" onClick={() => setCreating(true)}>
                      <Plus size={13} /> 新建第一个技能
                    </button>
                  )}
                </div>
              )}
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
      <input placeholder="skill 名称（小写字母/数字/._-）" value={name} onChange={e => setName(e.target.value)}  autoComplete="off"/>
      <input placeholder="一句话描述（agent 按此判断何时使用）" value={desc} onChange={e => setDesc(e.target.value)}  autoComplete="off"/>
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
