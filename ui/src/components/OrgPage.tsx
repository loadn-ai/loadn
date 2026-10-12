// 组织架构页（DW-3/4）：部门树 + 正式员工卡（编辑操作空间）+ 临时工
// 召集面板。员工即 agent 定义——编辑保存即同步 $LOADN_HOME/agents/，
// Task 工具 subagent_type=员工名 即点名派发。
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { toast } from '../stores/toasts';
import { askConfirm } from '../stores/confirm';
import { Users, Plus, Pencil, Trash, X, RotateCw, Spinner } from './icons';

interface Employee {
  id: number; dept_id: number | null; name: string; title: string;
  mbti: string; traits: string; persona: string; tools: string | null;
  model: string | null; status: string; color: string;
}
interface Department {
  id: number; name: string; icon: string; sort: number; employees: Employee[];
}
interface Org { departments: Department[]; pool: Record<string, number> }
interface TempWorker {
  id: number; name: string; mbti: string; traits: string;
  brief: string; state: string;
}

const MBTI = ['INTJ', 'INTP', 'ENTJ', 'ENTP', 'INFJ', 'INFP', 'ENFJ', 'ENFP',
  'ISTJ', 'ISFJ', 'ESTJ', 'ESFJ', 'ISTP', 'ISFP', 'ESTP', 'ESFP'];

