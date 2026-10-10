// 属性面板（右面板第一 tab）：会话级配置与安全中心前移。
// 基本信息+行内改名 / 模型参数（会话级覆盖，下一轮生效）/ 安全与外联
// （临时授权·拒绝历史·一键放行）/ Skills 挂接 / 资源与 MCP 三态 / 历史详情。
// PP-11：全部分区可折叠（低频默认收起），开合态持久化 localStorage。
import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useStore } from '../stores/sessions';
import type { ParamsMap } from '../stores/sessions';
import { api, fmtTokens, fmtTime } from '../api/client';
import { ChevronDown, ChevronRight, Pencil } from './icons';
import { toast } from '../stores/toasts';

/** 模型参数六键（与后端 params.ALLOWED 对齐；kind 只影响输入控件） */
const PARAM_ROWS: { key: keyof ParamsMap | string; label: string;
                    kind: 'text' | 'select' | 'num'; hint?: string }[] = [
  { key: 'model', label: '模型', kind: 'text' },
  { key: 'effort', label: '推理力度', kind: 'select' },
  { key: 'max_turns', label: '轮次上限', kind: 'num', hint: '0=不限制' },
  { key: 'timeout_s', label: '超时（秒）', kind: 'num' },
  { key: 'stall_timeout_s', label: '静默超时（秒）', kind: 'num' },
  { key: 'rotate_input_tokens', label: '轮换阈值（input tokens）', kind: 'num', hint: '0=禁用轮换' },
];

/** model 输入的 datalist 常用项（可自由输入其他值） */
const MODEL_HINTS = ['claude-opus-4-8', 'claude-sonnet-4-6', 'glm-5.3'];

function fmtVal(k: string, v: unknown): string {
  if (v == null) {
    return k === 'max_turns' ? '不限制'
         : k === 'rotate_input_tokens' ? '禁用' : '未设';
  }
  return String(v);
}

/** PP-5：长标识一键复制（会话 ID/引擎会话/工作区——运维刚需，此前只能选中
 *  ellipsis 隐藏部分连全文都选不到） */
function copyText(text: string) {
  void navigator.clipboard.writeText(text).then(
    () => toast(`已复制 ${text.length > 20 ? text.slice(0, 20) + '…' : text}`),
    () => toast('复制失败（剪贴板不可用）', false));
}

/** ---------------------------------------------------------------- 分区折叠
 *  PP-11：属性分区可折叠（功能堆积后 320px 窄栏全是长页）——低频分区默认
 *  收起；用户点过的开合态写 localStorage（刷新/重开保持），未动过的分区
 *  跟随默认值（升级换默认不被旧值钉死，因为只存用户显式 toggle 过的键）。 */
const SEC_DEFAULT_COLLAPSED: Record<string, boolean> = {
  basic: false, params: false,          // 高频：身份 / 调参
  sandbox: true, egress: true,          // 低频：运维档位 / 安全事件流
  skills: true, mcp: true,              // 低频：挂载好后很少动
  legacy: true,                         // 历史明细（原 details 即默认收起）
};
const SECS_KEY = 'wd_props_collapsed';

function loadSecStates(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(SECS_KEY);
    if (raw) {
      const v = JSON.parse(raw);
      if (v && typeof v === 'object') return v as Record<string, boolean>;
    }
  } catch { /* 损坏值当未存过 */ }
  return {};
}

function PropSection({ secKey, title, sub, extra, children }: {
  secKey: keyof typeof SEC_DEFAULT_COLLAPSED | string;
  title: ReactNode; sub?: ReactNode; extra?: ReactNode; children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(
    () => loadSecStates()[secKey] ?? SEC_DEFAULT_COLLAPSED[secKey] ?? false);
  const toggle = () => setCollapsed(c => {
    const next = !c;
    try {   // 只记用户显式 toggle 过的键——默认值变更不被旧存值钉死
      localStorage.setItem(SECS_KEY, JSON.stringify({ ...loadSecStates(), [secKey]: next }));
    } catch { /* 隐私模式写不进就算了，本会话内仍生效 */ }
    return next;
  });
  return (
    <section className={`props-section collapsible ${collapsed ? 'collapsed' : ''}`}>
      <h4 className="props-sec-head" onClick={toggle}
          title={collapsed ? '展开该分区' : '折叠该分区'}>
        <span className="props-sec-chev">
          {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
        </span>
        {title}
        {sub}
        {extra && (
          <span className="props-sec-extra" onClick={e => e.stopPropagation()}>{extra}</span>
        )}
      </h4>
      {!collapsed && children}
    </section>
  );
}

export default function PropertiesPanel() {
  const sid = useStore(s => s.currentSid);
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid));
  if (!sid || !s) return null;
  return (
    <div className="props-tab">
      <BasicSection />
      <ParamsSection />
      <SandboxSection />
      <EgressSection />
      <SkillsSection />
      <McpSection />
      <LegacyDetails />
    </div>
  );
}


