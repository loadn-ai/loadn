// 平台设置页（引擎/通知/分享/价目/外观主题）——从 AdminPanel 拆出（v0.6.12）
import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import { useStore } from '../../stores/sessions';
import { useAdminDirty } from '../../stores/adminDirty';
import { Sun, Moon } from '../icons';
import UsersCard from './UsersCard';

/* ================= 平台设置 ================= */
interface TitleGenCfg {
  enabled: boolean; api_base: string; model: string;
  api_key_set: boolean; api_key_hint: string;
}
interface PerEngineCfg { bin: string; model: string; provider: string; enabled: boolean; extra_args: string[]; override?: boolean }
interface EnginesCfg {
  default: string; available: string[]; opencode_provider: string;
  no_compact: boolean | null; per: Record<string, PerEngineCfg>;
}
interface ClaudeCfg { effort: string; model: string; claude_bin: string }
interface NotifyCfg {
  provider: string; bark_url: string; telegram_chat_id: string;
  events: Record<string, boolean>;
  serverchan_key_set: boolean; telegram_bot_token_set: boolean;
}
interface ShareCfg { base_url: string }
interface PricingCfg {
  usd_cny: number; api: Record<string, Record<string, number>>;
  plan_credits: Record<string, Record<string, number>>;
  builtin: { api: Record<string, Record<string, number>>;
             plan_credits: Record<string, Record<string, number>> };
}
interface ServerCfg {
  host: string; port: number; token_set: boolean;
  admin_token_set: boolean; token_grace_until: number;
}
interface ResCfg {
  ocr_url: string; sandbox_url: string; cdp_url: string; proxy: string;
  sms_url: string; sms_phone: string; vlm_api_base: string; vlm_model: string; adb_addr: string;
  mail_imap: string; mail_smtp: string; mail_user: string;
  zhipu_engine: string; textr_email: string;
  sandbox_api_key_set: boolean; sandbox_api_key_hint: string;
  sms_token_set: boolean; sms_token_hint: string;
  mail_auth_code_set: boolean; mail_auth_code_hint: string;
  vlm_api_key_set: boolean; vlm_api_key_hint: string;
  twocaptcha_key_set: boolean; twocaptcha_key_hint: string;
  bocha_key_set: boolean; bocha_key_hint: string;
  zhipu_key_set: boolean; zhipu_key_hint: string;
  textr_password_set: boolean; textr_password_hint: string;
}

interface ConvRow {
  name: string; timeout_s: number; stall_timeout_s: number; max_turns: number | null;
}
interface ConvDefaults { timeout_s: number; stall_timeout_s: number; max_turns: number | null }

/** 价目表结构化行（字符串态供输入编辑；err=行级校验文案，保存时填充） */
interface PriceRow { model: string; input: string; cache: string; output: string; err?: string }

const rowsFromTable = (t: Record<string, Record<string, number>> | undefined,
                       cacheKey: string): PriceRow[] =>
  Object.entries(t ?? {}).map(([m, p]) => ({
    model: m, input: String(p.input ?? ''), cache: String(p[cacheKey] ?? ''),
    output: String(p.output ?? ''),
  }));


