// 组织架构页（DW-3/4）：部门树 + 正式员工卡（编辑操作空间）+ 临时工
// 召集面板。员工即 agent 定义——编辑保存即同步 $LOADN_HOME/agents/，
// Task 工具 subagent_type=员工名 即点名派发。
// DW-6：职责说明（duty）卡片展示+进 agent 提示；任务记录抽屉按人名
// 回查 Task 卡归属（员工=subagent_type 点名 / 临时工=agent_name 出勤）。
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { toast } from '../stores/toasts';
import { askConfirm } from '../stores/confirm';
import { Users, Plus, Pencil, Trash, X, RotateCw, Spinner, ListTasks, Brain } from './icons';

interface Employee {
  id: number; dept_id: number | null; name: string; title: string;
  duty: string; mbti: string; traits: string; persona: string;
  tools: string | null; model: string | null; status: string; color: string;
  skills: string | null; resources: string | null;   // DW-7：JSON 数组或 null（不设限）
}
interface Department {
  id: number; name: string; icon: string; sort: number; employees: Employee[];
}
interface Org { departments: Department[]; pool: Record<string, number> }
interface TempWorker {
  id: number; name: string; mbti: string; traits: string;
  brief: string; state: string;
}
interface AgentTask {
  turn_id: number | null; session_id: string | null; session_title: string;
  description: string; subagent_type: string; agent_name: string;
  agent_id: string; files: number; artifacts: number; at: string | null;
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
  // 任务记录抽屉（DW-6）：按人名回查（员工与临时工共用）
  const [taskView, setTaskView] = useState<string | null>(null);
  // DW-7：员工记忆抽屉（domain=e:<名> 岗位知识库）
  const [memView, setMemView] = useState<string | null>(null);
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
                       onBlur={e => void addDept(e.target.value)}  autoComplete="off"/>
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
                {e.duty && <div className="org-duty" title={e.duty}>{e.duty}</div>}
                {(() => {
                  const sk = e.skills ? (JSON.parse(e.skills) as string[]) : null;
                  const rs = e.resources ? (JSON.parse(e.resources) as string[]) : null;
                  if (!sk && !rs) return null;
                  return <div className="org-binds">
                    {sk && <span className="chip bind" title={"技能面：" + sk.join("、")}>技能 {sk.length}</span>}
                    {rs && <span className="chip bind" title={"资源面：" + rs.join("、")}>资源 {rs.length}</span>}
                  </div>;
                })()}
                {e.traits && <div className="org-traits">{e.traits.split(/[,，、]/).filter(Boolean).slice(0, 4).map((t, i) => <span key={i} className="chip ghost">{t.trim()}</span>)}</div>}
                <div className="org-card-foot">
                  <label className="switch" title={e.status === 'active' ? '在岗（可点名）' : '停用（不注入）'}>
                    <input type="checkbox" checked={e.status === 'active'} onChange={() => void toggleEmp(e)}  autoComplete="off"/>
                    <span />
                  </label>
                  <span className="org-card-hint">{e.status === 'active' ? 'Task 点名可用' : '已停用'}</span>
                  <span className="spacer" />
                  <button className="icon-btn" title="岗位记忆（e:域）" onClick={() => setMemView(e.name)}><Brain size={12} /></button>
                  <button className="icon-btn" title="任务记录" onClick={() => setTaskView(e.name)}><ListTasks size={12} /></button>
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
              <select value={recN} onChange={e => setRecN(Number(e.target.value))} autoComplete="off">
                {[1, 2, 3, 4, 5, 6, 7, 8].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <input className="org-brief" placeholder="人物要求（如：懂数据合规的法务助理）"
                   value={recBrief} onChange={e => setRecBrief(e.target.value)}  autoComplete="off"/>
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
                    <span className="spacer" />
                    <button className="icon-btn" title="任务记录（出勤留痕）"
                            onClick={() => setTaskView(w.name)}><ListTasks size={11} /></button>
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
      {taskView && <TaskHistory name={taskView} onClose={() => setTaskView(null)} />}
      {memView && <EmpMemory name={memView} onClose={() => setMemView(null)} />}
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
  const [duty, setDuty] = useState(emp?.duty ?? '');
  const [mbti, setMbti] = useState(emp?.mbti ?? 'INTJ');
  const [traits, setTraits] = useState(emp?.traits ?? '');
  const [persona, setPersona] = useState(emp?.persona ?? '');
  const [tools, setTools] = useState((emp?.tools ? JSON.parse(emp.tools).join(', ') : ''));
  const [model, setModel] = useState(emp?.model ?? '');
  // DW-7 技能/资源绑定（null=不设限随会话全量）
  const [skillSel, setSkillSel] = useState<Set<string>>(new Set(emp?.skills ? JSON.parse(emp.skills) as string[] : []));
  const [resSel, setResSel] = useState<Set<string>>(new Set(emp?.resources ? JSON.parse(emp.resources) as string[] : []));
  const [skillList, setSkillList] = useState<{ name: string }[]>([]);
  const [svcList, setSvcList] = useState<{ name: string }[] | null>(null);   // null=admin 面不可读→降级文本框
  const [resText, setResText] = useState('');
  useEffect(() => {
    void api<{ skills: { name: string; disabled?: boolean }[] }>('/api/skills')
      .then(r => setSkillList(r.skills.filter(s => !s.disabled)))
      .catch(() => setSkillList([]));
    void api<{ custom: { name: string }[] }>('/api/admin/resources')
      .then(r => setSvcList(r.custom || []))
      .catch(() => setSvcList(null));
  }, []);
  if (svcList === null && !resText && resSel.size) setResText([...resSel].join(', '));
  const [saving, setSaving] = useState(false);

  // UI 巡检批：Esc 关闭（此前只支持点 mask/× ——键盘用户被锁死）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const commit = () => {
    setSaving(true);
    const data: Record<string, unknown> = {
      name: name.trim(), title: title.trim(), duty: duty.trim(), mbti,
      traits: traits.trim(), persona: persona.trim(),
      model: model.trim() || null,
      tools: tools.trim() ? tools.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean) : null,
      skills: skillSel.size ? [...skillSel] : null,
      resources: svcList === null ? (resText.trim() ? resText.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean) : null) : (resSel.size ? [...resSel] : null),
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
          <label>姓名（Task 点名键）<input value={name} onChange={e => setName(e.target.value)} placeholder="如：方维正"  autoComplete="off"/></label>
          <label>部门
            <select value={deptId} onChange={e => setDeptId(e.target.value === '' ? '' : Number(e.target.value))} autoComplete="off">
              <option value="">未分配</option>
              {depts.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          <label>头衔<input value={title} onChange={e => setTitle(e.target.value)} placeholder="如：法务顾问"  autoComplete="off"/></label>
          <label>职责说明（负责什么；进 agent 提示，也是模型选人依据）
            <textarea rows={2} value={duty} onChange={e => setDuty(e.target.value)}
                      placeholder="如：合规审查、风险条款识别与依据引用"  autoComplete="off"/></label>
          <label>MBTI
            <select value={mbti} onChange={e => setMbti(e.target.value)} autoComplete="off">
              {MBTI.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label>性格标签（逗号分隔）<input value={traits} onChange={e => setTraits(e.target.value)} placeholder="如：条框守护者,风险嗅觉,直率"  autoComplete="off"/></label>
          <label>人设（行为准则，进子代理 system 提示）
            <textarea rows={5} value={persona} onChange={e => setPersona(e.target.value)}
                      placeholder="口头禅、说话风格、关注点、交付习惯……"  autoComplete="off"/></label>
          <label>操作空间 · 工具白名单（逗号分隔，空=通用面）
            <input value={tools} onChange={e => setTools(e.target.value)} placeholder="如：Read, Grep, WebSearch"  autoComplete="off"/></label>
          <label>操作空间 · 独立模型（空=随主）<input value={model} onChange={e => setModel(e.target.value)} placeholder="如：glm-5.3-flash"  autoComplete="off"/></label>

          <label>技能面（DW-7；不选=随会话全量，选中=仅列出的技能）
            <div className="org-bind-list">
              {skillList.length === 0 && <span className="hint">技能库为空或不可读</span>}
              {skillList.map(s => (
                <label key={s.name} className={"org-bind-pick" + (skillSel.has(s.name) ? ' on' : '')}>
                  <input type="checkbox" checked={skillSel.has(s.name)} autoComplete="off"
                         onChange={() => { const n = new Set(skillSel); n.has(s.name) ? n.delete(s.name) : n.add(s.name); setSkillSel(n); }} />
                  {s.name}
                </label>))}
            </div>
          </label>
          <label>资源面（不选=全量；仅列 custom_services，值走环境变量）
            {svcList !== null ? (
              <div className="org-bind-list">
                {svcList.length === 0 && <span className="hint">暂无自定义服务（资源 Hub 可建）</span>}
                {svcList.map(s => (
                  <label key={s.name} className={"org-bind-pick" + (resSel.has(s.name) ? ' on' : '')}>
                    <input type="checkbox" checked={resSel.has(s.name)} autoComplete="off"
                           onChange={() => { const n = new Set(resSel); n.has(s.name) ? n.delete(s.name) : n.add(s.name); setResSel(n); }} />
                    {s.name}
                  </label>))}
              </div>
            ) : (
              <input value={resText} onChange={e => setResText(e.target.value)} placeholder='服务名逗号分隔（admin 面不可读时手动填）' autoComplete='off' />
            )}
          </label>
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

function TaskHistory({ name, onClose }: { name: string; onClose: () => void }) {
  // 任务记录抽屉（DW-6）：GET /api/org/tasks?name=——员工（subagent_type
  // 点名）与临时工（agent_name 出勤）双归属合并，倒序含产物计数。
  const [data, setData] = useState<{ total: number; tasks: AgentTask[] } | null>(null);
  const [err, setErr] = useState('');
  const [attempt, setAttempt] = useState(0);

  // UI 巡检批：Esc 关闭（与编辑抽屉同款——键盘不锁死）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    let alive = true;                      // 竞态守卫（AC-5.3 同款）
    setData(null); setErr('');
    api<{ name: string; total: number; tasks: AgentTask[] }>(
      `/api/org/tasks?name=${encodeURIComponent(name)}&limit=100`)
      .then(r => { if (alive) setData({ total: r.total, tasks: r.tasks }); })
      .catch(e => { if (alive) setErr(String((e as Error).message || e)); });
    return () => { alive = false; };
  }, [name, attempt]);

  return (
    <div className="drawer-mask" onClick={onClose}>
      <div className="drawer org-tasks" onClick={e => e.stopPropagation()}>
        <header>
          <h3><ListTasks size={14} /> 任务记录 · {name}</h3>
          <button className="icon-btn" onClick={onClose}><X size={14} /></button>
        </header>
        <div className="drawer-body">
          {err && <div className="admin-msg err">{err} <button className="btn" onClick={() => setAttempt(a => a + 1)}>重试</button></div>}
          {!err && !data && <div className="org-task-empty"><Spinner /> 加载中……</div>}
          {data && data.tasks.length === 0 && (
            <div className="org-task-empty">
              尚未参与过任务。<br />
              <span className="org-card-hint">Task 点名 subagent_type="{name}" 派发后，此处留痕。</span>
            </div>
          )}
          {data && data.tasks.map((t, i) => (
            <div key={`${t.turn_id}-${t.agent_id}-${i}`} className="org-task-row">
              <div className="org-task-desc">{t.description || '（无任务描述）'}</div>
              <div className="org-task-meta">
                <span className="chip ghost">{t.subagent_type === name ? '点名派发' : '临时出勤'}</span>
                {t.session_title && <span className="org-task-sess" title={t.session_id ?? ''}>{t.session_title}</span>}
                {(t.files > 0 || t.artifacts > 0) && (
                  <span>{[t.files > 0 && `写文件 ${t.files}`, t.artifacts > 0 && `产物 ${t.artifacts}`]
                    .filter(Boolean).join(' · ')}</span>
                )}
                {t.at && <span className="org-task-at">{String(t.at).slice(0, 16).replace('T', ' ')}</span>}
              </div>
            </div>
          ))}
          {data && data.total > data.tasks.length && (
            <div className="org-task-more">仅显示最近 {data.tasks.length} 条（共 {data.total} 条）</div>
          )}
        </div>
        <footer>
          <span className="hint">归属真源：Task 工具卡（员工=subagent_type 点名；临时工=agent_name 出勤）。</span>
          <button className="btn" onClick={onClose}>关闭</button>
        </footer>
      </div>
    </div>
  );
}

// DW-7：员工岗位记忆抽屉（domain=e:<名>；org 域名进 def memory 键，
// 派发该员工的子任务时由引擎注入「岗位长期记忆」块）。
interface MemEntry { id: string; summary: string; content: string; created_at?: string }

function EmpMemory({ name, onClose }: { name: string; onClose: () => void }) {
  const domain = 'e:' + name;
  const [entries, setEntries] = useState<MemEntry[]>([]);
  const [sel, setSel] = useState<MemEntry | null>(null);
  const [text, setText] = useState('');
  const [summary, setSummary] = useState('');
  const [busy, setBusy] = useState(false);
  const reload = useCallback(async () => {
    try {
      const r = await api<{ entries: MemEntry[] }>('/api/memory/entries?domain=' + encodeURIComponent(domain));
      setEntries(r.entries);
    } catch (e) { setEntries([]); }
  }, [domain]);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  const open = (e: MemEntry | null) => { setSel(e); setText(e?.content ?? ''); setSummary(e?.summary ?? ''); };
  const save = async () => {
    if (!summary.trim() || !text.trim()) return;
    setBusy(true);
    try {
      if (sel) {
        await api('/api/memory/file', { method: 'PUT', body: JSON.stringify({ domain, id: sel.id, summary: summary.trim(), content: text }) });
      } else {
        await api('/api/memory/file', { method: 'POST', body: JSON.stringify({ domain, summary: summary.trim(), content: text }) });
      }
      setSel(null); setText(''); setSummary(''); await reload();
    } catch (e) { toast(String((e as Error).message || e), false); }
    setBusy(false);
  };
  const del = async (e: MemEntry) => {
    try {
      await api('/api/memory/entry?domain=' + encodeURIComponent(domain) + '&id=' + e.id, { method: 'DELETE' });
      if (sel?.id === e.id) open(null);
      await reload();
    } catch (err) { toast(String((err as Error).message || err), false); }
  };
  return (
    <div className="drawer-mask" onClick={onClose}>
      <div className="drawer org-mem" onClick={e => e.stopPropagation()}>
        <header>
          <h3>岗位记忆 · {name}</h3>
          <button className="icon-btn" onClick={onClose}><X size={14} /></button>
        </header>
        <div className="drawer-body">
          <p className="hint">员工域记忆在 Task 点名 {name} 时注入其子代理提示（岗位长期记忆块）。跨会话沉淀，管理员维护。</p>
          {sel === null ? (<>
            <div className="org-mem-list">
              {entries.map(e => (
                <div key={e.id} className="org-mem-row">
                  <button className="org-mem-open" onClick={() => open(e)}>{e.summary}</button>
                  <span className="hint">{e.created_at?.slice(0, 16)}</span>
                  <button className="icon-btn" onClick={() => void del(e)}><Trash size={12} /></button>
                </div>))}
              {entries.length === 0 && <div className="org-empty small">暂无岗位记忆——点下方「记一条」开写。</div>}
            </div>
            <button className="btn" onClick={() => open(null)}>记一条</button>
          </>) : (<>
            <label>摘要（单行）<input value={summary} onChange={e => setSummary(e.target.value)} placeholder='如：合同审查走三步流程' autoComplete='off' /></label>
            <label>正文<textarea rows={5} value={text} onChange={e => setText(e.target.value)} /></label>
            <div className="org-mem-foot">
              <button className="btn primary" disabled={busy} onClick={() => void save()}>{busy ? <Spinner /> : null}保存</button>
              <button className="btn ghost" onClick={() => open(null)}>取消</button>
            </div>
          </>)}
        </div>
      </div>
    </div>
  );
}
