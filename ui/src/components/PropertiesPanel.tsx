// 属性面板（右面板第一 tab）：会话级配置与安全中心前移。
// 基本信息+行内改名 / 模型参数（会话级覆盖，下一轮生效）/ 安全与外联
// （临时授权·拒绝历史·一键放行）/ Skills 挂接 / 资源与 MCP 三态 / 历史详情折叠。
import { useCallback, useEffect, useState } from 'react';
import { useStore } from '../stores/sessions';
import type { ParamsMap } from '../stores/sessions';
import { api, fmtTokens, fmtTime } from '../api/client';
import { Pencil } from './icons';

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
    <section className="props-section">
      <h4>基本信息</h4>
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
      <div className="kv"><span>会话 ID</span><code>{s.id}</code></div>
      <div className="kv"><span>引擎</span><code>{engine}</code></div>
      <div className="kv"><span>引擎会话</span>
        <code>{s.claude_session_id ? `${s.claude_session_id.slice(0, 13)}…` : '-'}</code></div>
      <div className="kv"><span>角色</span>
        <code>{s.profile}{s.profile_auto ? ' ✨自动' : ''}</code></div>
      <div className="kv"><span>skills</span>
        <code>{skills.join(', ') || '-'}</code></div>
      {s.workspace && <div className="kv"><span>工作区</span><code>{s.workspace}</code></div>}
      <div className="kv"><span>tokens</span>
        <code>in {fmtTokens(s.usage?.in)} / out {fmtTokens(s.usage?.out)}</code></div>
      <div className="kv"><span>创建</span><code>{fmtTime(s.created_at)}</code></div>
    </section>
  );
}


