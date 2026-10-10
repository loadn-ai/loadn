// 定时任务管理：全局列表（倒计时/暂停/编辑/删除）+ 新建/编辑表单
// （message 投递到现有会话 / new_session 到点新建；单次 / every / cron 触发）。
// 本地状态自取自放（对齐 SkillsTab 模式，不动 zustand store）。
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { toast } from '../stores/toasts';
import { useStore } from '../stores/sessions';
import { Clock, Pause, Pencil, Play, Plus, Trash, X } from './icons';
import { askConfirm } from '../stores/confirm';

export interface ScheduleInfo {
  id: number; session_id: string | null; session_title?: string | null;
  kind: 'message' | 'new_session';
  label: string | null; prompt: string;
  due_at: string; every_s: number | null; cron: string | null; cron_desc?: string | null;
  max_fires: number; fires: number;
  status: 'active' | 'paused' | 'done';
  title: string | null; profile: string | null; engine: string | null;
  last_fired_at: string | null;
  is_system?: boolean;      // 内置 job（🫀心跳）：不可编辑档位，可暂停/永久关
}

function fmtDue(iso: string): string {
  try {
    return new Date(iso).toLocaleString('zh-CN',
      { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

function countdown(iso: string): string {
  const diffMs = new Date(iso).getTime() - Date.now();
  if (diffMs <= 0) return '已到期';
  const m = Math.round(diffMs / 60000);
  if (m < 60) return `${m} 分钟后`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} 小时 ${m % 60} 分后`;
  return `${Math.floor(h / 24)} 天后`;
}

function fmtEvery(s: number): string {
  if (s % 86400 === 0) return `每 ${s / 86400} 天`;
  if (s % 3600 === 0) return `每 ${s / 3600} 小时`;
  if (s % 60 === 0) return `每 ${s / 60} 分钟`;
  return `每 ${s}s`;
}

/** 触发人读化：cron 预设短语 > every > 单次时刻 */
function triggerText(j: ScheduleInfo): string {
  if (j.cron) return j.cron_desc || j.cron;
  if (j.every_s) return fmtEvery(j.every_s);
  return `单次 ${fmtDue(j.due_at)}`;
}

/** 编辑回填（AC-5.1）：cron → 表单四态。预设形态（daily/weekly/hours）
 *  精确匹配才回落对应预设；其余（含 dow=7、区间、逗号）一律 custom 原文
 *  ——保真优先，不让预设 UI 打开即悄悄改写触发表达式。 */
function parseCronForForm(cron: string): {
  mode: 'daily' | 'weekly' | 'hours' | 'custom';
  hhmm: string; dow: string; everyH: string; text: string;
} {
  const two = (s: string) => s.padStart(2, '0');
  const m = cron.trim().split(/\s+/);
  if (m.length === 5 && /^\d+$/.test(m[0]) && /^\d+$/.test(m[1])
    && m[2] === '*' && m[3] === '*') {
    const hhmm = `${two(m[1])}:${two(m[0])}`;
    if (m[4] === '*')
      return { mode: 'daily', hhmm, dow: '1', everyH: '6', text: cron };
    if (/^[0-6]$/.test(m[4]))
      return { mode: 'weekly', hhmm, dow: m[4], everyH: '6', text: cron };
  }
  if (m.length === 5 && m[0] === '0' && m[1].startsWith('*/')
    && /^\d+$/.test(m[1].slice(2)) && m[2] === '*' && m[3] === '*' && m[4] === '*')
    return { mode: 'hours', hhmm: '20:00', dow: '1', everyH: m[1].slice(2), text: cron };
  return { mode: 'custom', hhmm: '20:00', dow: '1', everyH: '6', text: cron };
}

/** UTC iso → datetime-local 值（本地墙钟 YYYY-MM-DDTHH:MM） */
function toLocalInput(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function SchedulesTab({ filterSid, onClearFilter }: {
  filterSid?: string; onClearFilter?: () => void }) {
  const [jobs, setJobs] = useState<ScheduleInfo[]>([]);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [, setTick] = useState(0);       // 倒计时重渲染节拍

  async function reload() {
    try {
      const d = await api<{ schedules: ScheduleInfo[] }>('/api/schedules');
      setJobs(d.schedules);
    } catch (e) { setMsg({ t: `加载失败：${e instanceof Error ? e.message : e}`, err: true }); }
  }

  useEffect(() => { void reload(); }, []);
  useEffect(() => {
    const t = setInterval(() => { void reload(); setTick(x => x + 1); }, 15000);
    return () => clearInterval(t);
  }, []);

  const rows = useMemo(
    () => filterSid ? jobs.filter(j => j.session_id === filterSid) : jobs,
    [jobs, filterSid]);

  async function act(j: ScheduleInfo, status: 'active' | 'paused') {
    try {
      await api(`/api/schedules/${j.id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
      await reload();
      void useStore.getState().loadSessions();   // 会话头徽章 next_wake 同步
    } catch (e) { toast(`操作失败：${e instanceof Error ? e.message : e}`, false); }
  }

  async function del(j: ScheduleInfo) {
    const tip = j.is_system
      ? '（内置心跳：删除=永久关闭，重启后不再重建；临时停用请用暂停）'
      : '';
    if (!await askConfirm({ title: `删除定时任务「${j.label || j.prompt.slice(0, 30)}」？${tip}`, danger: true })) return;
    try {
      await api(`/api/schedules/${j.id}`, { method: 'DELETE' });
      await reload();
      void useStore.getState().loadSessions();
    } catch (e) { toast(`删除失败：${e instanceof Error ? e.message : e}`, false); }
  }

  return (
    <div className="admin-body">
      <div className="admin-toolbar">
        <button className="btn primary" onClick={() => setCreating(v => !v)}>
          {creating ? <X size={14} /> : <Plus size={14} />} {creating ? '收起新建' : '新建定时任务'}
        </button>
        {filterSid && (
          <button className="btn ghost sm" onClick={onClearFilter}>
            <X size={13} /> 只看本会话，点此看全部
          </button>
        )}
        {msg && <span className={msg.err ? 'admin-msg err' : 'admin-msg'}>{msg.t}</span>}
      </div>
      {creating && (
        <JobForm presetSid={filterSid} onDone={async () => {
          setCreating(false);
          await reload();
          void useStore.getState().loadSessions();
        }} />
      )}
      <div className="tbl-wrap">
        <table className="mcp-table">
          <thead>
            <tr>
              <th>任务</th><th>目标</th><th>触发</th><th>下次</th><th>次数</th>
              <th>状态</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(j => (
              <JobRow key={j.id} job={j} onAct={act} onDel={del} onChanged={reload} />
            ))}
            {!rows.length && (
              <tr><td colSpan={7} className="panel-empty">（无定时任务）</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <HooksAside />{/* P3：事件触发与 cron 并列展示（管理在 Webhooks tab） */}
      <RoutinesLib />{/* P11：模板库——Ready 一键启用 / Needs setup 缺什么 */}
    </div>
  );
}

/** 事件触发概览（只读并列卡——与 cron 时间触发对照；建改去管理中心 Webhooks tab） */
function HooksAside() {
  const [hooks, setHooks] = useState<{ id: number; name: string; enabled: number;
    rate_limit_per_min: number; last_fired_at: string | null }[]>([]);
  useEffect(() => {
    void (async () => {
      try {
        const d = await api<{ hooks: typeof hooks }>('/api/hooks');
        setHooks(d.hooks);
      } catch { /* 管理面不可达时静默——概览卡不炸调度页 */ }
    })();
  }, []);
  if (!hooks.length) return null;
  return (
    <div className="admin-toolbar" style={{ marginTop: 10, opacity: 0.9 }}>
      <span className="muted">事件触发（webhook）：</span>
      {hooks.map(h => (
        <span key={h.id} className={`sk-src ${h.enabled ? 'local' : 'off-tag'}`}
          title={h.last_fired_at ? `最近触发 ${h.last_fired_at}` : '未触发过'}>
          {h.name}（{h.enabled ? `${h.rate_limit_per_min}/min` : '停'}）
        </span>
      ))}
      <span className="muted">建改在管理中心 → Webhooks</span>
    </div>
  );
}

function JobRow({ job: j, onAct, onDel, onChanged }: {
  job: ScheduleInfo;
  onAct: (j: ScheduleInfo, s: 'active' | 'paused') => Promise<void>;
  onDel: (j: ScheduleInfo) => Promise<void>;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const openSession = (sid: string) => {
    location.hash = '';
    void useStore.getState().openSession(sid);
  };
  return (
    <>
      <tr className="clickable">
        <td>
          <div className="sched-label">
            {j.label || '（无标签）'}
            {j.is_system && (j.label || '').includes('🫀·low')
              ? <span className="chip" title="连续 3 轮无产出已降频为 2h（有产出自动恢复 30min）">降频中</span>
              : null}
          </div>
          <div className="sched-prompt" title={j.prompt}>{j.prompt.slice(0, 60)}</div>
        </td>
        <td>
          {j.session_id ? (
            // 六轮修 A7：new_session job 回填最新实例 sid（调度侧三轮修）——
            // 原来被 kind 门挡死，显示「新建 · title」不可点进实例
            <a className="link" onClick={() => openSession(j.session_id!)}
               title={j.session_id}>{j.session_title || j.session_id.slice(0, 18)}</a>
          ) : j.kind === 'message' ? (
            <span>（会话已删除）</span>
          ) : (
            <span>新建 · {j.title || '（默认标题）'}{j.engine ? ` · ${j.engine}` : ''}</span>
          )}
        </td>
        <td>{triggerText(j)}</td>
        <td>
          {j.status === 'active'
            ? <span className="sched-count"><Clock size={12} /> {countdown(j.due_at)}</span>
            : <span className="muted">{fmtDue(j.due_at)}</span>}
        </td>
        <td>{j.fires}/{j.max_fires}</td>
        <td>
          <span className={`sched-dot ${j.status}`} title={j.status} />
          {j.status === 'active' ? '运行中' : j.status === 'paused' ? '已暂停' : '已完成'}
        </td>
        <td>
          <div className="sched-ops">
            {j.status !== 'done' && (
              <button className="btn ghost sm" title={j.status === 'active' ? '暂停' : '恢复'}
                      onClick={() => void onAct(j, j.status === 'active' ? 'paused' : 'active')}>
                {j.status === 'active' ? <Pause size={13} /> : <Play size={13} />}
              </button>
            )}
            {j.status !== 'done' && !j.is_system && (
              <button className="btn ghost sm" title="编辑（标签/指令/触发/目标）"
                      onClick={() => setEditing(v => !v)}>
                <Pencil size={13} />
              </button>
            )}
            <button className="btn ghost sm danger-link"
                    title={j.is_system
                      ? '永久关闭（删除后重启不重建；暂停可临时停用）'
                      : '删除'}
                    onClick={() => void onDel(j)}><Trash size={13} /></button>
          </div>
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={7}>
            <JobForm job={j} onDone={async () => { setEditing(false); await onChanged(); }} />
          </td>
        </tr>
      )}
    </>
  );
}

/** 分段选择器（等宽格子，替代一串 chips） */
function Seg<T extends string>({ value, onChange, items, disabled }: {
  value: T; onChange: (v: T) => void;
  items: { value: T; label: string }[];
  disabled?: boolean;
}) {
  return (
    <div className={`sched-seg ${disabled ? 'disabled' : ''}`}>
      {items.map(i => (
        <button key={i.value} type="button" disabled={disabled && value !== i.value}
                className={`sched-seg-item ${value === i.value ? 'on' : ''}`}
                onClick={() => onChange(i.value)}>{i.label}</button>
      ))}
    </div>
  );
}

/** 表单字段单元：竖排 label + 控件 */
function Field({ label, children, hint }: {
  label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="sched-field">
      <span className="sched-field-k">{label}</span>
      {children}
      {hint && <span className="sched-field-hint">{hint}</span>}
    </label>
  );
}

type TrigMode = 'once' | 'every' | 'cron';

/** 新建 / 编辑共用表单。编辑态（job 给定）：初始值全量回填、类型锁定、
 *  提交走 PATCH（触发恒送——表单即当前真相）。 */
function JobForm({ job, presetSid, onDone }: {
  job?: ScheduleInfo; presetSid?: string; onDone: () => Promise<void> }) {
  const editing = !!job;
  const sessions = useStore(s => s.sessions);
  const profiles = useStore(s => s.profiles);
  const engines = useStore(s => s.engines);
  const defaultEngine = useStore(s => s.defaultEngine);

  const [kind, setKind] = useState<'message' | 'new_session'>(
    job?.kind ?? (presetSid ? 'message' : 'message'));
  const [sid, setSid] = useState(job?.session_id || presetSid || '');
  const [title, setTitle] = useState(job?.title || '');
  const [profile, setProfile] = useState(job?.profile || 'auto');
  const [engine, setEngine] = useState(job?.engine || '');

  const [trig, setTrig] = useState<TrigMode>(
    job ? (job.cron ? 'cron' : job.every_s ? 'every' : 'once') : 'cron');
  const [at, setAt] = useState(job && !job.cron && !job.every_s ? toLocalInput(job.due_at) : '');
  const [everyN, setEveryN] = useState(() => {
    if (job?.every_s) {
      if (job.every_s % 86400 === 0) return String(job.every_s / 86400);
      if (job.every_s % 3600 === 0) return String(job.every_s / 3600);
      return String(Math.max(1, Math.round(job.every_s / 60)));
    }
    return '1';
  });
  const [everyU, setEveryU] = useState(() => {
    if (job?.every_s) {
      if (job.every_s % 86400 === 0) return 'd';
      if (job.every_s % 3600 === 0) return 'h';
      return 'm';
    }
    return 'h';
  });
  // AC-5.1 编辑回填：原 cron 解析回预设态（daily/weekly/hours）或锁 custom——
  // 修「打开表单即默认值，只改 label 保存也会把 9:30 改写成 20:00」的误改触发
  const pc = job?.cron
    ? parseCronForForm(job.cron)
    : { mode: 'daily' as const, hhmm: '20:00', dow: '1', everyH: '6', text: '0 20 * * 1-5' };
  const [cronMode, setCronMode] = useState<'daily' | 'weekly' | 'hours' | 'custom'>(pc.mode);
  const [hhmm, setHhmm] = useState(pc.hhmm);
  const [dow, setDow] = useState(pc.dow);
  const [everyH, setEveryH] = useState(pc.everyH);
  const [cronText, setCronText] = useState(pc.text);
  const [label, setLabel] = useState(job?.label || '');
  const [prompt, setPrompt] = useState(job?.prompt || '');
  const [maxFires, setMaxFires] = useState(String(job?.max_fires ?? 20));
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  const cron = useMemo(() => {
    const [h, m] = (hhmm || '20:00').split(':').map(Number);
    const mm = String(Math.max(0, Math.min(59, m || 0)));
    const hh = String(Math.max(0, Math.min(23, h || 0)));
    if (cronMode === 'daily') return `${mm} ${hh} * * *`;
    if (cronMode === 'weekly') return `${mm} ${hh} * * ${dow}`;
    if (cronMode === 'hours') return `0 */${Math.max(1, Number(everyH) || 1)} * * *`;
    return cronText.trim();
  }, [cronMode, hhmm, dow, everyH, cronText]);

  const activeSessions = sessions.filter(s => s.status === 'active');
  const recurring = trig !== 'once';

  async function submit() {
    // AC-5.1 后半：编辑态触发字段变更须显式确认——回填已保真，但用户仍可能
    // 切错预设/误碰时刻；diff 原值与将提交值，不同才弹（非触发改动静默通过）
    if (editing && job) {
      const next = trig === 'cron' ? `cron ${cron}`
        : trig === 'every' ? fmtEvery(Number(everyN)
          * ({ m: 60, h: 3600, d: 86400 } as const)[everyU as 'm' | 'h' | 'd'])
        : `单次 ${at}`;
      const orig = job.cron ? `cron ${job.cron}`
        : job.every_s ? fmtEvery(job.every_s)
        : `单次 ${toLocalInput(job.due_at)}`;   // 同口径（本地墙钟），未改不误弹
      if (orig !== next
        && !await askConfirm({ title: `触发条件将改变：\n${orig}\n→ ${next}\n\n确认保存？`, danger: false }))
        return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const body: Record<string, unknown> = { label: label || null, prompt };
      if (kind === 'message') {
        if (!sid) throw new Error('选一个目标会话');
        body.session_id = sid;
      } else if (editing) {
        if (title) body.title = title;
        body.profile = profile && profile !== 'auto' ? profile : null;
        body.engine = engine || null;
      } else {
        if (title) body.title = title;
        if (profile && profile !== 'auto') body.profile = profile;
        if (engine) body.engine = engine;
      }
      if (trig === 'once') {
        if (!at) throw new Error('选一个触发时刻');
        body.at = new Date(at).toISOString();          // 本地墙钟 → UTC iso
      } else if (trig === 'every') {
        const sec = Number(everyN) * ({ m: 60, h: 3600, d: 86400 } as const)[everyU as 'm' | 'h' | 'd'];
        if (!sec || sec < 60) throw new Error('间隔最短 1 分钟');
        body.at = new Date().toISOString();
        body.every_s = sec;
      } else {
        body.cron = cron;
      }
      if (recurring) body.max_fires = Number(maxFires) || 20;
      if (editing) {
        await api(`/api/schedules/${job!.id}`, { method: 'PATCH', body: JSON.stringify(body) });
      } else {
        await api('/api/schedules', { method: 'POST',
          body: JSON.stringify({ ...body, kind }) });
      }
      await onDone();
    } catch (e) {
      setMsg({ t: `${editing ? '保存' : '创建'}失败：${e instanceof Error ? e.message : e}`, err: true });
    } finally { setBusy(false); }
  }

  const canSubmit = !!prompt.trim() && !busy
    && (kind === 'new_session' || !!sid)
    && (trig !== 'once' || !!at);

  return (
    <div className="sched-form">
      <div className="sched-form-title">{editing ? `编辑 #${job!.id}` : '新建定时任务'}</div>

      <div className="sched-form-sec">
        <span className="sched-form-sec-k">做什么</span>
        <Seg value={kind} onChange={setKind} disabled={editing} items={[
          { value: 'message', label: '投递到现有会话' },
          { value: 'new_session', label: '到点新建会话' },
        ]} />
        {editing && (
          <span className="sched-field-hint">类型创建后不可改（删了重建）</span>
        )}
        {kind === 'message' ? (
          <Field label="目标会话">
            <select value={sid} onChange={e => setSid(e.target.value)}>
              <option value="">（选择会话）</option>
              {activeSessions.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
          </Field>
        ) : (
          <div className="sched-grid-3">
            <Field label="会话标题">
              <input value={title} onChange={e => setTitle(e.target.value)}
                     placeholder="默认用任务标签" />
            </Field>
            <Field label="角色">
              <select value={profile} onChange={e => setProfile(e.target.value)}>
                <option value="auto">auto（按内容匹配）</option>
                {profiles.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
              </select>
            </Field>
            <Field label="引擎">
              <select value={engine} onChange={e => setEngine(e.target.value)}>
                <option value="">跟随默认（{defaultEngine}）</option>
                {Object.keys(engines).map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </Field>
          </div>
        )}
      </div>

      <div className="sched-form-sec">
        <span className="sched-form-sec-k">何时触发</span>
        <Seg value={trig} onChange={setTrig} items={[
          { value: 'cron', label: '按周期' },
          { value: 'every', label: '按间隔' },
          { value: 'once', label: '单次' },
        ]} />
        {trig === 'once' && (
          <Field label="触发时刻">
            <input type="datetime-local" value={at} onChange={e => setAt(e.target.value)} />
          </Field>
        )}
        {trig === 'every' && (
          <div className="sched-grid-2">
            <Field label="间隔">
              <div className="sched-inline">
                <input className="sched-num" type="number" min={1} value={everyN}
                       onChange={e => setEveryN(e.target.value)} />
                <select value={everyU} onChange={e => setEveryU(e.target.value)}>
                  <option value="m">分钟</option>
                  <option value="h">小时</option>
                  <option value="d">天</option>
                </select>
              </div>
            </Field>
            <Field label="次数上限">
              <input className="sched-num" type="number" min={1} max={100000} value={maxFires}
                     onChange={e => setMaxFires(e.target.value)} />
            </Field>
          </div>
        )}
        {trig === 'cron' && (
          <>
            <Seg value={cronMode} onChange={setCronMode} items={[
              { value: 'daily', label: '每天' },
              { value: 'weekly', label: '每周' },
              { value: 'hours', label: '每 N 小时' },
              { value: 'custom', label: '自定义' },
            ]} />
            <div className="sched-grid-2">
              {(cronMode === 'daily' || cronMode === 'weekly') && (
                <Field label="时刻">
                  <input type="time" value={hhmm} onChange={e => setHhmm(e.target.value)} />
                </Field>
              )}
              {cronMode === 'weekly' && (
                <Field label="星期">
                  <select value={dow} onChange={e => setDow(e.target.value)}>
                    {['周日', '周一', '周二', '周三', '周四', '周五', '周六'].map((lab, i) =>
                      <option key={i} value={i}>{lab}</option>)}
                  </select>
                </Field>
              )}
              {cronMode === 'hours' && (
                <Field label="间隔（小时）">
                  <input className="sched-num" type="number" min={1} max={23} value={everyH}
                         onChange={e => setEveryH(e.target.value)} />
                </Field>
              )}
              {cronMode === 'custom' && (
                <Field label="cron 表达式" hint="分 时 日 月 周（本地时区）">
                  <input className="mono" value={cronText} onChange={e => setCronText(e.target.value)}
                         placeholder="0 20 * * 1-5" />
                </Field>
              )}
              {cronMode !== 'custom' && (
                <Field label="次数上限">
                  <input className="sched-num" type="number" min={1} max={100000} value={maxFires}
                         onChange={e => setMaxFires(e.target.value)} />
                </Field>
              )}
            </div>
            {cronMode === 'custom' && (
              <Field label="次数上限">
                <input className="sched-num" type="number" min={1} max={100000} value={maxFires}
                       onChange={e => setMaxFires(e.target.value)} />
              </Field>
            )}
            <div className="sched-cron-preview">cron：<code>{cron}</code>
              {cronMode !== 'custom' && <span className="muted">（可切「自定义」微调）</span>}
            </div>
          </>
        )}
      </div>

      <div className="sched-form-sec">
        <span className="sched-form-sec-k">说什么</span>
        <div className="sched-grid-2">
          <Field label="标签">
            <input value={label} onChange={e => setLabel(e.target.value)}
                   placeholder="如：比赛参赛 / 到期巡检" />
          </Field>
        </div>
        <Field label="到点投递的指令"
               hint="撞上运行中的 loadn 任务会作为插话实时注入（不等排队）">
          <textarea rows={3} value={prompt} onChange={e => setPrompt(e.target.value)}
                    placeholder="到点交给 agent 做什么" />
        </Field>
      </div>

      <div className="sched-form-foot">
        {msg && <span className="admin-err">{msg.t}</span>}
        <button className="btn primary" disabled={!canSubmit} onClick={() => void submit()}>
          {editing ? '保存修改' : '创建'}
        </button>
      </div>
    </div>
  );
}


/** P11 模板库：例程模板卡片（安装=复制为用户 schedule，与平台升级解耦） */
export function RoutinesLib() {
  const [items, setItems] = useState<RoutineInfo[]>([]);
  useEffect(() => {
    void (async () => {
      try {
        const d = await api<{ routines: RoutineInfo[] }>('/api/routines');
        setItems(d.routines);
      } catch { /* 静默 */ }
    })();
  }, []);
  async function install(key: string) {
    try {
      const d = await api<{ job: { id: number; label: string }; existing?: boolean }>(
        `/api/routines/${key}/install`, { method: 'POST' });
      toast(d.existing
        ? `「${d.job.label}」此前已安装——已定位既有任务（未重复创建）`
        : `已安装「${d.job.label}」为你的定时任务（可在上方列表编辑）`);
    } catch (e) { toast(`安装失败：${String(e)}`, false); }
  }
  if (!items.length) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <b style={{ fontSize: 'var(--fs-lg)' }}>模板库</b>
      <div className="admin-list">
        {items.map(t => (
          <div key={t.key} className="hub-card slim">
            <div className="sk-head">
              <b>{t.name}</b>
              <span className={`sk-src ${t.ready ? 'local' : 'off-tag'}`}>
                {t.ready ? 'Ready' : '需配置'}
              </span>
              <span className="sk-src ext">{t.cron}</span>
            </div>
            <div className="sk-desc">{t.description}</div>
            {!t.ready && (
              <div className="admin-err" style={{ fontSize: 'var(--fs-md)' }}>
                缺：{t.missing.join('、')}
                <button className="link" onClick={() => {
                  location.hash = '#/admin/settings';
                }}>去配置 →</button>
              </div>
            )}
            <div className="sk-foot">
              <span className="sk-time">建议档：notify 推送</span>
              <button className="btn sm primary" disabled={!t.ready}
                onClick={() => void install(t.key)}>一键启用</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
interface RoutineInfo { key: string; name: string; cron: string; description: string; ready: boolean; missing: string[] }