export default function SettingsTab() {
  const [tg, setTg] = useState<TitleGenCfg | null>(null);
  const [keyInput, setKeyInput] = useState('');
  const [run, setRun] = useState<{ max_concurrent_turns: number; replay_max_events: number;
                                   events_retain_days: number } | null>(null);
  const [conv, setConv] = useState<ConvRow[] | null>(null);
  const [convDef, setConvDef] = useState<ConvDefaults>(
    { timeout_s: 3600, stall_timeout_s: 1800, max_turns: null });
  const [convMsg, setConvMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [eng, setEng] = useState<EnginesCfg | null>(null);
  const [cl, setCl] = useState<ClaudeCfg | null>(null);
  const [engMsg, setEngMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [nf, setNf] = useState<NotifyCfg | null>(null);
  const [nfKeys, setNfKeys] = useState<Record<string, string>>({});
  const [nfMsg, setNfMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [nfTesting, setNfTesting] = useState(false);
  const [share, setShare] = useState<ShareCfg | null>(null);
  const [shareMsg, setShareMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [pricing, setPricing] = useState<PricingCfg | null>(null);
  const [apiRows, setApiRows] = useState<PriceRow[]>([]);
  const [planRows, setPlanRows] = useState<PriceRow[]>([]);
  const [priceMsg, setPriceMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [srv, setSrv] = useState<ServerCfg | null>(null);
  const [res, setRes] = useState<ResCfg | null>(null);
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [loadErr, setLoadErr] = useState('');
  const [testing, setTesting] = useState(false);

  // AC-5.2 脏状态保护：任意输入事件（容器 capture，覆盖全部子卡控件）记
  // lastEdit，任一保存成功 bump lastSaved——dirty = lastEdit > lastSaved。
  // 派生上报 adminDirty store，AdminPanel 切 tab 前统一拦截确认。
  const rootRef = useRef<HTMLDivElement>(null);
  const [lastEdit, setLastEdit] = useState(0);
  const [lastSaved, setLastSaved] = useState(0);
  const dirty = lastEdit > lastSaved;
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const onInput = () => setLastEdit(Date.now());
    el.addEventListener('input', onInput, true);
    return () => el.removeEventListener('input', onInput, true);
  }, []);
  useEffect(() => {
    useAdminDirty.getState().setDirty('settings', dirty);
    return () => useAdminDirty.getState().setDirty('settings', false);
  }, [dirty]);
  const bumpSaved = () => setLastSaved(Date.now());

  useEffect(() => { void reload(); }, []);
  async function reload() {
    try {
      const d = await api<{ titlegen: TitleGenCfg; run: NonNullable<typeof run>;
                            convergence: ConvRow[]; convergence_defaults: ConvDefaults;
                            resources: ResCfg;
                            engines: EnginesCfg; claude: ClaudeCfg; notify: NotifyCfg;
                            share: ShareCfg; pricing: PricingCfg; server: ServerCfg }>('/api/settings');
      setTg(d.titlegen); setRun(d.run); setConv(d.convergence); setRes(d.resources); setKeyInput('');
      setConvDef(d.convergence_defaults ?? { timeout_s: 3600, stall_timeout_s: 1800, max_turns: null });
      setEng(d.engines); setCl(d.claude); setNf(d.notify); setNfKeys({});
      setShare(d.share); setPricing(d.pricing);
      setApiRows(rowsFromTable(d.pricing.api, 'cache_read'));
      setPlanRows(rowsFromTable(d.pricing.plan_credits, 'cache_input'));
      setSrv(d.server);
      setLoadErr('');
    } catch (e) { setLoadErr(`设置加载失败：${e instanceof Error ? e.message : e}`); }  // 早退分支必须区分错误态
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
      setMsg({ t: '自动标题设置已保存（即时生效）' });
      bumpSaved();
    } catch (e) { setMsg({ t: `保存失败：${String(e)}`, err: true }); }
  }

  async function testConn() {
    setTesting(true); setMsg({ t: '测试中…' });
    try {
      // 先保存当前编辑值再测（key 留空时沿用已存 key）
      await saveTitlegenQuiet();
      const d = await api<{ reply: string }>('/api/settings/titlegen/test', { method: 'POST' });
      setMsg({ t: `✓ 连通正常，模型回复：「${d.reply}」` });
    } catch (e) { setMsg({ t: `✗ 测试失败：${String(e)}`, err: true }); }
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
      setMsg({ t: '运行参数已保存（并发数重启服务后生效）' });
      bumpSaved();
    } catch (e) { setMsg({ t: `保存失败：${String(e)}`, err: true }); }
  }

  async function saveEngines() {
    if (!eng || !cl) return;
    try {
      const engines: Record<string, unknown> = {};
      for (const [name, p] of Object.entries(eng.per)) {
        engines[name] = { bin: p.bin, model: p.model, provider: p.provider,
                          enabled: p.enabled, extra_args: p.extra_args };
      }
      const d = await api<{ engines: EnginesCfg }>('/api/settings/engines', {
        method: 'PUT', body: JSON.stringify({ default: eng.default,
          opencode_provider: eng.opencode_provider, no_compact: eng.no_compact, engines }) });
      setEng(d.engines);
      const d2 = await api<{ claude: ClaudeCfg }>('/api/settings/claude', {
        method: 'PUT', body: JSON.stringify({ effort: cl.effort, model: cl.model, claude_bin: cl.claude_bin }) });
      setCl(d2.claude);
      setEngMsg({ t: '✓ 已保存（新会话生效，在跑会话不受影响）' });
      bumpSaved();
    } catch (e) { setEngMsg({ t: `保存失败：${String(e)}`, err: true }); }
  }

  function updEngine(name: string, patch: Partial<PerEngineCfg>) {
    setEng(e => e ? { ...e, per: { ...e.per, [name]: { ...e.per[name], ...patch } } } : e);
  }

  /** 清除覆盖=整段删（yaml 段+内存回自动探测/继承），区别于逐字段清空 */
  async function clearEngineOverride(name: string) {
    if (!confirm(`清除 ${name} 的引擎覆盖？（yaml 段整删，bin/模型/参数回自动探测与继承链）`)) return;
    try {
      const d = await api<{ engines: EnginesCfg }>('/api/settings/engines', {
        method: 'PUT', body: JSON.stringify({ engines: { [name]: null } }) });
      setEng(d.engines);
      setEngMsg({ t: `✓ 已清除 ${name} 覆盖（下一 turn 起按自动探测）` });
    } catch (e) { setEngMsg({ t: `清除失败：${String(e)}`, err: true }); }
  }

  async function saveNotify() {
    await saveNotifyQuiet(true);
  }

  async function saveNotifyQuiet(quiet = false) {
    if (!nf) return;
    try {
      const body: Record<string, unknown> = { provider: nf.provider, bark_url: nf.bark_url,
                                               telegram_chat_id: nf.telegram_chat_id, events: nf.events };
      for (const k of ['serverchan_key', 'telegram_bot_token']) {
        const v = (nfKeys[k] ?? '').trim();
        if (v) body[k] = v;      // 留空 = 保持不变
      }
      const d = await api<{ notify: NotifyCfg }>('/api/settings/notify', {
        method: 'PUT', body: JSON.stringify(body) });
      setNf(d.notify); setNfKeys({});
      if (quiet) setNfMsg({ t: '✓ 已保存（即时生效）' });
      bumpSaved();
    } catch (e) { setNfMsg({ t: `保存失败：${String(e)}`, err: true }); throw e; }
  }

  async function testNotify() {
    setNfTesting(true); setNfMsg({ t: '发送中…（先保存当前编辑值）' });
    try {
      await saveNotifyQuiet();
      const d = await api<{ ok: boolean; msg?: string }>('/api/settings/notify/test', { method: 'POST' });
      setNfMsg(d.ok ? { t: '✓ 测试通知已发出（查收手机）' } : { t: `未发送：${d.msg || 'provider 未配置或通道参数不全'}`, err: true });
    } catch (e) { if (!String(e).includes('保存失败')) setNfMsg({ t: `✗ 失败：${String(e)}`, err: true }); }
    finally { setNfTesting(false); }
  }

  async function saveShare() {
    if (!share) return;
    try {
      const d = await api<{ share: ShareCfg }>('/api/settings/share', {
        method: 'PUT', body: JSON.stringify({ base_url: share.base_url }) });
      setShare(d.share);
      setShareMsg({ t: '✓ 已保存（即时生效）' });
      bumpSaved();
    } catch (e) { setShareMsg({ t: `保存失败：${String(e)}`, err: true }); }
  }

  /** 行校验（红框标记+行级文案，就地写回 rows）；全过才允许发请求 */
  function collectTable(rows: PriceRow[], cacheKey: string):
      { rows: PriceRow[]; table: Record<string, Record<string, number>> | null } {
    const seen = new Set<string>();
    const marked = rows.map(r => {
      let err = '';
      if (!r.model.trim()) err = '模型名不能为空';
      else if (seen.has(r.model.trim())) err = `模型名重复：${r.model.trim()}`;
      else if ([r.input, r.cache, r.output].some(v => v === '' || Number.isNaN(Number(v)) || Number(v) < 0))
        err = '三个数值都需为 ≥0 的数';
      if (r.model.trim()) seen.add(r.model.trim());
      const { err: _drop, ...rest } = r;
      return err ? { ...rest, err } : rest;
    });
    const bad = marked.some(r => (r as PriceRow).err);
    if (bad) return { rows: marked, table: null };
    const table = Object.fromEntries(marked.map(r => [r.model.trim(), {
      input: Number(r.input), [cacheKey]: Number(r.cache), output: Number(r.output) }]));
    return { rows: marked, table };
  }

  async function savePricing() {
    if (!pricing) return;
    const a = collectTable(apiRows, 'cache_read');
    const p = collectTable(planRows, 'cache_input');
    setApiRows(a.rows); setPlanRows(p.rows);
    if (a.table === null || p.table === null) {
      setPriceMsg({ t: '有校验未过的行（红底行）——修正后再保存', err: true });
      return;
    }
    try {
      const d = await api<{ pricing: PricingCfg }>('/api/settings/pricing', {
        method: 'PUT', body: JSON.stringify({ usd_cny: pricing.usd_cny,
          api: a.table, plan_credits: p.table }) });
      setPricing(d.pricing);
      setApiRows(rowsFromTable(d.pricing.api, 'cache_read'));
      setPlanRows(rowsFromTable(d.pricing.plan_credits, 'cache_input'));
      setPriceMsg({ t: '✓ 已保存（即时生效）' });
      bumpSaved();
    } catch (e) { setPriceMsg({ t: `保存失败：${String(e)}`, err: true }); }
  }

  async function restoreBuiltinPricing() {
    if (!pricing) return;
    const n = Object.keys(pricing.builtin?.api ?? {}).length;
    if (!confirm(`恢复内置官方价目？当前覆盖表（api ${apiRows.length} 行 / plan ${planRows.length} 行）将清空，回到 ${n} 个内置模型。`)) return;
    try {
      const d = await api<{ pricing: PricingCfg }>('/api/settings/pricing', {
        method: 'PUT', body: JSON.stringify({ usd_cny: pricing.usd_cny, api: {}, plan_credits: {} }) });
      setPricing(d.pricing);
      setApiRows([]); setPlanRows([]);
      setPriceMsg({ t: '✓ 已恢复内置价目' });
    } catch (e) { setPriceMsg({ t: `恢复失败：${String(e)}`, err: true }); }
  }

  async function copyPricingJson() {
    const a = collectTable(apiRows, 'cache_read');
    const p = collectTable(planRows, 'cache_input');
    const text = JSON.stringify({ api: a.table ?? {}, plan_credits: p.table ?? {} }, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      setPriceMsg({ t: '✓ 当前覆盖表 JSON 已复制到剪贴板' });
    } catch { setPriceMsg({ t: text }); }                       // 剪贴板不可用时直接展示
  }

  function updConv(name: string, patch: Partial<ConvRow>) {
    setConv(rs => rs?.map(r => r.name === name ? { ...r, ...patch } : r) ?? null);
  }

  /** 恢复默认=弹掉该角色的收敛三键（registry 条目保留，回 PROFILE_DEFAULTS） */
  async function resetConv(name: string) {
    const { timeout_s, stall_timeout_s, max_turns } = convDef;
    const t = `${Math.round(timeout_s / 60)} 分 / ${Math.round(stall_timeout_s / 60)} 分 / ${max_turns ?? '不限'}`;
    if (!confirm(`恢复 ${name} 的收敛度默认？（硬超时/静默判死/轮次上限 → ${t}）`)) return;
    try {
      const d = await api<{ profiles: ConvRow[] }>('/api/settings/convergence', {
        method: 'PUT', body: JSON.stringify({ reset: [name] }) });
      setConv(d.profiles);
      setConvMsg({ t: `✓ ${name} 已恢复默认（下一 turn 生效）` });
    } catch (e) { setConvMsg({ t: `恢复失败：${String(e)}`, err: true }); }
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
      setConvMsg({ t: '✓ 已保存（下一 turn 生效）' });
      bumpSaved();
    } catch (e) { setConvMsg({ t: `保存失败：${String(e)}`, err: true }); }
  }

  // AC-2.1：资源读写面收敛到资源 tab（/api/admin/resources/* 卡墙），
  // saveResources/testResources 与密钥表单已删——/api/settings/resources
  // 后端保留（资源卡墙同源落 vault），前端不再双编辑。

  if (!tg || !run) return loadErr ? (
    <div className="admin-body">
      <div className="admin-msg err">{loadErr}
        <button className="link" style={{ marginLeft: 8 }} onClick={() => void reload()}>重试</button>
      </div>
    </div>
  ) : <div className="admin-body muted">加载中…</div>;
  const groupNav = [
    { id: 'g-basic', label: '基础' }, { id: 'g-engine', label: '引擎与模型' },
    { id: 'g-notify', label: '通知与分享' }, { id: 'g-users', label: '用户与账号' },
    { id: 'g-res', label: '外部资源' },
  ];
  const G = ({ id, title }: { id: string; title: string }) => (
    <div id={id} className="settings-group-h">
      <span>{title}</span>
      <a className="link" onClick={() => document.getElementById('settings-top')
        ?.scrollIntoView({ behavior: 'smooth' })}>↑ 顶部</a>
    </div>
  );
  return (
    <div className="admin-body" id="settings-top" ref={rootRef}>
      <div className="settings-nav">
        {groupNav.map(g =>
          <a key={g.id} className="chip" onClick={() => document.getElementById(g.id)
            ?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>{g.label}</a>)}
        {dirty && <span className="chip warn" title="有未保存的改动，切页前先保存">● 未保存</span>}
      </div>
      <AppearanceCard />
      <G id="g-basic" title="基础 / 会话与外观" />
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
          {msg && <span className={msg.err ? 'admin-msg err' : 'admin-msg'}>{msg.t}</span>}
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

      <G id="g-engine" title="引擎与模型" />
      {eng && cl &&
      <div className="setting-card">
        <h4>引擎与模型 <span className="muted">（默认引擎 / 推理力度 / 模型与路径覆盖；新会话生效）</span></h4>
        <div className="setting-row">
          <span className="setting-k">默认引擎</span>
          {eng.available.map(e => (
            <button key={e} className={`chip ${eng.default === e ? 'on' : ''}`}
              onClick={() => setEng({ ...eng, default: e })}>{e}</button>
          ))}
          <span className="muted">灰度切换只翻这一处；角色的 engine 字段仍可按角色覆盖</span>
        </div>
        <div className="setting-row">
          <span className="setting-k">推理力度</span>
          {['low', 'medium', 'high'].map(v => (
            <button key={v} className={`chip ${cl.effort === v ? 'on' : ''}`}
              onClick={() => setCl({ ...cl, effort: v })}>{v}</button>
          ))}
          <span className="muted">无头会话默认值；会话属性面板可按会话覆盖</span>
        </div>
        <div className="setting-row">
          <span className="setting-k">默认模型</span>
          <input value={cl.model} onChange={e => setCl({ ...cl, model: e.target.value })}
            placeholder="空 = 继承 CLI/订阅默认" style={{ maxWidth: 240 }} />
          <span className="setting-k" style={{ paddingLeft: 12 }}>claude 路径</span>
          <input value={cl.claude_bin} onChange={e => setCl({ ...cl, claude_bin: e.target.value })}
            placeholder="空 = 自动探测" style={{ maxWidth: 240 }} />
        </div>
        <div className="setting-row">
          <span className="setting-k">loadn 内压</span>
          {([['自动', null], ['恒开', true], ['恒关', false]] as [string, boolean | null][]).map(([label, v]) => (
            <button key={label} className={`chip ${eng.no_compact === v ? 'on' : ''}`}
              onClick={() => setEng({ ...eng, no_compact: v })}>{label}</button>
          ))}
          <span className="muted">自动 = profile 设了 rotate_input_tokens 则禁内压</span>
          <span className="setting-k" style={{ paddingLeft: 12 }}>opencode 前缀</span>
          <input value={eng.opencode_provider} onChange={e => setEng({ ...eng, opencode_provider: e.target.value })}
            placeholder="zai" style={{ maxWidth: 120 }} />
        </div>
        <div className="tbl-wrap"><table className="mcp-table">
          <thead><tr><th>引擎</th><th>启用</th><th>bin</th><th>模型</th><th>provider</th><th>追加参数（每行一个）</th><th>操作</th></tr></thead>
          <tbody>{Object.entries(eng.per).map(([name, p]) => (
            <tr key={name}>
              <td>{name}{name === 'hahaness' && <span className="muted">（旧名）</span>}</td>
              <td><button className={`chip ${p.enabled ? 'on' : ''}`}
                onClick={() => updEngine(name, { enabled: !p.enabled })}>{p.enabled ? '启用' : '停用'}</button></td>
              <td><input value={p.bin} placeholder="自动" style={{ minWidth: 140 }}
                onChange={e => updEngine(name, { bin: e.target.value })} /></td>
              <td><input value={p.model} placeholder="继承" style={{ minWidth: 120 }}
                onChange={e => updEngine(name, { model: e.target.value })} /></td>
              <td><input value={p.provider} placeholder="—" style={{ minWidth: 90 }}
                onChange={e => updEngine(name, { provider: e.target.value })} /></td>
              <td><textarea className="mono" rows={2} value={p.extra_args.join('\n')}
                onChange={e => updEngine(name, { extra_args: e.target.value.split('\n') })} /></td>
              <td><button className="mini-btn" disabled={!p.override}
                title={p.override ? '删除 yaml 覆盖段，回自动探测/继承' : '该引擎无覆盖（全靠自动探测）'}
                onClick={() => void clearEngineOverride(name)}>清除覆盖</button></td>
            </tr>
          ))}</tbody>
        </table></div>
        <div className="setting-row">
          <button className="btn primary sm" onClick={() => void saveEngines()}>保存</button>
          {engMsg && <span className={engMsg.err ? 'admin-msg err' : 'admin-msg'}>{engMsg.t}</span>}
        </div>
        <div className="setting-note muted">改动不影响在跑会话——下一个 turn / 新会话按新值组装 argv；bin 留空走自动探测，模型留空走继承链（会话覆盖 &gt; claude 节 &gt; 引擎节 &gt; CLI 默认）。</div>
      </div>}

      <G id="g-notify" title="通知与分享" />
      {nf &&
      <div className="setting-card">
        <h4>运维通知 <span className="muted">（任务报错 / 调度唤醒 / 完成 → 推给自己手机）</span></h4>
        <div className="setting-row">
          <span className="setting-k">通道</span>
          {([['关', ''], ['Bark', 'bark'], ['Server酱', 'serverchan'], ['Telegram', 'telegram']] as [string, string][]).map(([label, v]) => (
            <button key={label} className={`chip ${nf.provider === v ? 'on' : ''}`}
              onClick={() => setNf({ ...nf, provider: v })}>{label}</button>
          ))}
        </div>
        {nf.provider === 'bark' && <div className="setting-row">
          <span className="setting-k">Bark URL</span>
          <input value={nf.bark_url} onChange={e => setNf({ ...nf, bark_url: e.target.value })}
            placeholder="https://api.day.app/<yourkey>" style={{ maxWidth: 340 }} />
        </div>}
        {nf.provider === 'serverchan' && <div className="setting-row">
          <span className="setting-k">SendKey</span>
          <input type="password" value={nfKeys.serverchan_key ?? ''}
            onChange={e => setNfKeys({ ...nfKeys, serverchan_key: e.target.value })}
            placeholder={nf.serverchan_key_set ? '已保存，留空不改' : 'sct…'} />
        </div>}
        {nf.provider === 'telegram' && <div className="setting-row">
          <span className="setting-k">Bot Token</span>
          <input type="password" value={nfKeys.telegram_bot_token ?? ''}
            onChange={e => setNfKeys({ ...nfKeys, telegram_bot_token: e.target.value })}
            placeholder={nf.telegram_bot_token_set ? '已保存，留空不改' : '123:abc…'} />
          <span className="setting-k" style={{ paddingLeft: 12 }}>Chat ID</span>
          <input value={nf.telegram_chat_id} onChange={e => setNf({ ...nf, telegram_chat_id: e.target.value })}
            placeholder="42" style={{ maxWidth: 140 }} />
        </div>}
        <div className="setting-row">
          <span className="setting-k">事件</span>
          {([['on_error', '任务报错'], ['on_scheduled', '调度唤醒'], ['on_turn_done', '任务完成']] as [string, string][]).map(([k, label]) => (
            <button key={k} className={`chip ${nf.events[k] ? 'on' : ''}`}
              onClick={() => setNf({ ...nf, events: { ...nf.events, [k]: !nf.events[k] } })}>{label}</button>
          ))}
          <span className="muted">任务完成默认关（防刷屏）</span>
        </div>
        <div className="setting-row">
          <button className="btn primary sm" onClick={() => void saveNotify()}>保存</button>
          <button className="btn sm" disabled={nfTesting} onClick={() => void testNotify()}>
            {nfTesting ? '发送中…' : '发测试通知'}
          </button>
          {nfMsg && <span className={nfMsg.err ? 'admin-msg err' : 'admin-msg'}>{nfMsg.t}</span>}
        </div>
        <div className="setting-note muted">与会话内 wechat-send（发给联系人）语义不同——这是运维告警通道。telegram 自动走「外部资源」里的代理（墙内必需）。</div>
      </div>}

      {share && <div className="setting-card">
        <h4>产物分享 <span className="muted">（/share/&lt;token&gt; 只读外链的对外基地址）</span></h4>
        <div className="setting-row">
          <span className="setting-k">外链 base</span>
          <input value={share.base_url} onChange={e => setShare({ ...share, base_url: e.target.value })}
            placeholder="https://your-domain.com/share（空 = 未启用）" style={{ maxWidth: 340 }} />
          <button className="btn primary sm" onClick={() => void saveShare()}>保存</button>
          {shareMsg && <span className={shareMsg.err ? 'admin-msg err' : 'admin-msg'}>{shareMsg.t}</span>}
        </div>
        <div className="setting-note muted">VPS 反代 your-domain.com/share → 本机 8792；只影响生成的外链前缀，分享路由本身一直在线。</div>
      </div>}

      {conv && <div className="setting-card">
        <h4>收敛度 <span className="muted">（防跑飞三闸，按角色：硬超时 / 静默判死 / 工具轮次上限）</span></h4>
        <div className="tbl-wrap"><table className="mcp-table">
          <thead><tr><th>角色</th><th>硬超时（分）</th><th>静默判死（分）</th><th>工具轮次上限</th><th>操作</th></tr></thead>
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
              <td><button className="mini-btn"
                title={`弹掉该角色的收敛三键，回默认（${Math.round(convDef.timeout_s / 60)} 分 / ${Math.round(convDef.stall_timeout_s / 60)} 分 / ${convDef.max_turns ?? '不限'}）`}
                onClick={() => void resetConv(r.name)}>恢复默认</button></td>
            </tr>
          ))}</tbody>
        </table></div>
        <div className="setting-row">
          <button className="btn primary sm" onClick={() => void saveConv()}>保存</button>
          {convMsg && <span className={convMsg.err ? 'admin-msg err' : 'admin-msg'}>{convMsg.t}</span>}
        </div>
        <div className="setting-note muted">硬超时 = turn 最长运行；静默判死 = 事件流 + transcript 双静默超阈值才杀
          （不误杀长 bash）；轮次上限 = agent 工具调用循环轮数（触顶报 error_max_turns，留空不限）。
          运行中 turn 不受影响，下一 turn 生效。</div>
      </div>}

      <G id="g-users" title="用户与账号" />
      <UsersCard />
      <G id="g-res" title="外部资源与服务器" />
      {res &&
      <div className="setting-card">
        <h4>外部资源 <span className="muted">（agent 动手能力：OCR/沙箱/短信/VLM/打码/搜索/真机）</span></h4>
        {/* AC-2.1：唯一编辑面收敛到资源 tab 卡墙（逐项独立测试+状态灯体验更好）；
            本卡改只读摘要+入口——同一配置双编辑器、字段三份拷贝的漂移风险到此为止 */}
        <div className="setting-row" style={{ cursor: 'pointer' }}
          onClick={() => { location.hash = '#/admin/resources'; }}>
          <span className="muted" style={{ fontSize: 'var(--fs-md)' }}>
            已配置端点 {[res.ocr_url, res.sandbox_url, res.cdp_url, res.proxy,
              res.sms_url, res.mail_user, res.adb_addr, res.textr_email]
              .filter(Boolean).length} 项 · 密钥 {[res.sandbox_api_key_set, res.sms_token_set,
              res.mail_auth_code_set, res.vlm_api_key_set, res.twocaptcha_key_set,
              res.bocha_key_set, res.zhipu_key_set, res.textr_password_set]
              .filter(Boolean).length} 项（AES-GCM，只写不回显）
          </span>
          <button className="btn sm primary" style={{ marginLeft: 'auto' }}>前往资源中心配置 →</button>
        </div>
        <div className="setting-note muted">
          卡片墙支持逐项独立测试（状态灯/耗时/最近失败原因）；密钥存 config.yaml 与 vault，
          agent 经 <code>python3 "$WORKDADDY_CLI" r …</code> 调用，不进环境变量。
        </div>
      </div>}

      {pricing && <div className="setting-card">
        <h4>成本价目 <span className="muted">（成本分析页价目；覆盖即整表替换，留空 = 恢复内置 z.ai 官方价目）</span></h4>
        <div className="setting-row">
          <span className="setting-k">USD/CNY</span>
          <input type="number" min={0} max={100} step={0.01} value={pricing.usd_cny}
            onChange={e => setPricing({ ...pricing, usd_cny: Number(e.target.value) })}
            style={{ maxWidth: 110 }} />
          <span className="muted">0 = 内置 7.1</span>
        </div>
        <PriceTable label="按量价 api" unit="单位 $/M tokens（input / cache_read / output）"
          cacheKey="cache_read" rows={apiRows} setRows={setApiRows}
          builtin={pricing.builtin?.api ?? {}} />
        <PriceTable label="订阅积分 plan_credits" unit="单位 积分/M tokens（input / cache_input / output）"
          cacheKey="cache_input" rows={planRows} setRows={setPlanRows}
          builtin={pricing.builtin?.plan_credits ?? {}} />
        <div className="setting-row">
          <button className="btn primary sm" onClick={() => void savePricing()}>保存</button>
          <button className="mini-btn" disabled={apiRows.length === 0 && planRows.length === 0}
            title="清空两张覆盖表，回官方内置价目"
            onClick={() => void restoreBuiltinPricing()}>恢复内置</button>
          <button className="mini-btn" title="当前覆盖表 JSON 复制到剪贴板（备份/手改）"
            onClick={() => void copyPricingJson()}>复制 JSON</button>
          {priceMsg && <span className={priceMsg.err ? 'admin-msg err' : 'admin-msg'} style={{ maxWidth: 420, whiteSpace: 'pre-wrap' }}>{priceMsg.t}</span>}
        </div>
        <div className="setting-note muted">覆盖即整表替换：两张表都留空=内置官方价目兜底。
          只调一两个模型的推荐路径是「从内置复制」后在行内改；删行=该模型回兜底表价。</div>
      </div>}

      {srv && <div className="setting-card">
        <h4>服务器 <span className="muted">（只读——host/port/令牌属启动期与部署面）</span></h4>
        <div style={{ display: 'flex', gap: 16, marginBottom: 8, flexWrap: 'wrap', fontSize: 'var(--fs-lg)' }}>
          <span>监听 <b className="mono-cell">{srv.host}:{srv.port}</b></span>
          <span>API token <b style={{ color: srv.token_set ? 'var(--green)' : undefined }}>{srv.token_set ? '已配置' : '未配置'}</b></span>
          <span>管理令牌 <b>{srv.admin_token_set ? '独立配置' : '复用 API token'}</b></span>
          {srv.token_grace_until > 0 && (
            <span className="muted">机生 token 宽限期至 {new Date(srv.token_grace_until * 1000).toLocaleString()}</span>
          )}
        </div>
        <div className="setting-note muted">改 host/port 编辑 config.yaml 后重启；查看/轮换令牌走 CLI
          <code> loadn-web token show | rotate</code>——界面上改自己正在用的 token 会把前端锁在外面，故不开放网页编辑。</div>
      </div>}
    </div>
  );
}