/** ---------------------------------------------------------------- 模型参数 */
function ParamsSection() {
  const sid = useStore(s => s.currentSid)!;
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid))!;
  const extras = useStore(st => st.sessionExtras);
  const patchParams = useStore(st => st.patchParams);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  // extras 变化（打开会话 / PATCH 后重拉）→ 草稿回服务端真相
  useEffect(() => {
    const p: ParamsMap = extras?.params ?? {};
    const d: Record<string, string> = {};
    for (const r of PARAM_ROWS) d[r.key] = p[r.key] != null ? String(p[r.key]) : '';
    setDraft(d);
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
    try { await patchParams(sid, build(skipKey)); }
    catch (e) { alert(`参数保存失败：${e instanceof Error ? e.message : e}`); }
    finally { setBusy(false); }
  };

  const prof = extras?.params_profile ?? {};
  const eng = s.engine_override || s.engine || '';
  return (
    <section className="props-section">
      <h4>模型参数
        <span className="muted" style={{ fontWeight: 400 }}>（覆盖本会话，其余跟随 profile）</span>
      </h4>
      {PARAM_ROWS.map(r => (
        <div key={r.key} className="setting-row">
          <span style={{ minWidth: 92 }}>{r.label}</span>
          {r.kind === 'select' ? (
            <select value={draft[r.key] ?? ''} className="props-num"
              onChange={e => setDraft(d => ({ ...d, [r.key]: e.target.value }))}>
              <option value="">跟随 profile（{fmtVal(r.key, prof[r.key])}）</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          ) : (
            <input className={r.kind === 'num' ? 'props-num' : ''}
              type={r.kind === 'num' ? 'number' : 'text'}
              list={r.key === 'model' ? 'props-model-hints' : undefined}
              value={draft[r.key] ?? ''}
              placeholder={`跟随 profile（${fmtVal(r.key, prof[r.key])}）`}
              onChange={e => setDraft(d => ({ ...d, [r.key]: e.target.value }))} />
          )}
          {r.key === 'model' && (
            <datalist id="props-model-hints">
              {MODEL_HINTS.map(m => <option key={m} value={m} />)}
            </datalist>
          )}
          {r.hint && <span className="muted" style={{ fontSize: 11 }}>{r.hint}</span>}
          <button className="mini-btn" disabled={busy || !(draft[r.key] ?? '').trim()}
            title="清除本项覆盖（跟随 profile）"
            onClick={() => void commit(r.key)}>恢复默认</button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn ghost" disabled={busy} onClick={() => void commit()}>
          {busy ? '保存中…' : '保存参数'}
        </button>
        <span className="muted" style={{ fontSize: 11 }}>下一轮生效（进行中 turn 不受影响）</span>
      </div>
      {eng === 'opencode' && (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          opencode 引擎不支持轮次上限（max_turns 将被忽略）
        </div>
      )}
    </section>
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
    <section className="props-section">
      <h3>沙箱档位 <span className="muted">（下一 turn 生效）</span></h3>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {SANDBOX_TIERS_UI.map(t =>
          <button key={t.label} className={`chip ${cur === t.v ? 'on' : ''}`}
            disabled={busy} title={t.tip} onClick={() => setTier(t.v)}>{t.label}</button>)}
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
        直跑=本任务可访问宿主全机（仍过权限引擎/审计）；配合资源桥接用。
      </div>
    </section>
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

  const allowHost = (host: string) => {
    setBusy(host);
    void api<{ allow: string[] }>('/api/admin/egress/allow', {
      method: 'POST', body: JSON.stringify({ host }),
    }).then(load).catch(() => setBusy(''));
  };
  const revokeGrant = (host: string) => {
    setBusy(host);
    void api('/api/admin/egress/grant/revoke', {
      method: 'POST', body: JSON.stringify({ sid, host }),
    }).then(load).catch(() => setBusy(''));
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
    <section className="props-section">
      <h4>安全与外联
        <button className="mini-btn" style={{ marginLeft: 'auto' }}
          onClick={load}>刷新</button>
      </h4>
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
      <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
        出口模式 <b style={{ color: (data?.effective ?? mode) === 'enforce' ? 'var(--danger)' : 'var(--green)' }}>
          {data?.effective ?? mode}</b>
        {data?.override ? '（本任务覆盖）' : `（全局 ${mode}）`}
        {data?.effective === 'enforce' && data.on_deny === 'ask'
          ? ` · 白名单外弹卡确认（等待 ${data.ask_wait_s ?? 120}s）` : ''}
        {' '}· 白名单 {allow.length} 域
      </div>
      {(data?.grants ?? []).length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
            本会话临时授权（到期自动收回）：
          </div>
          {data!.grants.map(g => (
            <span key={g.host} className="chip" style={{ marginRight: 6 }}>
              {g.host} · 至 {g.expires_at.slice(11, 16)}
              <a style={{ marginLeft: 4, cursor: 'pointer', opacity: 0.7 }}
                 onClick={() => revokeGrant(g.host)} title="立即收回">×</a>
            </span>
          ))}
        </div>
      )}
      <table className="kv-table" style={{ width: '100%' }}>
        <thead><tr><th>目标域</th><th style={{ width: 50 }}>次数</th>
          <th style={{ width: 110 }}>判定</th><th style={{ width: 70 }}>最近</th></tr></thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={4} className="muted"
              style={{ textAlign: 'center', padding: 10 }}>
              （暂无本会话的外联记录）</td></tr>
          )}
          {rows.map(r => (
            <tr key={r.host}>
              <td>{r.host}{allow.includes(r.host)
                ? <span className="muted" style={{ fontSize: 11 }}>（已放行）</span> : null}</td>
              <td style={{ fontVariantNumeric: 'tabular-nums' }}>{r.n}</td>
              <td style={{ color: r.denied ? 'var(--danger)' : 'var(--green)' }}>
                {r.denied ? `拒绝 ${r.denied}/${r.n}` : '放行'}</td>
              <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(r.last || '').slice(11, 19)}</td>
              {r.denied > 0 && !allow.includes(r.host) && (
                <td style={{ width: 60 }}>
                  <button className="mini-btn" disabled={busy === r.host}
                    title="加入出口白名单（热生效，免重启）"
                    onClick={() => allowHost(r.host)}>放行</button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
        拒绝历史自本版本起记录会话归属（更早的事件无归属，不在此列）·
        <a className="link" onClick={() => { location.hash = '#/admin/security'; }}>安全中心看全部</a>
      </div>
    </section>
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
      alert(`skills 更新失败：${e instanceof Error ? e.message : e}`));
  };
  return (
    <section className="props-section">
      <h4>Skills
        <span className="muted" style={{ fontWeight: 400 }}>（点击挂载/卸载，下一轮生效）</span>
      </h4>
      {all.length === 0 && <div className="muted" style={{ fontSize: 12 }}>（无可用 skill）</div>}
      <div className="chips">
        {all.map(n => (
          <button key={n} className={`chip ${on.includes(n) ? 'on' : 'off'}`}
            title={desc(n) ?? n} onClick={() => toggle(n)}>{n}</button>
        ))}
      </div>
    </section>
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
      alert(`MCP 更新失败：${e instanceof Error ? e.message : e}`));

  const globals = servers.filter(x => !x.session_only);
  const extrasOnly = Object.keys(sessMcp).filter(k => !servers.some(x => x.name === k));
  return (
    <section className="props-section">
      <h4>资源与 MCP
        <span className="muted" style={{ fontWeight: 400 }}>（三态：全局启用 / 本会话禁用）</span>
      </h4>
      {globals.length === 0 && extrasOnly.length === 0 && (
        <div className="muted" style={{ fontSize: 12 }}>（未配置全局 MCP server）</div>
      )}
      {globals.map(srv => {
        const off = sessMcp[srv.name] === false;   // 哨兵：本会话禁用
        return (
          <div key={srv.name} className="setting-row">
            <span className={`chip ${off ? 'off' : 'on'}`}
              style={{ cursor: 'default' }}>{srv.name}</span>
            <span className="muted" style={{ fontSize: 11 }}>
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
          <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
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
      <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
        <a className="link" onClick={() => { location.hash = '#/admin/tools'; }}>管理全局 MCP</a>
        {' · '}
        <a className="link" onClick={() => { location.hash = '#/admin/resources'; }}>资源中心</a>
      </div>
    </section>
  );
}


/** ---------------------------------------------------------------- 历史详情（原详情 tab） */
function LegacyDetails() {
  const turns = useStore(s => s.turns);
  return (
    <details className="props-section">
      <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--text-dim)' }}>
        历史详情（turns 明细）</summary>
      <table className="turns-table">
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
    </details>
  );
}