/** ---------------------------------------------------------------- 基本信息 */
function BasicSection() {
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid))!;
  const renameSession = useStore(st => st.renameSession);
  const defaultEngine = useStore(st => st.defaultEngine);
  const [editing, setEditing] = useState(false);
  const skills: string[] = s.skills_json ? JSON.parse(s.skills_json) : [];
  const engine = s.engine_override
    ? `${s.engine_override}（会话覆盖）` : (s.engine ?? defaultEngine);
  return (
    <PropSection secKey="basic" title="基本信息">
      {editing ? (
        <input className="row-edit" defaultValue={s.title} autoFocus maxLength={80}
          onKeyDown={e => {
            const v = (e.target as HTMLInputElement).value;
            if (e.key === 'Enter') { if (v.trim()) void renameSession(s.id, v.trim().slice(0, 80)); setEditing(false); }
            else if (e.key === 'Escape') setEditing(false);
          }}
          onBlur={e => {
            const v = e.target.value.trim();
            if (v && v !== s.title) void renameSession(s.id, v.slice(0, 80));
            setEditing(false);
          }} />
      ) : (
        <div className="kv">
          <span>标题</span>
          <code title="点击铅笔改名">{s.title}
            <button className="mini-btn" style={{ marginLeft: 6 }}
              onClick={() => setEditing(true)}><Pencil size={11} /></button>
          </code>
        </div>
      )}
      <div className="kv"><span>会话 ID</span>
        <code className="copyable" title="点击复制全文" onClick={() => copyText(s.id)}>{s.id}</code></div>
      <div className="kv"><span>引擎</span><code>{engine}</code></div>
      <div className="kv"><span>引擎会话</span>
        <code className="copyable" title="点击复制全文" onClick={() => copyText(s.claude_session_id ?? '-')}
          >{s.claude_session_id ? `${s.claude_session_id.slice(0, 13)}…` : '-'}</code></div>
      <div className="kv"><span>角色</span>
        <code>{s.profile}{s.profile_auto ? ' ✨自动' : ''}</code></div>
      <div className="kv"><span>skills</span>
        <code>{skills.join(', ') || '-'}</code></div>
      {s.workspace && <div className="kv"><span>工作区</span>
        <code className="copyable" title="点击复制全文" onClick={() => copyText(s.workspace!)}>{s.workspace}</code></div>}
      <div className="kv"><span>tokens</span>
        <code>in {fmtTokens(s.usage?.in)} / out {fmtTokens(s.usage?.out)}</code></div>
      <div className="kv"><span>创建</span><code>{fmtTime(s.created_at)}</code></div>
    </PropSection>
  );
}