/* ================= 价目表结构化编辑（api / plan_credits 通用） ================= */
/** 行 CRUD + 从内置复制 + 空态兜底提示；行级校验文案由保存路径写入 rows[i].err */
function PriceTable({ label, unit, cacheKey, rows, setRows, builtin }: {
  label: string; unit: string; cacheKey: string;
  rows: PriceRow[]; setRows: (r: PriceRow[]) => void;
  builtin: Record<string, Record<string, number>>;
}) {
  const upd = (i: number, patch: Partial<PriceRow>) =>
    setRows(rows.map((r, j) => j === i ? { ...r, ...patch, err: undefined } : r));
  const fromBuiltin = () => {
    if (rows.length > 0
        && !confirm(`用内置价目覆盖当前 ${label} 编辑区（现有 ${rows.length} 行未保存改动将丢弃）？`)) return;
    setRows(rowsFromTable(builtin, cacheKey));
  };
  return (
    <div className="setting-row" style={{ alignItems: 'flex-start', flexDirection: 'column' as const }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', flexWrap: 'wrap' }}>
        <span className="setting-k">{label}</span>
        {rows.length === 0
          ? <span className="muted" style={{ fontSize: 'var(--fs-md)', flex: 1 }}>
              未覆盖——内置官方价目兜底（{Object.keys(builtin).length} 个模型）
            </span>
          : <span className="muted" style={{ fontSize: 'var(--fs-md)', flex: 1 }}>{unit}</span>}
        <button className="mini-btn" onClick={() => setRows(
          [...rows, { model: '', input: '', cache: '', output: '' }])}>+ 模型</button>
        <button className="mini-btn" onClick={fromBuiltin}
          disabled={Object.keys(builtin).length === 0}>从内置复制</button>
      </div>
      {rows.length > 0 && (
        <div className="tbl-wrap" style={{ width: '100%', marginTop: 6 }}>
          <table className="mcp-table">
            <thead><tr><th>模型</th><th>input</th><th>{cacheKey}</th><th>output</th><th /></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={r.err ? { background: 'rgba(229,72,77,.08)' } : undefined}>
                  <td><input value={r.model} placeholder="glm-…" style={{ minWidth: 140 }}
                    onChange={e => upd(i, { model: e.target.value })} /></td>
                  <td><input type="number" min={0} step="0.01" value={r.input} style={{ minWidth: 90 }}
                    onChange={e => upd(i, { input: e.target.value })} /></td>
                  <td><input type="number" min={0} step="0.01" value={r.cache} style={{ minWidth: 90 }}
                    onChange={e => upd(i, { cache: e.target.value })} /></td>
                  <td><input type="number" min={0} step="0.01" value={r.output} style={{ minWidth: 90 }}
                    onChange={e => upd(i, { output: e.target.value })} /></td>
                  <td><a className="danger-link" title="删除该模型行（回兜底表价）"
                    onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</a></td>
                </tr>
              ))}
              {rows.some(r => r.err) && (
                <tr><td colSpan={5} style={{ color: 'var(--danger)', fontSize: 'var(--fs-md)' }}>
                  {rows.filter(r => r.err).map(r => `${r.model || '(空名)'}：${r.err}`).join('；')}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ================= 外观（原侧栏「暗色」按钮并入） ================= */
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