export default function OrgPage({ onClose }: { onClose: () => void }) {
  const [org, setOrg] = useState<Org | null>(null);
  const [pool, setPool] = useState<TempWorker[]>([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [deptSel, setDeptSel] = useState<number | 'all'>('all');
  const [editor, setEditor] = useState<null | { mode: 'new' } | { mode: 'edit'; emp: Employee }>(null);
  const [deptNew, setDeptNew] = useState(false);
  // 召集面板（DW-4）
  const [recN, setRecN] = useState(3);
  const [recBrief, setRecBrief] = useState('');

  const reload = useCallback(async () => {
    try {
      const [o, p] = await Promise.all([api<Org>('/api/org'), api<{ pool: TempWorker[] }>('/api/org/pool')]);
      setOrg(o); setPool(p.pool); setErr('');
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const init = async () => {
    setBusy(true);
    try {
      const r = await api<{ employees: number }>('/api/org/init', { method: 'POST' });
      toast(`默认组织架构已初始化（${r.employees} 名员工）`);
      await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
    setBusy(false);
  };

  const saveEmp = async (data: Record<string, unknown>, id?: number) => {
    try {
      await api(id ? `/api/org/employees/${id}` : '/api/org/employees', {
        method: id ? 'PATCH' : 'POST',
        body: JSON.stringify(data),
      });
      toast(id ? '员工已保存' : '员工已入职');
      setEditor(null);
      await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
  };

  const delEmp = async (emp: Employee) => {
    if (!await askConfirm({ title: `解聘「${emp.name}」？agent 定义同步摘除，不可恢复。`, danger: true })) return;
    try {
      await api(`/api/org/employees/${emp.id}`, { method: 'DELETE' });
      toast(`已解聘 ${emp.name}`);
      await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
  };

  const toggleEmp = async (emp: Employee) => {
    const to = emp.status === 'active' ? 'disabled' : 'active';
    try {
      await api(`/api/org/employees/${emp.id}`, { method: 'PATCH', body: JSON.stringify({ status: to }) });
      await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
  };

  const addDept = async (name: string) => {
    setDeptNew(false);
    if (!name.trim()) return;
    try {
      await api('/api/org/departments', { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
      toast('部门已创建');
      await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
  };

  const delDept = async (d: Department) => {
    if (!await askConfirm({ title: `删除部门「${d.name}」？成员移入「未分配」，不会被解聘。`, danger: true })) return;
    try {
      await api(`/api/org/departments/${d.id}`, { method: 'DELETE' });
      if (deptSel === d.id) setDeptSel('all');
      await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
  };

  const recruit = async () => {
    setBusy(true);
    try {
      const r = await api<{ workers: TempWorker[] }>('/api/org/recruit', {
        method: 'POST', body: JSON.stringify({ n: recN, brief: recBrief }),
      });
      toast(`已召集 ${r.workers.length} 名临时工入池`);
      await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
    setBusy(false);
  };

  const retire = async (w: TempWorker) => {
    try {
      await api(`/api/org/pool/${w.id}`, { method: 'DELETE' });
      await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
  };

  const emps = org?.departments.flatMap(d => d.employees.map(e => ({ ...e, dept: d.name }))) ?? [];
  const shown = deptSel === 'all' ? emps : emps.filter(e => e.dept_id === deptSel);
  const total = emps.length;

  return (
    <div className="org-page admin-body">
      <header className="org-head">
        <div className="org-title">
          <Users size={18} />
          <h2>组织架构</h2>
          {total > 0 && <span className="org-sub">{total} 名正式员工 · 池 {pool.filter(w => w.state === 'idle').length} 名临时工</span>}
        </div>
        <div className="org-actions">
          <button className="btn" onClick={() => void reload()}><RotateCw size={13} /> 刷新</button>
          <button className="btn primary" onClick={() => setEditor({ mode: 'new' })}><Plus size={13} /> 招聘员工</button>
          <button className="btn ghost" onClick={onClose}><X size={14} /></button>
        </div>
      </header>

      {err && <div className="admin-msg err">{err} <button className="btn" onClick={() => void reload()}>重试</button></div>}

      {org && org.departments.length === 0 && (
        <div className="org-empty">
          <Users size={40} />
          <h3>还没有组织架构</h3>
          <p>初始化默认组织架构（8 部门 18 名数字员工：总经办/产品/研发/设计/法务/HR/运营/数据），每人带 MBTI 性格与专属人设；之后可自由编辑、招聘、解聘。</p>
          <button className="btn primary" disabled={busy} onClick={() => void init()}>
            {busy ? <Spinner /> : <Users size={14} />} 一键初始化默认组织架构
          </button>
        </div>
      )}

      {org && org.departments.length > 0 && (
        <div className="org-main">
          <aside className="org-depts">
            <div className={'org-dept' + (deptSel === 'all' ? ' on' : '')}
                 onClick={() => setDeptSel('all')}>
              <span>全部员工</span><span className="org-n">{total}</span>
            </div>
            {org.departments.map(d => (
              <div key={d.id} className={'org-dept' + (deptSel === d.id ? ' on' : '')}
                   onClick={() => setDeptSel(d.id)}>
                <span>{d.name}</span>
                <span className="org-n">{d.employees.length}</span>
                <button className="icon-btn" title="删除部门"
                        onClick={e => { e.stopPropagation(); void delDept(d); }}><Trash size={11} /></button>
              </div>
            ))}
            {deptNew
              ? <input className="org-dept-input" autoFocus placeholder="部门名，回车确认"
                       onKeyDown={e => {
                         if (e.key === 'Enter') void addDept((e.target as HTMLInputElement).value);
                         if (e.key === 'Escape') setDeptNew(false);
                       }}
                       onBlur={e => void addDept(e.target.value)} />
              : <button className="org-dept-add" onClick={() => setDeptNew(true)}><Plus size={12} /> 新建部门</button>}
          </aside>

          <div className="org-grid">
            {shown.map(e => (
              <div key={e.id} className={'org-card' + (e.status === 'disabled' ? ' off' : '')}>
                <div className="org-card-bar" style={{ background: e.color || 'var(--accent-dim, #6366f1)' }} />
                <div className="org-card-head">
                  <strong>{e.name}</strong>
                  {e.status === 'disabled' && <span className="chip">停用</span>}
                  {e.mbti && <span className="chip mbti">{e.mbti}</span>}
                </div>
                <div className="org-card-sub">{(deptSel === 'all' && (e as any).dept) ? `${(e as any).dept} · ` : ''}{e.title || '员工'}</div>
                {e.traits && <div className="org-traits">{e.traits.split(/[,，、]/).filter(Boolean).slice(0, 4).map((t, i) => <span key={i} className="chip ghost">{t.trim()}</span>)}</div>}
                <div className="org-card-foot">
                  <label className="switch" title={e.status === 'active' ? '在岗（可点名）' : '停用（不注入）'}>
                    <input type="checkbox" checked={e.status === 'active'} onChange={() => void toggleEmp(e)} />
                    <span />
                  </label>
                  <span className="org-card-hint">{e.status === 'active' ? 'Task 点名可用' : '已停用'}</span>
                  <span className="spacer" />
                  <button className="icon-btn" title="编辑" onClick={() => setEditor({ mode: 'edit', emp: e })}><Pencil size={12} /></button>
                  <button className="icon-btn" title="解聘" onClick={() => void delEmp(e)}><Trash size={12} /></button>
                </div>
              </div>
            ))}
            {shown.length === 0 && <div className="org-empty small">该部门暂无员工——点右上「招聘员工」补位。</div>}
          </div>
        </div>
      )}

      {org && (
        <section className="org-recruit">
          <h3>临时工召集</h3>
          <p className="org-recruit-note">按人物要求生成 n 名临时工入池；此后未点名的子任务将从池中取名（池空回落默认人名库）。</p>
          <div className="org-recruit-form">
            <label>人数
              <select value={recN} onChange={e => setRecN(Number(e.target.value))}>
                {[1, 2, 3, 4, 5, 6, 7, 8].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <input className="org-brief" placeholder="人物要求（如：懂数据合规的法务助理）"
                   value={recBrief} onChange={e => setRecBrief(e.target.value)} />
            <button className="btn primary" disabled={busy} onClick={() => void recruit()}>
              {busy ? <Spinner /> : <Users size={13} />} 召集入池
            </button>
          </div>
          {pool.length > 0 && (
            <div className="org-pool">
              {pool.map(w => (
                <div key={w.id} className={'org-pool-card' + (w.state === 'retired' ? ' off' : '')}>
                  <div className="org-card-head">
                    <strong>{w.name}</strong>
                    {w.mbti && <span className="chip mbti">{w.mbti}</span>}
                    <span className={'chip ' + (w.state === 'idle' ? 'ok' : 'ghost')}>
                      {w.state === 'idle' ? '空闲' : w.state === 'busy' ? '出勤中' : '已退役'}</span>
                  </div>
                  {w.traits && <div className="org-traits">{w.traits.split(/[,，、]/).filter(Boolean).slice(0, 3).map((t, i) => <span key={i} className="chip ghost">{t.trim()}</span>)}</div>}
                  {w.brief && <div className="org-card-sub">{w.brief}</div>}
                  {w.state !== 'retired' && (
                    <button className="icon-btn org-pool-retire" title="退役（不再取用）"
                            onClick={() => void retire(w)}><Trash size={11} /></button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {editor && <EmpEditor depts={org?.departments ?? []} emp={editor.mode === 'edit' ? editor.emp : null}
                            onCancel={() => setEditor(null)} onSave={saveEmp} />}
    </div>
  );
}

function EmpEditor({ depts, emp, onCancel, onSave }: {
  depts: Department[]; emp: Employee | null;
  onCancel: () => void; onSave: (data: Record<string, unknown>, id?: number) => void;
}) {
  const [name, setName] = useState(emp?.name ?? '');
  const [deptId, setDeptId] = useState<number | ''>(emp?.dept_id ?? '');
  const [title, setTitle] = useState(emp?.title ?? '');
  const [mbti, setMbti] = useState(emp?.mbti ?? 'INTJ');
  const [traits, setTraits] = useState(emp?.traits ?? '');
  const [persona, setPersona] = useState(emp?.persona ?? '');
  const [tools, setTools] = useState((emp?.tools ? JSON.parse(emp.tools).join(', ') : ''));
  const [model, setModel] = useState(emp?.model ?? '');
  const [saving, setSaving] = useState(false);

  const commit = () => {
    setSaving(true);
    const data: Record<string, unknown> = {
      name: name.trim(), title: title.trim(), mbti, traits: traits.trim(),
      persona: persona.trim(), model: model.trim() || null,
      tools: tools.trim() ? tools.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean) : null,
    };
    if (deptId !== '') data.dept_id = deptId;
    onSave(data, emp?.id);
  };

  return (
    <div className="drawer-mask" onClick={onCancel}>
      <div className="drawer org-editor" onClick={e => e.stopPropagation()}>
        <header>
          <h3>{emp ? `编辑 · ${emp.name}` : '招聘新员工'}</h3>
          <button className="icon-btn" onClick={onCancel}><X size={14} /></button>
        </header>
        <div className="drawer-body">
          <label>姓名（Task 点名键）<input value={name} onChange={e => setName(e.target.value)} placeholder="如：方维正" /></label>
          <label>部门
            <select value={deptId} onChange={e => setDeptId(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">未分配</option>
              {depts.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          <label>头衔<input value={title} onChange={e => setTitle(e.target.value)} placeholder="如：法务顾问" /></label>
          <label>MBTI
            <select value={mbti} onChange={e => setMbti(e.target.value)}>
              {MBTI.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label>性格标签（逗号分隔）<input value={traits} onChange={e => setTraits(e.target.value)} placeholder="如：条框守护者,风险嗅觉,直率" /></label>
          <label>人设（行为准则，进子代理 system 提示）
            <textarea rows={5} value={persona} onChange={e => setPersona(e.target.value)}
                      placeholder="口头禅、说话风格、关注点、交付习惯……" /></label>
          <label>操作空间 · 工具白名单（逗号分隔，空=通用面）
            <input value={tools} onChange={e => setTools(e.target.value)} placeholder="如：Read, Grep, WebSearch" /></label>
          <label>操作空间 · 独立模型（空=随主）<input value={model} onChange={e => setModel(e.target.value)} placeholder="如：glm-5.3-flash" /></label>
        </div>
        <footer>
          <span className="hint">保存即同步 agent 定义（$LOADN_HOME/agents/），新子代理立即可点名。</span>
          <button className="btn" onClick={onCancel}>取消</button>
          <button className="btn primary" disabled={saving || !name.trim()} onClick={commit}>
            {saving && <Spinner />} 保存
          </button>
        </footer>
      </div>
    </div>
  );
}