/** ---------------------------------------------------------------- 模型参数 */
function ParamsSection() {
  const sid = useStore(s => s.currentSid)!;
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid))!;
  const extras = useStore(st => st.sessionExtras);
  const patchParams = useStore(st => st.patchParams);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [base, setBase] = useState<Record<string, string>>({});   // PP-6：服务端真相快照（脏判定基准）
  const [busy, setBusy] = useState(false);

  // extras 变化（打开会话 / PATCH 后重拉）→ 草稿回服务端真相
  useEffect(() => {
    const p: ParamsMap = extras?.params ?? {};
    const d: Record<string, string> = {};
    for (const r of PARAM_ROWS) d[r.key] = p[r.key] != null ? String(p[r.key]) : '';
    setDraft(d); setBase(d);
  }, [sid, extras]);

  /** 草稿 → 提交体（skip 键 = 跟随 profile；skipKey 供「恢复默认」单键清除） */
  const build = (skipKey?: string): ParamsMap => {
    const out: ParamsMap = {};
    for (const r of PARAM_ROWS) {
      if (r.key === skipKey) continue;
      const raw = (draft[r.key] ?? '').trim();
      if (!raw) continue;
      out[r.key] = r.kind === 'num' ? Number(raw) : raw;
    }
    return out;
  };
  const commit = async (skipKey?: string) => {
    setBusy(true);
    try {
      await patchParams(sid, build(skipKey));
      toast(skipKey ? '已恢复默认（跟随 profile）' : '参数已保存 · 下一轮生效');   // PP-6：不再静默成功
    }
    catch (e) { toast(`参数保存失败：${e instanceof Error ? e.message : e}`, false); }
    finally { setBusy(false); }
  };

  // PP-6：脏判定（与保存后重拉的 base 比对，非与初始 profile 比）
  const dirty = PARAM_ROWS.some(r => (draft[r.key] ?? '') !== (base[r.key] ?? ''));

  const prof = extras?.params_profile ?? {};
  const eng = s.engine_override || s.engine || '';
  return (
    <PropSection secKey="params" title="模型参数"
      sub={<span className="muted" style={{ fontWeight: 400 }}>（覆盖本会话，其余跟随 profile）</span>}>
      {PARAM_ROWS.map(r => {
        const has = (draft[r.key] ?? '').trim().length > 0;   // PP-1：恢复键仅在有覆盖值时渲染（无值=本就跟随，无恢复语义）
        return (
        <div key={r.key} className="setting-row props-param">
          <span className="props-k" title={r.hint}>{r.label}</span>
          {r.kind === 'select' ? (
            <select value={draft[r.key] ?? ''}
              onChange={e => setDraft(d => ({ ...d, [r.key]: e.target.value }))}>
              <option value="">跟随（{fmtVal(r.key, prof[r.key])}）</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          ) : (
            <input
              type={r.kind === 'num' ? 'number' : 'text'}
              list={r.key === 'model' ? 'props-model-hints' : undefined}
              value={draft[r.key] ?? ''}
              placeholder={`跟随（${fmtVal(r.key, prof[r.key])}）`}
              onChange={e => setDraft(d => ({ ...d, [r.key]: e.target.value }))} />
          )}
          {r.key === 'model' && (
            <datalist id="props-model-hints">
              {MODEL_HINTS.map(m => <option key={m} value={m} />)}
            </datalist>
          )}
          {has && (
            <button className="mini-btn" disabled={busy}
              title={`清除本项覆盖（跟随 profile）${r.hint ? `；${r.hint}` : ''}`}
              onClick={() => void commit(r.key)}>↺</button>
          )}
        </div>
        );
      })}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className={dirty ? 'btn primary' : 'btn ghost'} disabled={busy || !dirty}
          onClick={() => void commit()}>
          {busy ? '保存中…' : '保存参数'}
        </button>
        {dirty && <span className="chip warn" title="有未保存的改动（切会话/重拉即丢）">● 未保存</span>}
        {!dirty && <span className="muted" style={{ fontSize: 'var(--fs-sm)' }}>下一轮生效</span>}
      </div>
      {eng === 'opencode' && (
        <div className="muted" style={{ fontSize: 'var(--fs-sm)', marginTop: 4 }}>
          opencode 引擎不支持轮次上限（max_turns 将被忽略）
        </div>
      )}
    </PropSection>
  );
}


/** ---------------------------------------------------------------- 安全与外联 */
interface EgressView {
  mode: string;
  effective?: string;
  override?: string | null;
  on_deny?: string;
  ask_wait_s?: number;
  allow: string[];
  grants: { host: string; expires_at: string }[];
  events: { ts: string; host: string; decision: string }[];
}

/** 任务级沙箱档位（params.sandbox；「跟随全局」=清除覆盖）——
 *  宿主运维/跨项目接管会话放开隔离（off=直跑），普通任务保持全局默认。 */
const SANDBOX_TIERS_UI: { v: string | null; label: string; tip: string }[] = [
  { v: null, label: '跟随全局', tip: '清除本任务覆盖，用全局沙箱档位' },
  { v: 'off', label: '直跑', tip: '本任务不隔离（宿主运维/跨仓接管用；下一 turn 生效）' },
  { v: 'bwrap', label: '隔离', tip: '本任务强制 bwrap（全局放开时收紧单任务）' },
];

