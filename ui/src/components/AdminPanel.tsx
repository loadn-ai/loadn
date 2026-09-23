// 管理中心（统一管理页）：Skills / 工具（MCP / 内建开关）/ 设置（含外观主题）/
// 定时 / 成本分析——原侧栏三个入口（暗色/成本/管理）合并于此
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { fetchZhDesc } from '../api/zh';
import { useStore } from '../stores/sessions';
import SkillEditor from './SkillEditor';
import SchedulesTab from './SchedulePanel';
import InstallDialog from './InstallDialog';
import CostTab from './CostPanel';
import SecurityTab from './SecurityPanel';
import { Plus, Upload, Globe, Sun, Moon } from './icons';

function EgressPanel() {
  // 产品视角：用户关心「数据流向了哪些域、各多少次、有没有被拒」，
  // 平铺流水只是原料——聚合成域维度，行可展开看时间点
  const [events, setEvents] = useState<{ ts: string; host: string; decision: string }[]>([]);
  const [mode, setMode] = useState('');
  const [allow, setAllow] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const load = async () => {
    try {
      const d = await api<{ events: { ts: string; host: string; decision: string }[]; mode: string; allow: string[] }>(
        '/api/admin/egress?n=100');
      setEvents(d.events); setMode(d.mode); setAllow(d.allow ?? []);
    } catch { /* 忽略 */ }
  };
  useEffect(() => { void load(); const t = setInterval(() => void load(), 15000); return () => clearInterval(t); }, []);

  const GW = 'llm-gw.internal';
  const byHost = new Map<string, { n: number; denied: number; last: string; times: string[] }>();
  for (const e of events) {
    const h = e.host || '?';
    const cur = byHost.get(h) ?? { n: 0, denied: 0, last: '', times: [] };
    cur.n += 1;
    if (!String(e.decision).startsWith('allow')) cur.denied += 1;
    cur.last = e.ts;
    cur.times.push((e.ts || '').slice(11, 19));
    byHost.set(h, cur);
  }
  const gw = byHost.get(GW);
  byHost.delete(GW);
  const hosts = [...byHost.entries()].sort((a, b) => b[1].n - a[1].n);
  const deniedTotal = [...byHost.values()].reduce((s2, v) => s2 + v.denied, 0);
  const external = hosts.filter(([, v]) => v.denied === 0).length;

  return (
    <div className="pad">
      <div style={{ display: 'flex', gap: 16, marginBottom: 12, flexWrap: 'wrap', fontSize: 13 }}>
        <span>模式 <b style={{ color: mode === 'enforce' ? undefined : '#d4a017' }}>{mode || '-'}</b></span>
        <span>窗口内请求 <b>{events.length}</b></span>
        <span>放行域 <b style={{ color: '#3aa675' }}>{external}</b></span>
        <span>被拒 <b style={{ color: deniedTotal ? 'var(--accent,#e5484d)' : undefined }}>{deniedTotal}</b></span>
        {gw && <span className="muted">另有模型调用 {gw.n} 次（走内部网关，不外发）</span>}
      </div>
      <div className="muted" style={{ marginBottom: 10, fontSize: 12, lineHeight: 1.6 }}>
        所有对外请求经出口代理审计；模型对话经内部安全网关转发，
        真实 API 凭证不出控制域。点域名行查看请求时间点。
      </div>
      <table className="kv-table" style={{ width: '100%' }}>
        <thead><tr><th>域名</th><th style={{ width: 70 }}>次数</th><th style={{ width: 90 }}>判定</th><th style={{ width: 90 }}>最近</th></tr></thead>
        <tbody>
          {hosts.length === 0 && (
            <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 16 }}>
              （窗口内没有对外请求——模型调用不算外发）
            </td></tr>
          )}
          {hosts.map(([h, v]) => (
            <>
              <tr key={h} style={{ cursor: 'pointer' }} onClick={() => setExpanded(expanded === h ? null : h)}>
                <td>
                  <span style={{ marginRight: 6, display: 'inline-block', transition: '.15s', transform: expanded === h ? 'rotate(90deg)' : undefined }}>›</span>
                  {h}
                </td>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{v.n}</td>
                <td style={{ color: v.denied ? 'var(--accent,#e5484d)' : '#3aa675' }}>
                  {v.denied ? `拒绝 ${v.denied}/${v.n}` : '放行'}
                </td>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{v.last.slice(11, 19)}</td>
              </tr>
              {expanded === h && (
                <tr key={h + '-x'}>
                  <td colSpan={4} className="muted" style={{ fontSize: 12, padding: '4px 12px' }}>
                    {v.times.slice(0, 20).join(' · ')}{v.times.length > 20 ? ' …' : ''}
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
      {allow.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>白名单（其余域名一律拒绝）</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {allow.map(a => (
              <span key={a} className="muted" style={{ fontSize: 12, border: '1px solid var(--border,#333)', borderRadius: 999, padding: '2px 10px' }}>
                {a}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export type AdminTab = 'skills' | 'tools' | 'settings' | 'schedules' | 'cost' | 'egress' | 'security';

export interface SkillItem {
  name: string; description: string; mtime: string; disabled: boolean;
  source?: { via: string; repo?: string; subpath?: string; installed_at?: string };
}
export interface McpServer { name: string; spec: Record<string, any>; sessions_overriding: number; session_only?: boolean }
export interface ProfileTools { name: string; description: string; disallowed_tools: string[] }

export default function AdminPanel({ onClose, initialTab, filterSid, onClearFilter }:
  { onClose: () => void; initialTab?: AdminTab;
    filterSid?: string; onClearFilter?: () => void }) {
  const [tab, setTab] = useState<AdminTab>(initialTab ?? 'skills');

  return (
    <div className="admin-page">
      <div className="admin-head">
        <h3>管理中心</h3>
        <button className="btn ghost" onClick={onClose}>← 返回</button>
      </div>
      <div className="admin-tabs">
        <button className={`tab ${tab === 'skills' ? 'on' : ''}`} onClick={() => setTab('skills')}>Skills</button>
        <button className={`tab ${tab === 'tools' ? 'on' : ''}`} onClick={() => setTab('tools')}>工具</button>
        <button className={`tab ${tab === 'settings' ? 'on' : ''}`} onClick={() => setTab('settings')}>设置</button>
        <button className={`tab ${tab === 'schedules' ? 'on' : ''}`} onClick={() => setTab('schedules')}>定时</button>
        <button className={`tab ${tab === 'cost' ? 'on' : ''}`} onClick={() => setTab('cost')}>成本</button>
        <button className={`tab ${tab === 'egress' ? 'on' : ''}`} onClick={() => setTab('egress')}>流量</button>
        <button className={`tab ${tab === 'security' ? 'on' : ''}`} onClick={() => setTab('security')}>安全</button>
      </div>
      {tab === 'skills' ? <SkillsTab /> : tab === 'tools' ? <ToolsTab />
        : tab === 'schedules'
          ? <SchedulesTab filterSid={filterSid} onClearFilter={onClearFilter} />
          : tab === 'cost' ? <CostTab />
          : tab === 'egress' ? <EgressPanel />
          : tab === 'security' ? <SecurityTab onClose={onClose} /> : <SettingsTab />}
    </div>
  );
}

/* ================= Skills ================= */
function SkillsTab() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [zh, setZh] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  async function reload() {
    const d = await api<{ skills: SkillItem[] }>('/api/skills');
    setSkills(d.skills);
    // 英文描述 → 后端翻译（kv 缓存）补中文简介；失败回退原文
    void fetchZhDesc(d.skills).then(setZh);
  }
  useEffect(() => { void reload(); }, []);

  async function del(s: SkillItem) {
    if (!confirm(`删除 skill「${s.name}」？整目录移除，不可恢复。`)) return;
    try {
      await api(`/api/skills/${encodeURIComponent(s.name)}?force=true`, { method: 'DELETE' });
      setMsg(`已删除 ${s.name}`);
    } catch (e) { setMsg(`删除失败：${String(e)}`); }
    void reload();
  }

  async function toggle(s: SkillItem) {
    try {
      await api(`/api/skills/${encodeURIComponent(s.name)}/toggle`, {
        method: 'POST', body: JSON.stringify({ disabled: !s.disabled }) });
      setMsg(s.disabled ? `已启用 ${s.name}（新会话生效，引用它的 active 会话已补挂）`
                        : `已禁用 ${s.name}（新会话不挂载，active 会话下一 turn 失效）`);
    } catch (e) { setMsg(`操作失败：${String(e)}`); }
    void reload();
  }

  async function uploadZip(f: File) {
    const fd = new FormData();
    fd.append('file', f);
    setMsg(`安装 ${f.name} 中…`);
    try {
      const d = await api<{ installed: string[]; skipped?: string[] }>(
        withQ('/api/skills/upload'), { method: 'POST', body: fd });
      setMsg(`已安装：${d.installed.join(', ')}${d.skipped?.length ? '（跳过 ' + d.skipped.join('; ') + '）' : ''}`);
    } catch (e) { setMsg(`安装失败：${String(e)}`); }
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
              {msg && <span className="admin-msg">{msg}</span>}
            </div>
            {creating && <NewSkillForm onDone={() => { setCreating(false); void reload(); }} />}
            <div className="skill-grid">
              {skills.map(s => (
                <div key={s.name} className={`skill-card${s.disabled ? ' off' : ''}`}>
                  <div className="sk-head">
                    <b>{s.name}</b>
                    {s.disabled && <span className="sk-src off-tag">已禁用</span>}
                    <span className={`sk-src ${s.source ? 'ext' : 'local'}`}>
                      {s.source ? (s.source.repo ?? s.source.via) : '本地'}
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

function withQ(url: string): string {
  const t = new URLSearchParams(location.search).get('token');
  return t ? url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t) : url;
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
function AppearanceCard() {
  const theme = useStore(s => s.theme);
  const toggleTheme = useStore(s => s.toggleTheme);
  return (
    <div className="setting-card">
      <h4>外观</h4>
      <div className="setting-row">
        <span className="setting-k">主题</span>
        <button className={`chip ${theme === 'light' ? 'on' : ''}`}
          onClick={() => { if (theme !== 'light') toggleTheme(); }}>
          <Sun size={13} /> 亮色
        </button>
        <button className={`chip ${theme === 'dark' ? 'on' : ''}`}
          onClick={() => { if (theme !== 'dark') toggleTheme(); }}>
          <Moon size={13} /> 暗色
        </button>
        <span className="muted">初始跟随系统，选择后记住（存本机）</span>
      </div>
    </div>
  );
}

/* ================= 平台设置 ================= */
interface TitleGenCfg {
  enabled: boolean; api_base: string; model: string;
  api_key_set: boolean; api_key_hint: string;
}
interface ResCfg {
  ocr_url: string; sandbox_url: string; cdp_url: string; proxy: string;
  sms_url: string; sms_phone: string; vlm_api_base: string; vlm_model: string; adb_addr: string;
  mail_imap: string; mail_smtp: string; mail_user: string;
  sandbox_api_key_set: boolean; sandbox_api_key_hint: string;
  sms_token_set: boolean; sms_token_hint: string;
  mail_auth_code_set: boolean; mail_auth_code_hint: string;
  vlm_api_key_set: boolean; vlm_api_key_hint: string;
  twocaptcha_key_set: boolean; twocaptcha_key_hint: string;
  bocha_key_set: boolean; bocha_key_hint: string;
}

const RES_SECRET_FIELDS: [key: string, label: string][] = [
  ['sandbox_api_key', '沙箱 API Key'],
  ['sms_token', '短信 Token'],
  ['mail_auth_code', '126 授权码'],
  ['vlm_api_key', 'VLM API Key'],
  ['twocaptcha_key', '2captcha Key'],
  ['bocha_key', '博查 Key'],
];

interface ConvRow {
  name: string; timeout_s: number; stall_timeout_s: number; max_turns: number | null;
}

function SettingsTab() {
  const [tg, setTg] = useState<TitleGenCfg | null>(null);
  const [keyInput, setKeyInput] = useState('');
  const [run, setRun] = useState<{ max_concurrent_turns: number; replay_max_events: number;
                                   events_retain_days: number } | null>(null);
  const [conv, setConv] = useState<ConvRow[] | null>(null);
  const [convMsg, setConvMsg] = useState('');
  const [res, setRes] = useState<ResCfg | null>(null);
  const [resKeys, setResKeys] = useState<Record<string, string>>({});
  const [resMsg, setResMsg] = useState('');
  const [pingRows, setPingRows] = useState<[string, { ok: boolean; msg: string; ms?: number }][] | null>(null);
  const [pinging, setPinging] = useState(false);
  const [msg, setMsg] = useState('');
  const [testing, setTesting] = useState(false);

  useEffect(() => { void reload(); }, []);
  async function reload() {
    const d = await api<{ titlegen: TitleGenCfg; run: NonNullable<typeof run>;
                          convergence: ConvRow[]; resources: ResCfg }>('/api/settings');
    setTg(d.titlegen); setRun(d.run); setConv(d.convergence); setRes(d.resources); setKeyInput(''); setResKeys({});
  }

  async function saveTitlegen() {
    if (!tg) return;
    try {
      const body: Record<string, unknown> = {
        enabled: tg.enabled, api_base: tg.api_base, model: tg.model,
      };
      if (keyInput.trim()) body.api_key = keyInput.trim();
      const d = await api<{ titlegen: TitleGenCfg }>('/api/settings/titlegen', {
        method: 'PUT', body: JSON.stringify(body) });
      setTg(d.titlegen); setKeyInput('');
      setMsg('自动标题设置已保存（即时生效）');
    } catch (e) { setMsg(`保存失败：${String(e)}`); }
  }

  async function testConn() {
    setTesting(true); setMsg('测试中…');
    try {
      // 先保存当前编辑值再测（key 留空时沿用已存 key）
      await saveTitlegenQuiet();
      const d = await api<{ reply: string }>('/api/settings/titlegen/test', { method: 'POST' });
      setMsg(`✓ 连通正常，模型回复：「${d.reply}」`);
    } catch (e) { setMsg(`✗ 测试失败：${String(e)}`); }
    finally { setTesting(false); }
  }

  async function saveTitlegenQuiet() {
    if (!tg) return;
    const body: Record<string, unknown> = { enabled: tg.enabled, api_base: tg.api_base, model: tg.model };
    if (keyInput.trim()) body.api_key = keyInput.trim();
    const d = await api<{ titlegen: TitleGenCfg }>('/api/settings/titlegen', {
      method: 'PUT', body: JSON.stringify(body) });
    setTg(d.titlegen); setKeyInput('');
  }

  async function saveRun() {
    if (!run) return;
    try {
      await api('/api/settings/run', { method: 'PUT', body: JSON.stringify(run) });
      setMsg('运行参数已保存（并发数重启服务后生效）');
    } catch (e) { setMsg(`保存失败：${String(e)}`); }
  }

  function updConv(name: string, patch: Partial<ConvRow>) {
    setConv(rs => rs?.map(r => r.name === name ? { ...r, ...patch } : r) ?? null);
  }

  async function saveConv() {    if (!conv) return;
    try {
      const d = await api<{ profiles: ConvRow[] }>('/api/settings/convergence', {
        method: 'PUT',
        body: JSON.stringify({ profiles: Object.fromEntries(conv.map(r => [r.name, {
          timeout_s: r.timeout_s, stall_timeout_s: r.stall_timeout_s, max_turns: r.max_turns,
        }])) }),
      });
      setConv(d.profiles);
      setConvMsg('✓ 已保存（下一 turn 生效）');
    } catch (e) { setConvMsg(`保存失败：${String(e)}`); }
  }

  async function saveResources() {
    if (!res) return;
    try {
      const body: Record<string, unknown> = {};
      for (const k of ['ocr_url', 'sandbox_url', 'cdp_url', 'proxy', 'sms_url',
                       'sms_phone', 'mail_imap', 'mail_smtp', 'mail_user',
                       'vlm_api_base', 'vlm_model', 'adb_addr']) body[k] = (res as any)[k];
      for (const [k] of RES_SECRET_FIELDS) {
        const v = (resKeys[k] ?? '').trim();
        if (v) body[k] = v;      // 留空 = 保持不变
      }
      const d = await api<{ resources: ResCfg }>('/api/settings/resources', {
        method: 'PUT', body: JSON.stringify(body) });
      setRes(d.resources); setResKeys({});
      setResMsg('✓ 已保存（即时生效）');
    } catch (e) { setResMsg(`保存失败：${String(e)}`); }
  }

  async function testResources() {
    setPinging(true); setResMsg('探测中…（先保存当前编辑值）');
    try {
      await saveResources();
      const d = await api<Record<string, { ok: boolean; msg: string; ms?: number }>>(
        '/api/settings/resources/test', { method: 'POST' });
      setPingRows(Object.entries(d));
      setResMsg('');
    } catch (e) { setResMsg(`探测失败：${String(e)}`); }
    finally { setPinging(false); }
  }

  if (!tg || !run) return <div className="admin-body muted">加载中…</div>;
  return (
    <div className="admin-body">
      <AppearanceCard />
      <div className="setting-card">
        <h4>自动标题 <span className="muted">（首条消息 → 外部小模型生成任务名）</span></h4>
        <div className="setting-row">
          <span className="setting-k">状态</span>
          <button className={`chip ${tg.enabled ? 'on' : ''}`}
            onClick={() => setTg({ ...tg, enabled: !tg.enabled })}>{tg.enabled ? '启用' : '停用'}</button>
          <span className="muted">{tg.api_key_set ? '已配置 API Key' : '未配置 API Key（不会生成）'}</span>
        </div>
        <div className="setting-row">
          <span className="setting-k">API Base</span>
          <input value={tg.api_base} onChange={e => setTg({ ...tg, api_base: e.target.value })}
            placeholder="https://ark.cn-beijing.volces.com/api/v3" />
        </div>
        <div className="setting-row">
          <span className="setting-k">模型</span>
          <input value={tg.model} onChange={e => setTg({ ...tg, model: e.target.value })}
            placeholder="doubao-seed-2-0-mini-260428" />
        </div>
        <div className="setting-row">
          <span className="setting-k">API Key</span>
          <input type="password" value={keyInput} onChange={e => setKeyInput(e.target.value)}
            placeholder={tg.api_key_set ? `已保存（${tg.api_key_hint}），留空不改` : 'sk-… / 方舟 key'} />
        </div>
        <div className="setting-row">
          <button className="btn primary sm" onClick={() => void saveTitlegen()}>保存</button>
          <button className="btn sm" disabled={testing} onClick={() => void testConn()}>{testing ? '测试中…' : '测试连通'}</button>
          {msg && <span className="admin-msg">{msg}</span>}
        </div>
        <div className="setting-note muted">规则：未显式命名的任务，在首条消息发出后自动起名；手动改名或显式标题永远优先，只生成一次。</div>
      </div>

      <div className="setting-card">
        <h4>运行参数</h4>
        <div className="setting-row">
          <span className="setting-k">并发任务数</span>
          <input type="number" min={1} max={16} value={run.max_concurrent_turns}
            onChange={e => setRun({ ...run, max_concurrent_turns: Number(e.target.value) })} />
          <span className="muted">同时在跑的 claude 进程上限，超出排队（重启服务后生效）</span>
        </div>
        <div className="setting-row">
          <span className="setting-k">回放条数</span>
          <input type="number" min={20} max={2000} value={run.replay_max_events}
            onChange={e => setRun({ ...run, replay_max_events: Number(e.target.value) })} />
          <span className="muted">重进会话只回放活跃任务尾部这些条（无活跃任务零回放；断线补发不受限）</span>
        </div>
        <div className="setting-row">
          <span className="setting-k">事件保留天数</span>
          <input type="number" min={0.5} max={365} step={0.5} value={run.events_retain_days}
            onChange={e => setRun({ ...run, events_retain_days: Number(e.target.value) })} />
          <span className="muted">已完成任务的过线事件自动清理（活跃任务永不清理；聊天记录不受影响）</span>
        </div>
        <div className="setting-row">
          <button className="btn primary sm" onClick={() => void saveRun()}>保存</button>
        </div>
      </div>

      {conv && <div className="setting-card">
        <h4>收敛度 <span className="muted">（防跑飞三闸，按角色：硬超时 / 静默判死 / 工具轮次上限）</span></h4>
        <div className="tbl-wrap"><table className="mcp-table">
          <thead><tr><th>角色</th><th>硬超时（分）</th><th>静默判死（分）</th><th>工具轮次上限</th></tr></thead>
          <tbody>{conv.map(r => (
            <tr key={r.name}>
              <td>{r.name}</td>
              <td><input type="number" min={1} max={1440} value={r.timeout_s ? Math.round(r.timeout_s / 60) : ''}
                placeholder="不限"
                onChange={e => updConv(r.name, {
                  timeout_s: e.target.value === '' ? 0 : Math.max(1, Math.round(Number(e.target.value)) || 1) * 60 })} /></td>
              <td><input type="number" min={1} max={1440} value={Math.round(r.stall_timeout_s / 60)}
                onChange={e => updConv(r.name, {
                  stall_timeout_s: Math.max(1, Math.round(Number(e.target.value)) || 1) * 60 })} /></td>
              <td><input type="number" min={1} max={1000} value={r.max_turns ?? ''}
                placeholder="不限"
                onChange={e => updConv(r.name, {
                  max_turns: e.target.value === '' ? null : Number(e.target.value) })} /></td>
            </tr>
          ))}</tbody>
        </table></div>
        <div className="setting-row">
          <button className="btn primary sm" onClick={() => void saveConv()}>保存</button>
          {convMsg && <span className="admin-msg">{convMsg}</span>}
        </div>
        <div className="setting-note muted">硬超时 = turn 最长运行；静默判死 = 事件流 + transcript 双静默超阈值才杀
          （不误杀长 bash）；轮次上限 = agent 工具调用循环轮数（触顶报 error_max_turns，留空不限）。
          运行中 turn 不受影响，下一 turn 生效。</div>
      </div>}

      {res && <div className="setting-card">
        <h4>外部资源 <span className="muted">（agent 动手能力：OCR/沙箱/短信/VLM/打码/搜索/真机；密钥只存不回显）</span></h4>
        <div className="setting-row">
          <span className="setting-k">OCR 服务</span>
          <input value={res.ocr_url} onChange={e => setRes({ ...res, ocr_url: e.target.value })}
            placeholder="http://127.0.0.1:8686" />
        </div>
        <div className="setting-row">
          <span className="setting-k">AIO 沙箱</span>
          <input value={res.sandbox_url} onChange={e => setRes({ ...res, sandbox_url: e.target.value })}
            placeholder="http://127.0.0.1:21111" />
          <input type="password" value={resKeys.sandbox_api_key ?? ''}
            onChange={e => setResKeys({ ...resKeys, sandbox_api_key: e.target.value })}
            placeholder={res.sandbox_api_key_set ? `已保存（${res.sandbox_api_key_hint}），留空不改` : 'your-key'} />
        </div>
        <div className="setting-row">
          <span className="setting-k">沙箱 CDP</span>
          <input value={res.cdp_url} onChange={e => setRes({ ...res, cdp_url: e.target.value })}
            placeholder="http://127.0.0.1:21111/cdp" />
          <span className="muted">fetch_page / playwright 直连用</span>
        </div>
        <div className="setting-row">
          <span className="setting-k">代理</span>
          <input value={res.proxy} onChange={e => setRes({ ...res, proxy: e.target.value })}
            placeholder="http://127.0.0.1:7890（空=不可用）" />
        </div>
        <div className="setting-row">
          <span className="setting-k">短信服务</span>
          <input value={res.sms_url} onChange={e => setRes({ ...res, sms_url: e.target.value })}
            placeholder="https://sms.example.test:30443" />
          <input type="password" value={resKeys.sms_token ?? ''}
            onChange={e => setResKeys({ ...resKeys, sms_token: e.target.value })}
            placeholder={res.sms_token_set ? `已保存（${res.sms_token_hint}），留空不改` : 'token'} />
          <input value={res.sms_phone} onChange={e => setRes({ ...res, sms_phone: e.target.value })}
            placeholder="+86 1…" title="手机号" style={{ maxWidth: 130 }} />
        </div>
        <div className="setting-row">
          <span className="setting-k">平台邮箱</span>
          <input value={res.mail_user} onChange={e => setRes({ ...res, mail_user: e.target.value })}
            placeholder="user@example.com" title="邮箱地址（注册/登录收验证码用）" />
          <input type="password" value={resKeys.mail_auth_code ?? ''}
            onChange={e => setResKeys({ ...resKeys, mail_auth_code: e.target.value })}
            placeholder={res.mail_auth_code_set ? `已保存（${res.mail_auth_code_hint}），留空不改` : '126 授权码（非登录密码）'} />
          <input value={res.mail_imap} onChange={e => setRes({ ...res, mail_imap: e.target.value })}
            placeholder="imap.126.com:993" title="IMAP" style={{ maxWidth: 160 }} />
          <input value={res.mail_smtp} onChange={e => setRes({ ...res, mail_smtp: e.target.value })}
            placeholder="smtp.126.com:465" title="SMTP" style={{ maxWidth: 160 }} />
        </div>
        <div className="setting-row">
          <span className="setting-k">VLM 模型</span>
          <input value={res.vlm_model} onChange={e => setRes({ ...res, vlm_model: e.target.value })}
            placeholder="doubao-seed-2-1-turbo-260628" />
          <input value={res.vlm_api_base} onChange={e => setRes({ ...res, vlm_api_base: e.target.value })}
            placeholder="API Base（空=继承自动标题）" />
          <input type="password" value={resKeys.vlm_api_key ?? ''}
            onChange={e => setResKeys({ ...resKeys, vlm_api_key: e.target.value })}
            placeholder={res.vlm_api_key_set ? `已保存（${res.vlm_api_key_hint}），留空不改`
              : (res.vlm_api_key_hint || '留空继承自动标题 key')} />
        </div>
        <div className="setting-row">
          <span className="setting-k">2captcha</span>
          <input type="password" value={resKeys.twocaptcha_key ?? ''}
            onChange={e => setResKeys({ ...resKeys, twocaptcha_key: e.target.value })}
            placeholder={res.twocaptcha_key_set ? `已保存（${res.twocaptcha_key_hint}），留空不改` : 'key'} />
          <span className="setting-k" style={{ paddingLeft: 12 }}>博查</span>
          <input type="password" value={resKeys.bocha_key ?? ''}
            onChange={e => setResKeys({ ...resKeys, bocha_key: e.target.value })}
            placeholder={res.bocha_key_set ? `已保存（${res.bocha_key_hint}），留空不改` : 'key'} />
        </div>
        <div className="setting-row">
          <span className="setting-k">adb 真机</span>
          <input value={res.adb_addr} onChange={e => setRes({ ...res, adb_addr: e.target.value })}
            placeholder="127.0.0.1:5555" />
        </div>
        <div className="setting-row">
          <button className="btn primary sm" onClick={() => void saveResources()}>保存</button>
          <button className="btn sm" disabled={pinging} onClick={() => void testResources()}>
            {pinging ? '探测中…' : '测试全部'}
          </button>
          {resMsg && <span className="admin-msg">{resMsg}</span>}
        </div>
        {pingRows && (
          <div className="tbl-wrap">
            <table className="mcp-table res-test-table">
              <thead><tr><th>资源</th><th>状态</th><th>耗时</th><th>说明</th></tr></thead>
              <tbody>
                {pingRows.map(([name, r]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td>{r.ok ? '✓ OK' : '✗ FAIL'}</td>
                    <td>{r.ms != null ? `${r.ms}ms` : '—'}</td>
                    <td className="mono-cell">{r.msg}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="setting-note muted">
          沙箱同时是全局 MCP server（工具 tab 里「sandbox」），agent 原生获得浏览器/命令行工具；
          密钥存 config.yaml，agent 经 <code>python3 "$WORKDADDY_CLI" r …</code> 调用，不进环境变量。
        </div>
      </div>}
    </div>
  );
}

/* ================= 工具（MCP + 内建开关） ================= */
function ToolsTab() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [profiles, setProfiles] = useState<ProfileTools[]>([]);
  const [builtin, setBuiltin] = useState<string[]>([]);
  const [editing, setEditing] = useState<McpServer | 'new' | null>(null);
  const [msg, setMsg] = useState('');

  async function reload() {
    const d = await api<{ servers: McpServer[]; profiles: ProfileTools[]; builtin_tools: string[] }>('/api/tools');
    setServers(d.servers); setProfiles(d.profiles); setBuiltin(d.builtin_tools);
  }
  useEffect(() => { void reload(); }, []);

  async function del(s: McpServer) {
    if (!confirm(`删除全局 MCP server「${s.name}」？（会话级覆盖不受影响）`)) return;
    try {
      await api(`/api/tools/mcp/${encodeURIComponent(s.name)}`, { method: 'DELETE' });
      setMsg(`已删除 ${s.name}`);
    } catch (e) { setMsg(String(e)); }
    void reload();
  }

  async function toggleTool(p: ProfileTools, tool: string) {
    const cur = p.disallowed_tools;
    const next = cur.includes(tool) ? cur.filter(t => t !== tool) : [...cur, tool];
    try {
      await api(`/api/tools/profile/${encodeURIComponent(p.name)}`, {
        method: 'PUT', body: JSON.stringify({ disallowed_tools: next }),
      });
    } catch (e) { setMsg(String(e)); }
    void reload();
  }

  return (
    <div className="admin-body">
      {editing !== null ? (
        <McpForm init={editing === 'new' ? null : editing} onDone={() => { setEditing(null); void reload(); }} />
      ) : (
        <>
          <div className="admin-toolbar">
            <button className="btn sm primary" onClick={() => setEditing('new')}><Plus size={13} /> 添加 MCP server</button>
            {msg && <span className="admin-msg">{msg}</span>}
          </div>
          <div className="tbl-wrap">
          <table className="mcp-table">
            <thead><tr><th>名称</th><th>类型</th><th>命令 / URL</th><th>会话覆盖</th><th /></tr></thead>
            <tbody>
              {servers.map(s => (
                <tr key={s.name}>
                  <td>{s.name}{s.session_only && <span className="sk-src local">仅会话级</span>}</td>
                  <td><span className="sk-src local">{s.spec.type ?? 'stdio'}</span></td>
                  <td className="mono-cell">{s.spec.command
                    ? [s.spec.command, ...(s.spec.args ?? [])].join(' ')
                    : s.spec.url ?? '—'}</td>
                  <td>{s.sessions_overriding || '—'}</td>
                  <td>
                    <button className="link" onClick={() => setEditing(s)}>编辑</button>{' '}
                    <button className="link danger-link" onClick={() => void del(s)}>删除</button>
                  </td>
                </tr>
              ))}
              {servers.length === 0 && <tr><td colSpan={5} className="muted">未配置全局 MCP server</td></tr>}
            </tbody>
          </table>
          </div>
          <h4 className="admin-h4">内建工具开关 <span className="muted">（按角色禁用，写 registry.yaml，新会话生效）</span></h4>
          <div className="tbl-wrap">
          <table className="mcp-table">
            <thead><tr><th>角色</th>{builtin.map(t => <th key={t}>{t}</th>)}</tr></thead>
            <tbody>
              {profiles.map(p => (
                <tr key={p.name}>
                  <td>{p.name}</td>
                  {builtin.map(t => {
                    const off = p.disallowed_tools.includes(t);
                    return (
                      <td key={t}>
                        <button className={`chip ${off ? '' : 'on'}`}
                          onClick={() => void toggleTool(p, t)}>{off ? '已禁用' : '启用'}</button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </>
      )}
    </div>
  );
}

function McpForm({ init, onDone }: { init: McpServer | null; onDone: () => void }) {
  const spec = init?.spec ?? {};
  const [name, setName] = useState(init?.name ?? '');
  const [type, setType] = useState<string>(spec.type ?? 'stdio');
  const [command, setCommand] = useState(spec.command ?? '');
  const [args, setArgs] = useState((spec.args ?? []).join('\n'));
  const [url, setUrl] = useState(spec.url ?? '');
  const [envJson, setEnvJson] = useState(JSON.stringify(spec.env ?? {}, null, 2));
  const [err, setErr] = useState('');

  async function save() {
    let s: Record<string, any> = { type };
    try {
      if (type === 'stdio') {
        s = { type, command, args: args.split('\n').map((x: string) => x.trim()).filter(Boolean) };
        const env = JSON.parse(envJson || '{}');
        if (Object.keys(env).length) s.env = env;
      } else {
        s = { type, url };
        const headers = JSON.parse(envJson || '{}');
        if (Object.keys(headers).length) s.headers = headers;
      }
      await api(`/api/tools/mcp/${encodeURIComponent(name.trim())}`, {
        method: 'PUT', body: JSON.stringify({ spec: s }),
      });
      onDone();
    } catch (e) { setErr(String(e)); }
  }

  return (
    <div className="new-skill-form">
      <label>名称</label>
      <input value={name} onChange={e => setName(e.target.value)} placeholder="例：context7" disabled={!!init && !init.session_only} />
      <label>类型</label>
      <div className="skill-row">
        {['stdio', 'http', 'sse'].map(t => (
          <button key={t} className={`chip ${type === t ? 'on' : ''}`} onClick={() => setType(t)}>{t}</button>
        ))}
      </div>
      {type === 'stdio' ? <>
        <label>command</label>
        <input value={command} onChange={e => setCommand(e.target.value)} placeholder="npx / uvx / 绝对路径" />
        <label>args（每行一个）</label>
        <textarea value={args} onChange={e => setArgs(e.target.value)} rows={3} placeholder={'-y\n@mcp/context7-mcp'} />
        <label>env（JSON）</label>
        <textarea className="mono" value={envJson} onChange={e => setEnvJson(e.target.value)} rows={4}>{}</textarea>
      </> : <>
        <label>url</label>
        <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://mcp.example.com/sse" />
        <label>headers（JSON）</label>
        <textarea className="mono" value={envJson} onChange={e => setEnvJson(e.target.value)} rows={4}>{}</textarea>
      </>}
      <div className="modal-foot">
        <button className="btn ghost sm" onClick={onDone}>取消</button>
        <button className="btn primary sm" disabled={!name.trim()} onClick={() => void save()}>保存（全局生效 + 重写会话 .mcp.json）</button>
      </div>
      {err && <div className="admin-err">{err}</div>}
    </div>
  );
}