function SandboxSection() {
  const sid = useStore(s => s.currentSid)!;
  const extras = useStore(st => st.sessionExtras);
  const patchParams = useStore(st => st.patchParams);
  const [busy, setBusy] = useState(false);
  const cur = (extras?.params?.sandbox as string | undefined) ?? null;

  const setTier = (v: string | null) => {
    const merged: ParamsMap = { ...(extras?.params ?? {}) };
    if (v == null) delete merged.sandbox; else merged.sandbox = v;
    setBusy(true);
    void patchParams(sid, merged).catch(() => { /* patchParams 已 alert */ })
      .finally(() => setBusy(false));
  };

  return (
    <PropSection secKey="sandbox" title="沙箱档位"
      sub={<span className="muted" style={{ fontWeight: 400 }}>（下一 turn 生效）</span>}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {SANDBOX_TIERS_UI.map(t =>
          <button key={t.label} className={`chip ${cur === t.v ? 'on' : ''}`}
            disabled={busy} title={t.tip} onClick={() => setTier(t.v)}>{t.label}</button>)}
      </div>
      <div className="muted" style={{ fontSize: 'var(--fs-md)', marginTop: 6 }}>
        直跑=本任务可访问宿主全机（仍过权限引擎/审计）；配合资源桥接用。
      </div>
    </PropSection>
  );
}

/** 任务级外联档位（后端 params.EGRESS_MODES；「跟随全局」=清除覆盖） */
const EGRESS_TIERS: { v: string | null; label: string; tip: string }[] = [
  { v: null, label: '跟随全局', tip: '清除本任务覆盖，用全局出口模式' },
  { v: 'off', label: '放开', tip: '本任务不拦外联（仍走代理审计）' },
  { v: 'warn', label: '告警', tip: '本任务放行外联但逐条告警' },
  { v: 'enforce', label: '强制', tip: '本任务按白名单拦截' },
];

function EgressSection() {
  const sid = useStore(s => s.currentSid)!;
  const tick = useStore(s => s.egressTick);
  const extras = useStore(st => st.sessionExtras);
  const patchParams = useStore(st => st.patchParams);
  const [data, setData] = useState<EgressView | null>(null);
  const [busy, setBusy] = useState('');

  const load = useCallback(() => {
    void api<EgressView>(`/api/sessions/${encodeURIComponent(sid)}/egress?n=100`)
      .then(setData).catch(() => { /* 面板保持旧值 */ });
  }, [sid]);
  // SSE egress 事件（payload 带 sid）驱动刷新 + 手动刷新；不轮询
  useEffect(load, [load, tick]);

  /** 任务档位 chips：与既有模型参数覆盖合并提交（PATCH params 整体替换语义） */
  const setTier = (v: string | null) => {
    const merged: ParamsMap = { ...(extras?.params ?? {}) };
    if (v == null) delete merged.egress; else merged.egress = v;
    setBusy('tier');
    void patchParams(sid, merged)
      .then(load)
      .catch(() => { /* patchParams 已 alert */ })
      .finally(() => setBusy(''));
  };

  const allowHost = async (host: string) => {
    setBusy(host);
    try {
      await api<{ allow: string[] }>('/api/admin/egress/allow', {
        method: 'POST', body: JSON.stringify({ host }),
      });
      toast(`已放行 ${host}（热生效）`);
      load();
    } catch (e) {   // PP-7：吞错修复（普通用户 403/网络错此前无声失败）
      toast(`放行失败：${e instanceof Error ? e.message : e}`, false);
      setBusy('');
    }
  };
  const revokeGrant = async (host: string) => {
    setBusy(host);
    try {
      await api('/api/admin/egress/grant/revoke', {
        method: 'POST', body: JSON.stringify({ sid, host }),
      });
      toast(`已收回 ${host} 的临时授权`);
      load();
    } catch (e) {   // PP-10：同款吞错修复
      toast(`收回失败：${e instanceof Error ? e.message : e}`, false);
      setBusy('');
    }
  };

  // 按域聚合（承袭管理面 EgressDetail 的提取法）
  const rows: { host: string; n: number; denied: number; last: string }[] = [];
  if (data) {
    const m = new Map<string, { n: number; denied: number; last: string }>();
    for (const e of data.events) {
      if (e.host === 'llm-gw.internal') continue;
      const cur = m.get(e.host) ?? { n: 0, denied: 0, last: '' };
      cur.n++; if (!String(e.decision).startsWith('allow')) cur.denied++;
      cur.last = e.ts; m.set(e.host, cur);
    }
    rows.push(...[...m.entries()].sort((a, b) => b[1].n - a[1].n).map(([host, v]) => ({ host, ...v })));
  }
  const mode = data?.mode ?? '-';
  const allow = data?.allow ?? [];
  return (
    <PropSection secKey="egress" title="安全与外联"
      extra={<button className="mini-btn" onClick={load}>刷新</button>}>
      <div className="setting-row" style={{ marginBottom: 8 }}>
        <span style={{ minWidth: 92 }}>任务外联档位</span>
        {EGRESS_TIERS.map(t => {
          const on = (data?.override ?? null) === t.v;
          return (
            <button key={t.label} className={`chip${on ? '' : ' off'}`}
              style={{ cursor: 'pointer' }} disabled={busy === 'tier'}
              title={t.tip} onClick={() => void setTier(t.v)}>{t.label}</button>
          );
        })}
      </div>
      <div className="muted" style={{ fontSize: 'var(--fs-md)', marginBottom: 6 }}>
        出口模式 <b style={{ color: (data?.effective ?? mode) === 'enforce' ? 'var(--danger)' : 'var(--green)' }}>
          {data?.effective ?? mode}</b>
        {data?.override ? '（本任务覆盖）' : `（全局 ${mode}）`}
        {data?.effective === 'enforce' && data.on_deny === 'ask'
          ? ` · 白名单外弹卡确认（等待 ${data.ask_wait_s ?? 120}s）` : ''}
        {' '}· 白名单 {allow.length} 域
      </div>
      {(data?.grants ?? []).length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div className="muted" style={{ fontSize: 'var(--fs-md)', marginBottom: 4 }}>
            本会话临时授权（到期自动收回）：
          </div>
          {data!.grants.map(g => (
            <span key={g.host} className="chip" style={{ marginRight: 6 }}>
              {g.host} · 至 {g.expires_at.slice(5, 16).replace('T', ' ')}
              <a style={{ marginLeft: 4, cursor: 'pointer', opacity: 0.7 }}
                 onClick={() => void revokeGrant(g.host)} title="立即收回">×</a>
            </span>
          ))}
        </div>
      )}
      {/* PP-2：五列表 → 行卡（域名+操作首行 / 统计+判定+时刻次行）——320px 窄容器
          塞不下五列，原表格字号上调后更是挤压重叠 */}
      {rows.map(r => (
        <div key={r.host} className="eg-row">
          <div className="eg-row-top">
            <span className="eg-host" title={r.host}>{r.host}</span>
            {allow.includes(r.host)
              && <span className="chip on" title="在全局白名单，外发即放行">已放行</span>}
            {r.denied > 0 && !allow.includes(r.host) && (
              <button className="mini-btn" disabled={busy === r.host}
                title="加入全局出口白名单（热生效，免重启）"
                onClick={() => void allowHost(r.host)}>放行</button>
            )}
          </div>
          <div className="eg-meta">
            <span>{r.n} 次{r.denied ? ` · 拒绝 ${r.denied}` : ''}</span>
            <span style={{ color: r.denied ? 'var(--danger)' : 'var(--green)' }}>
              {r.denied ? '拦截中' : '放行'}</span>
            <span className="mono" style={{ marginLeft: 'auto' }}>
              {(r.last || '').slice(5, 16).replace('T', ' ')}</span>
          </div>
        </div>
      ))}
      {rows.length === 0 && (
        <div className="panel-empty">（暂无本会话的外联记录）</div>
      )}
      <div className="muted" style={{ fontSize: 'var(--fs-sm)', marginTop: 6 }}>
        拒绝历史自本版本起记录会话归属（更早的事件无归属，不在此列）·
        <a className="link" onClick={() => { location.hash = '#/admin/security'; }}>安全中心看全部</a>
      </div>
    </PropSection>
  );
}


/** ---------------------------------------------------------------- Skills 挂接 */
function SkillsSection() {
  const sid = useStore(s => s.currentSid)!;
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid))!;
  const extras = useStore(st => st.sessionExtras);
  const patchSkills = useStore(st => st.patchSkills);
  const all = extras?.skills_available ?? [];
  const on: string[] = s.skills_json ? JSON.parse(s.skills_json) : [];
  const desc = (n: string) => useStore.getState().skills.find(x => x.name === n)?.description;
  const toggle = (n: string) => {
    const next = on.includes(n) ? on.filter(x => x !== n) : [...on, n];
    void patchSkills(sid, next).catch(e =>
      toast(`skills 更新失败：${e instanceof Error ? e.message : e}`, false));
  };
  return (
    <PropSection secKey="skills" title="Skills"
      sub={<span className="muted" style={{ fontWeight: 400 }}>（点击挂载/卸载，下一轮生效）</span>}>
      {all.length === 0 && <div className="muted" style={{ fontSize: 'var(--fs-md)' }}>（无可用 skill）</div>}
      <div className="chips">
        {all.map(n => (
          <button key={n} className={`chip ${on.includes(n) ? 'on' : 'off'}`}
            title={desc(n) ?? n} onClick={() => toggle(n)}>{n}</button>
        ))}
      </div>
    </PropSection>
  );
}


/** ---------------------------------------------------------------- 资源与 MCP */
interface ToolServer { name: string; spec: Record<string, unknown>;
                       sessions_overriding: number; session_only?: boolean }

function McpSection() {
  const sid = useStore(s => s.currentSid)!;
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid))!;
  const patchMcp = useStore(st => st.patchMcp);
  const [servers, setServers] = useState<ToolServer[]>([]);
  useEffect(() => {
    setServers([]);
    void api<{ servers: ToolServer[] }>('/api/tools')
      .then(d => setServers(d.servers ?? [])).catch(() => { });
  }, [sid]);

  const sessMcp: Record<string, unknown> = s.mcp_json ? JSON.parse(s.mcp_json) : {};
  const put = (next: Record<string, unknown>) =>
    void patchMcp(sid, next).catch(e =>
      toast(`MCP 更新失败：${e instanceof Error ? e.message : e}`, false));

  const globals = servers.filter(x => !x.session_only);
  const extrasOnly = Object.keys(sessMcp).filter(k => !servers.some(x => x.name === k));
  return (
    <PropSection secKey="mcp" title="资源与 MCP"
      sub={<span className="muted" style={{ fontWeight: 400 }}>（三态：全局启用 / 本会话禁用）</span>}>
      {globals.length === 0 && extrasOnly.length === 0 && (
        <div className="muted" style={{ fontSize: 'var(--fs-md)' }}>（未配置全局 MCP server）</div>
      )}
      {globals.map(srv => {
        const off = sessMcp[srv.name] === false;   // 哨兵：本会话禁用
        return (
          <div key={srv.name} className="setting-row">
            <span className={`chip ${off ? 'off' : 'on'}`}
              style={{ cursor: 'default' }}>{srv.name}</span>
            <span className="muted" style={{ fontSize: 'var(--fs-sm)' }}>
              {off ? '本会话已禁用' : '全局启用'}</span>
            <button className="mini-btn" style={{ marginLeft: 'auto' }}
              onClick={() => {
                const rest = { ...sessMcp };
                if (off) delete rest[srv.name];
                else rest[srv.name] = false;
                put(rest);
              }}>{off ? '启用' : '禁用'}</button>
          </div>
        );
      })}
      {extrasOnly.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div className="muted" style={{ fontSize: 'var(--fs-md)', marginBottom: 4 }}>
            本会话额外挂载（非全局）：
          </div>
          {extrasOnly.map(n => (
            <span key={n} className="chip on" style={{ marginRight: 6 }}>
              {n}
              <a style={{ marginLeft: 4, cursor: 'pointer', opacity: 0.7 }}
                 title="移除" onClick={() => {
                   const rest = { ...sessMcp };
                   delete rest[n];
                   put(rest);
                 }}>×</a>
            </span>
          ))}
        </div>
      )}
      <div className="muted" style={{ fontSize: 'var(--fs-sm)', marginTop: 6 }}>
        <a className="link" onClick={() => { location.hash = '#/admin/tools'; }}>管理全局 MCP</a>
        {' · '}
        <a className="link" onClick={() => { location.hash = '#/admin/resources'; }}>资源中心</a>
      </div>
    </PropSection>
  );
}


/** ---------------------------------------------------------------- 历史详情（原详情 tab） */
function LegacyDetails() {
  const turns = useStore(s => s.turns);
  return (
    <PropSection secKey="legacy" title="历史详情（turns 明细）">
      <table className="turns-table">
        <thead><tr><th>#</th><th>状态</th><th>时长</th><th>费用</th><th>开始</th></tr></thead>
        <tbody>
          {turns.map(t => (
            <tr key={t.id} className={`ts-${t.status}`}>
              <td>#{t.id}</td>
              <td>{t.status}</td>
              <td>{t.duration_s != null ? `${Math.round(t.duration_s)}s` : ''}</td>
              <td>{t.cost_usd != null ? `$${t.cost_usd.toFixed(3)}` : ''}</td>
              <td>{fmtTime(t.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </PropSection>
  );
}
