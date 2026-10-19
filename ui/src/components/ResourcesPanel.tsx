// 资源 Hub（管理中心「资源」tab，2026-10-19 设计稿重构批）：
// 三子视图——服务与环境资源（分类分组卡片墙）/ MCP 协议端点 / 凭证库。
// 核心交互：展示态卡片 + 抽屉编辑器（结构化表单 ↔ Resource.md 双向实时
// 同步）+ Harness 注入预览 + 导入 .md + 全部/单项探测（ping 历史回看）。
// 安全语义不变：密钥值永不出后端（只写不读）；凭证不进任务 env（预览中
// 以 vault decrypt 形式呈现）。后端 custom_services v2 结构化模型同批。
// 样式类在 index.css「资源 Hub」段（.rh-* 独立命名空间，双主题令牌）。
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { toast } from '../stores/toasts';
import { Lock, Zap, Key, Shield } from './icons';
import { askConfirm } from '../stores/confirm';

interface ServiceItem { key: string; label: string; note: string; value: string; kind: string }
interface SecretItem { key: string; set: boolean }
interface CustomSvc {
  name: string; url: string; note: string; category: string; icon: string; desc: string;
  enabled: boolean; prompt: string; key_set: boolean;
  config: { key: string; label: string; value: string; env: string }[];
  secrets: { label: string; env_key: string; vault_ref: string }[];
}
interface McpServer { name: string; spec: Record<string, any>; sessions_overriding: number; session_only?: boolean }
interface VaultPlatform { platform: string; user?: string; email?: string; has_password: boolean; updated_at: string }
interface Overview {
  services: ServiceItem[];
  secrets: SecretItem[];
  custom: CustomSvc[];
  mcp: { servers: McpServer[] };
  vault: { platforms: VaultPlatform[]; verify: { ok: boolean; entries: number; encrypted: boolean; error?: string } };
}

/** 统一资源视图模型：原生（GROUPS 定义+settings 扁平存取）与自定义（v2 条目）同构 */
interface RhCfgItem { key: string; label: string; value: string; env: string; svcKey?: string }
interface RhSecItem { label: string; envKey: string; vaultRef: string; set: boolean }
interface RhResource {
  id: string; native: boolean; name: string; icon: string; category: string;
  desc: string; note: string; enabled: boolean; prompt: string;
  config: RhCfgItem[]; secrets: RhSecItem[]; ping: string[];
}

/** 原生资源定义（不可增删；config.value 从 settings services 取，key=存取键） */
interface NativeCardDef {
  id: string; name: string; icon: string; note: string; ping?: string[];
  fields: { key: string; k: string }[];
  secrets: { key: string; label: string }[];
}
const GROUPS: { title: string; cards: NativeCardDef[] }[] = [
  {
    title: '基础设施', cards: [
      { id: 'ocr', name: 'OCR 识别', icon: '🖨️', note: '图片 / PDF / Office 文本识别', ping: ['ocr'],
        fields: [{ key: 'ocr_url', k: '端点' }], secrets: [] },
      { id: 'sandbox', name: 'AIO 沙箱', icon: '📦', note: '浏览器 / 命令行 / 文件 操作环境', ping: ['sandbox', 'sandbox_mcp'],
        fields: [{ key: 'sandbox_url', k: '端点' }, { key: 'cdp_url', k: 'CDP' }],
        secrets: [{ key: 'sandbox_api_key', label: 'API Key' }] },
      { id: 'proxy', name: '出网代理', icon: '🌐', note: '出海请求经此转发（clash 等）', ping: ['proxy'],
        fields: [{ key: 'proxy', k: '地址' }], secrets: [] },
      { id: 'adb', name: 'Android 真机', icon: '📱', note: 'adb 无线调试',
        fields: [{ key: 'adb_addr', k: '地址' }], secrets: [] },
    ],
  },
  {
    title: '通信', cards: [
      { id: 'sms', name: '短信查询', icon: '💬', note: '真机收验证码', ping: ['sms'],
        fields: [{ key: 'sms_url', k: '端点' }, { key: 'sms_phone', k: '手机号' }],
        secrets: [{ key: 'sms_token', label: 'Token' }] },
      { id: 'mail', name: '主邮箱', icon: '📮', note: '注册 / 登录收验证码（126/163 需授权码）', ping: ['mail'],
        fields: [{ key: 'mail_imap', k: 'IMAP' }, { key: 'mail_smtp', k: 'SMTP' }, { key: 'mail_user', k: '账号' }],
        secrets: [{ key: 'mail_auth_code', label: '授权码' }] },
      { id: 'textr', name: 'Textr 虚拟号', icon: '🇺🇸', note: '美国号码收码',
        fields: [{ key: 'textr_email', k: '账号' }],
        secrets: [{ key: 'textr_password', label: '密码' }] },
    ],
  },
  {
    title: 'AI 能力', cards: [
      { id: 'vlm', name: '视觉模型', icon: '👁️', note: '图片理解（截图分析等）', ping: ['vlm'],
        fields: [{ key: 'vlm_api_base', k: 'API' }, { key: 'vlm_model', k: '模型' }],
        secrets: [{ key: 'vlm_api_key', label: 'API Key' }] },
      { id: 'search', name: '网页搜索', icon: '🔍', note: '博查（中文）/ 智谱（档位计费）', ping: ['bocha', 'zhipu'],
        fields: [{ key: 'zhipu_engine', k: '智谱档' }],
        secrets: [{ key: 'bocha_key', label: '博查 Key' }, { key: 'zhipu_key', label: '智谱 Key' }] },
      { id: 'captcha', name: '过验证码', icon: '🧩', note: '2Captcha（图形码 / reCAPTCHA）', ping: ['twocaptcha'],
        fields: [], secrets: [{ key: 'twocaptcha_key', label: 'API Key' }] },
    ],
  },
];

/** vault 键白名单（后端 set_res_secret 同款）：原生密钥键 + svc:<资源名> */
const NATIVE_SECRET_KEYS = ['sandbox_api_key', 'sms_token', 'mail_auth_code', 'vlm_api_key',
  'twocaptcha_key', 'bocha_key', 'zhipu_key', 'textr_password'];
const CATEGORIES = ['基础设施', '通信', 'AI 能力', '自定义服务'];

type Section = 'service' | 'mcp' | 'vault';
type PingRes = Record<string, { ok: boolean; msg: string; ms?: number }>;

/** Resource.md 生成（frontmatter + prompt 正文）——与后端 v2 字段一一对应 */
function genResourceMd(r: RhResource): string {
  const q = (s: string) => `"${String(s ?? '').replace(/"/g, '\\"')}"`;
  let y = '---\n'
    + `id: ${r.id}\nname: ${q(r.name)}\ncategory: ${q(r.category)}\n`
    + `icon: ${q(r.icon || '🔌')}\ndescription: ${q(r.desc || r.note || '')}\n`
    + `enabled: ${r.enabled}\n`;
  if (r.config.length) {
    y += 'config:\n';
    for (const c of r.config) {
      y += `  ${c.key || c.env.toLowerCase() || 'field'}:\n`
        + `    label: ${q(c.label)}\n    value: ${q(c.value)}\n    env: ${q(c.env)}\n`;
    }
  } else { y += 'config: []\n'; }
  if (r.secrets.length) {
    y += 'secrets:\n';
    for (const s of r.secrets) {
      y += `  - label: ${q(s.label)}\n    env_key: ${q(s.envKey)}\n    vault_ref: ${q(s.vaultRef)}\n`;
    }
  } else { y += 'secrets: []\n'; }
  return y + '---\n\n' + (r.prompt || '').trim() + '\n';
}

/** Resource.md 解析（宽容行式：标量/config 嵌套/secrets 列表项）。
 *  解析失败返回 null（前端态红条提示，不炸编辑） */
function parseResourceMd(text: string): RhResource | null {
  const m = text.match(/^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/);
  if (!m) return null;
  const unq = (s: string) => s.trim().replace(/^["']|["']$/g, '');
  const scalar: Record<string, string> = {};
  const config: RhCfgItem[] = [];
  const secrets: RhSecItem[] = [];
  let mode: 'root' | 'config' | 'secrets' = 'root';
  let curCfg: RhCfgItem | null = null;
  let curSec: RhSecItem | null = null;
  for (const ln of m[1].split('\n')) {
    if (!ln.trim() || ln.trim().startsWith('#')) continue;
    let mm = ln.match(/^(\w[\w-]*):\s*(.*)$/);
    if (mm && !ln.startsWith(' ')) {
      mode = 'root'; curCfg = null; curSec = null;
      if (mm[1] === 'config' && unq(mm[2]) === '[]') continue;
      if (mm[1] === 'secrets' && unq(mm[2]) === '[]') continue;
      if (mm[1] === 'config' || mm[1] === 'secrets') { mode = mm[1] as 'config' | 'secrets'; continue; }
      scalar[mm[1]] = unq(mm[2]);
      continue;
    }
    if (mode === 'config') {
      mm = ln.match(/^  (\S+):\s*(.*)$/);
      if (mm) {
        curCfg = { key: mm[1].replace(/:$/, ''), label: mm[1], value: '', env: '' };
        config.push(curCfg); continue;
      }
      mm = ln.match(/^    (\w+):\s*(.*)$/);
      if (mm && curCfg) {
        const v = unq(mm[2]);
        if (mm[1] === 'label') curCfg.label = v;
        else if (mm[1] === 'value') curCfg.value = v;
        else if (mm[1] === 'env') curCfg.env = v;
      }
    } else if (mode === 'secrets') {
      mm = ln.match(/^  - label:\s*(.*)$/);
      if (mm) {
        curSec = { label: unq(mm[1]), envKey: '', vaultRef: '', set: false };
        secrets.push(curSec); continue;
      }
      mm = ln.match(/^    (\w+):\s*(.*)$/);
      if (mm && curSec) {
        const v = unq(mm[2]);
        if (mm[1] === 'env_key') curSec.envKey = v;
        else if (mm[1] === 'vault_ref') curSec.vaultRef = v;
      }
    }
  }
  if (!scalar.id && !scalar.name) return null;
  return {
    id: scalar.id || scalar.name || 'custom_service',
    native: false, name: scalar.name || scalar.id || '新资源',
    icon: scalar.icon || '🔌',
    category: CATEGORIES.includes(scalar.category) ? scalar.category : '自定义服务',
    desc: scalar.description || '', note: scalar.description || '',
    enabled: scalar.enabled !== 'false',
    prompt: m[2].trim(), config, secrets, ping: [],
  };
}

export default function ResourcesPanel() {
  const [data, setData] = useState<Overview | null>(null);
  const [sec, setSec] = useState<Section>('service');
  const [catFilter, setCatFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [pinging, setPinging] = useState<string | null>(null);
  const [pingRes, setPingRes] = useState<PingRes>({});
  const [hist, setHist] = useState<Record<string, {
    last: { ok: boolean; ms?: number | null; msg?: string | null; ts: string };
    fails_24h: number;
  }>>({});
  const [vSearch, setVSearch] = useState('');
  const [loadErr, setLoadErr] = useState('');
  // 抽屉态
  const [editing, setEditing] = useState<RhResource | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [mode, setMode] = useState<'form' | 'md'>('form');
  const [mdText, setMdText] = useState('');
  const [mdOk, setMdOk] = useState(true);
  const [secretDraft, setSecretDraft] = useState<Record<string, string>>({});
  // 导入弹窗
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');

  const loadHist = async () => {
    try {
      const d = await api<{ items: { target: string; last: { ok: boolean; ms?: number | null; msg?: string | null; ts: string }; fails_24h: number }[] }>(
        '/api/admin/resources/history');
      setHist(Object.fromEntries(d.items.map(it => [it.target, it])));
    } catch { /* 历史是增强面：失败不阻断概览 */ }
  };
  const load = async () => {
    try { setData(await api<Overview>('/api/admin/resources')); setLoadErr(''); }
    catch (e) { setLoadErr(`资源概览加载失败：${String(e)}`); }
    void loadHist();
  };
  useEffect(() => { void load(); }, []);

  if (!data) return loadErr ? (
    <div className="admin-body">
      <div className="admin-msg err">{loadErr}
        <button className="link" style={{ marginLeft: 8 }} onClick={() => void load()}>重试</button>
      </div>
    </div>
  ) : <div className="admin-body muted">加载中…</div>;

  const valOf = (k: string) => data.services.find(s => s.key === k)?.value ?? '';
  const secretSet = (k: string) => data.secrets.find(s => s.key === k)?.set ?? false;

  /** 原生+自定义 → 统一资源合集（量级 ~20，直接算不需 memo；且须在早退
   *  返回之后——原 useMemo 写法被早退分支条件化，违反 hooks 规则） */
  const all: RhResource[] = (() => {
    const native: RhResource[] = GROUPS.flatMap(g => g.cards.map(c => ({
      id: c.id, native: true, name: c.name, icon: c.icon, category: g.title,
      desc: c.note, note: c.note, enabled: true, prompt: '', ping: c.ping || [],
      config: c.fields.map(f => ({
        key: f.key, label: f.k, value: valOf(f.key),
        env: '', svcKey: f.key,
      })),
      secrets: (c.secrets || []).map(s => ({
        label: s.label, envKey: '', vaultRef: s.key, set: secretSet(s.key),
      })),
    })));
    const custom: RhResource[] = data.custom.map(c => ({
      id: c.name, native: false, name: c.name, icon: c.icon || '🧩',
      category: CATEGORIES.includes(c.category) ? c.category : '自定义服务',
      desc: c.desc || c.note || '', note: c.note || '',
      enabled: c.enabled !== false, prompt: c.prompt || '',
      ping: [`svc:${c.name}`],
      config: (c.config || []).map(x => ({ ...x })),
      secrets: (c.secrets || []).map(s => ({
        label: s.label, envKey: s.env_key, vaultRef: s.vault_ref,
        set: s.vault_ref.startsWith('svc:') ? c.key_set : secretSet(s.vault_ref),
      })),
    }));
    return [...native, ...custom];
  })();

  const matchSearch = (r: RhResource) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return r.name.toLowerCase().includes(q) || r.id.toLowerCase().includes(q)
      || r.desc.toLowerCase().includes(q)
      || r.config.some(c => c.value.toLowerCase().includes(q) || c.env.toLowerCase().includes(q));
  };
  const shown = catFilter === 'all' ? all.filter(matchSearch)
    : all.filter(r => r.category === catFilter && matchSearch(r));
  const svcCount = all.length;

  // ---- 探测 ----
  const stOf = (p: string): { ok: boolean; msg?: string; ms?: number } | undefined =>
    pingRes[p] ?? (hist[p]
      ? { ok: hist[p].last.ok, msg: hist[p].last.msg ?? undefined, ms: hist[p].last.ms ?? undefined }
      : undefined);
  const doPing = async (targets: string[], mark: string) => {
    if (!targets.length) return;
    setPinging(mark);
    try {
      const d = await api<{ results: PingRes }>('/api/admin/resources/test',
        { method: 'POST', body: JSON.stringify({ only: targets }) });
      setPingRes(r => ({ ...r, ...d.results }));
      void loadHist();
    } catch (e) { toast(`探测失败：${String(e)}`, false); }
    finally { setPinging(null); }
  };
  const pingCard = (r: RhResource) => {
    if (r.native) { void doPing(r.ping, r.id); return; }
    void doPing(r.ping, `svc:${r.id}`);
  };
  const pingAll = () => void doPing(all.flatMap(r => r.ping), '__all__');

  /** 卡片状态徽章（本次探测优先→历史回退；附 24h 败数） */
  const badgeOf = (r: RhResource) => {
    if (!r.ping.length) return <span className="rh-badge untested">—</span>;
    if (pinging === r.id || pinging === `svc:${r.id}`) return <span className="rh-badge testing">探测中…</span>;
    const rs = r.ping.map(p => stOf(p)).filter(Boolean) as { ok: boolean; msg?: string; ms?: number }[];
    if (!rs.length) return <span className="rh-badge untested">未测</span>;
    const ok = rs.every(x => x.ok);
    if (ok) {
      const ms = Math.max(...rs.map(x => x.ms ?? 0));
      return <span className="rh-badge online"><span className="rh-dot" />{ms ? `${ms}ms` : '在线'}</span>;
    }
    const fails = r.ping.reduce((s, p) => s + (hist[p]?.fails_24h ?? 0), 0);
    return <span className="rh-badge error" title={rs.find(x => !x.ok)?.msg}>
      需检查{fails > 0 ? ` · 24h败${fails}` : ''}</span>;
  };

  // ---- 抽屉 ----
  const openCreate = (preset?: Partial<RhResource>) => {
    setIsNew(true); setMode('form'); setSecretDraft({});
    setEditing({
      id: '', native: false, name: '', icon: '🔌', category: '自定义服务',
      desc: '', note: '', enabled: true, prompt: '', config: [], secrets: [], ping: [],
      ...preset,
    });
  };
  const openEdit = (r: RhResource) => {
    setIsNew(false); setMode('form'); setSecretDraft({});
    setEditing(JSON.parse(JSON.stringify(r)));
  };
  const closeDrawer = () => { setEditing(null); setMdText(''); };
  const switchMode = (m: 'form' | 'md') => {
    if (!editing) return;
    if (m === 'md') { setMdText(genResourceMd(editing)); setMdOk(true); }
    setMode(m);
  };
  const onMdInput = (text: string) => {
    setMdText(text);
    const parsed = parseResourceMd(text);
    if (parsed) { setEditing({ ...parsed, native: false }); setMdOk(true); }
    else setMdOk(false);
  };
  const patch = (p: Partial<RhResource>) => setEditing(e => e ? { ...e, ...p } : e);

  /** 保存分流：自定义→custom v2 API；原生→逐字段 settings API（+密钥草稿） */
  const saveDrawer = async () => {
    if (!editing) return;
    if (!editing.id.trim() || !editing.name.trim()) { toast('资源名称与唯一 ID 必填', false); return; }
    if (!editing.native && !/^[a-z0-9][a-z0-9_-]{0,31}$/.test(editing.id)) {
      toast('ID 需为小写 slug（字母/数字开头，可含 - _，≤32 位）', false); return;
    }
    try {
      if (editing.native) {
        for (const c of editing.config) {
          if (c.svcKey && c.value !== valOf(c.svcKey)) {
            await api('/api/admin/resources/service',
              { method: 'POST', body: JSON.stringify({ key: c.svcKey, value: c.value }) });
          }
        }
      } else {
        await api('/api/admin/resources/custom', {
          method: 'POST',
          body: JSON.stringify({
            name: editing.id, url: editing.config.find(c =>
              c.value.startsWith('http://') || c.value.startsWith('https://'))?.value || '',
            note: editing.note || editing.desc, category: editing.category,
            icon: editing.icon, desc: editing.desc, enabled: editing.enabled,
            prompt: editing.prompt,
            config: editing.config.map(c => ({
              key: c.key || c.env.toLowerCase(), label: c.label, value: c.value, env: c.env })),
            secrets: editing.secrets.map(s => ({
              label: s.label, env_key: s.envKey, vault_ref: s.vaultRef })),
          }),
        });
      }
      for (const [k, v] of Object.entries(secretDraft)) {
        if (v.trim()) {
          await api('/api/admin/resources/secret',
            { method: 'POST', body: JSON.stringify({ key: k, value: v.trim() }) });
        }
      }
      toast(isNew ? `已创建资源「${editing.name}」` : `已保存资源「${editing.name}」`);
      closeDrawer(); await load();
    } catch (e) { toast(`保存失败：${String(e)}`, false); }
  };
  const delDrawer = async () => {
    if (!editing || editing.native) return;
    if (!await askConfirm({ title: `删除资源「${editing.name}」？（vault 中的密钥一并清除）`, danger: true })) return;
    try {
      await api(`/api/admin/resources/custom?name=${encodeURIComponent(editing.id)}`, { method: 'DELETE' });
      toast('已删除资源'); closeDrawer(); await load();
    } catch (e) { toast(`删除失败：${String(e)}`, false); }
  };

  /** 导入 Resource.md：解析成功开抽屉（用户确认后落库） */
  const doImport = () => {
    const parsed = parseResourceMd(importText);
    if (!parsed) { toast('frontmatter 解析失败（需 --- 包裹且含 id/name）', false); return; }
    setImportOpen(false); setImportText('');
    openCreate(parsed);
  };

  /** Harness 注入预览（诚实语义：自定义=env 注入；原生=平台集成消费；
   *  密钥恒为 vault 解密形式，不进任务 env） */
  const envPreview = (r: RhResource) => {
    if (!r.enabled) return <><span className="c"># 该资源当前已停用（不注入）</span></>;
    if (r.native) return <>
      <span className="c"># 原生资源：由平台内置集成消费（OCR/搜索/收码等 skill 通道）</span><br />
      <span className="c"># 不直接注入任务 env</span>
    </>;
    if (!r.config.length && !r.secrets.length) return <><span className="c"># 暂无可注入字段</span></>;
    return <>
      <span className="c"># 1. Harness 注入的环境变量（下一 turn 起）</span><br />
      {r.config.map((c, i) => (<span key={i}>
        <span className="k">export</span> <span className="v">{c.env}</span>=&quot;{c.value}&quot;<br />
      </span>))}
      {r.secrets.length > 0 && <><span className="c"># 2. 密钥：平台侧解密后供集成调用（不进任务 env）</span><br /></>}
      {r.secrets.map((s, i) => (<span key={i}>
        <span className="k">export</span> <span className="s">{s.envKey}</span>=<span className="s">&quot;$(vault decrypt {s.vaultRef})&quot;</span><br />
      </span>))}
    </>;
  };

  const vFiltered = data.vault.platforms.filter(p =>
    !vSearch || p.platform.toLowerCase().includes(vSearch.toLowerCase())
    || (p.user || '').toLowerCase().includes(vSearch.toLowerCase())
    || (p.email || '').toLowerCase().includes(vSearch.toLowerCase()));

  return (
    <div className="admin-body">
      {/* ===== 子导航：服务 / MCP / 凭证库 + 加密提示 ===== */}
      <div className="rh-subnav">
        {([['service', `服务与环境资源`, svcCount], ['mcp', `MCP 协议端点`, data.mcp.servers.length],
          ['vault', `凭证库`, data.vault.platforms.length]] as [Section, string, number][]).map(([k, lb, n]) => (
          <button key={k} className={`rh-subnav-tab ${sec === k ? 'on' : ''}`}
            onClick={() => setSec(k)}>{lb}<span className="rh-count">{n}</span></button>
        ))}
        <span className="rh-subnav-note"><Lock size={13} /> 密钥 AES-GCM 加密存储 · 明文不落配置</span>
      </div>

      {/* ============ 子视图 1：服务与环境资源（卡片墙） ============ */}
      {sec === 'service' && (
        <>
          <div className="rh-toolbar">
            <div className="rh-seg">
              {['all', ...CATEGORIES].map(c => (
                <button key={c} className={`rh-seg-btn ${catFilter === c ? 'on' : ''}`}
                  onClick={() => setCatFilter(c)}>{c === 'all' ? '全部' : c.replace('服务', '')}</button>
              ))}
            </div>
            <input className="rh-search admin-input" placeholder="搜索资源名、端点、环境变量…"
              value={search} onChange={e => setSearch(e.target.value)} autoComplete="off" />
            <span className="rh-toolbar-hint hidden-mobile">
              已启用资源自动注入 <code>Harness Env</code>
            </span>
            <button className="btn" disabled={!!pinging} onClick={pingAll}>
              {pinging === '__all__' ? '探测中…' : <><Zap size={13} style={{ verticalAlign: -2 }} /> 全部探测</>}
            </button>
            <button className="btn ghost" onClick={() => setImportOpen(true)}>导入 .md</button>
            <button className="btn" onClick={() => openCreate()}>＋ 新增资源</button>
          </div>

          {CATEGORIES.map(cat => {
            if (catFilter !== 'all' && catFilter !== cat) return null;
            const cards = shown.filter(r => r.category === cat);
            if (!cards.length && cat !== '自定义服务') return null;
            return (
              <div key={cat}>
                <div className="rh-group-title">{cat}<span className="rh-count">({cards.length})</span></div>
                <div className="rh-grid">
                  {cards.length === 0 && cat === '自定义服务' && (
                    <div className="rh-empty" style={{ gridColumn: '1 / -1' }} onClick={() => openCreate()}>
                      <div className="rh-empty-l">
                        <div className="rh-empty-plus">＋</div>
                        <div>
                          <div className="rh-empty-t">添加自定义资源或导入 Resource.md</div>
                          <div className="rh-empty-s">支持任意异构参数、绑定加密库密钥</div>
                        </div>
                      </div>
                      <span className="rh-empty-go">立即配置 →</span>
                    </div>
                  )}
                  {cards.map(r => {
                    const extra = Math.max(0, r.config.length - 2);
                    return (
                      <div key={r.id} className={`rh-card ${r.enabled ? '' : 'off'}`} onClick={() => openEdit(r)}>
                        <div>
                          <div className="rh-card-head">
                            <div className="rh-card-id">
                              <span className="rh-icon">{r.icon}</span>
                              <span className="rh-name">{r.name}</span>
                            </div>
                            {badgeOf(r)}
                          </div>
                          <div className="rh-desc" title={r.desc}>{r.desc || '—'}</div>
                          <div className="rh-kvs" style={{ marginTop: 8 }}>
                            {r.config.length > 0 ? r.config.slice(0, 2).map((c, i) => (
                              <div key={i} className="rh-kv"><b>{c.label}</b><span>{c.value || '（未配置）'}</span></div>
                            )) : <div className="rh-kv"><b>纯凭证驱动</b><span>—</span></div>}
                          </div>
                        </div>
                        <div className="rh-card-foot">
                          <div className="rh-chips">
                            {r.secrets.length > 0 ? r.secrets.slice(0, 2).map((s, i) => (
                              <span key={i} className={`rh-chip ${s.set ? 'ok' : 'warn'}`}>
                                🔑 {s.label} {s.set ? '已加密' : '未设'}</span>
                            )) : <span className="rh-chip dim">免密</span>}
                            {r.secrets.length > 2 && <span className="rh-chip dim">+{r.secrets.length - 2}</span>}
                            {extra > 0 && <span className="rh-chip dim">+{extra} 项</span>}
                          </div>
                          {r.ping.length > 0 && (
                            <button className="btn ghost sm" onClick={e => { e.stopPropagation(); pingCard(r); }}>
                              测试</button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </>
      )}

      {/* ============ 子视图 2：MCP 协议端点 ============ */}
      {sec === 'mcp' && (
        <>
          <div className="rh-mcp-hero" style={{ marginTop: 14 }}>
            <div>
              <div className="rh-mcp-hero-t">MCP (Model Context Protocol) 与 Resource.md 的协同</div>
              <div className="rh-mcp-hero-s">
                MCP 提供标准化的工具 Schema 暴露；「服务与环境资源」通过 <code>Resource.md</code>
                为 Harness 提供环境变量注入、凭证解耦与非标设施的使用说明书。
              </div>
            </div>
            <button className="btn" onClick={() => { setSec('service'); openCreate(); }}>＋ 新建 MD 资源</button>
          </div>
          <div className="res-addbar focused" style={{ margin: '10px 0', cursor: 'pointer' }}
            onClick={() => { location.hash = '#/admin/tools'; }}>
            <span>🧩 MCP server 的增删改收敛在「工具」tab（stdio/http/sse 全类型编辑器 + 会话覆盖计数）</span>
            <button className="btn ghost" style={{ marginLeft: 'auto' }}>前往工具 tab 管理 →</button>
          </div>
          <div className="rh-mcp-grid">
            {data.mcp.servers.length === 0 && (
              <div className="rh-empty" style={{ gridColumn: '1 / -1' }} onClick={() => { location.hash = '#/admin/tools'; }}>
                <div className="rh-empty-l">
                  <div className="rh-empty-plus">🧩</div>
                  <div>
                    <div className="rh-empty-t">尚无全局 MCP server</div>
                    <div className="rh-empty-s">前往工具 tab 添加 stdio / http / sse 端点</div>
                  </div>
                </div>
                <span className="rh-empty-go">去添加 →</span>
              </div>
            )}
            {data.mcp.servers.map(m => {
              const cmd = [m.spec?.command, ...(m.spec?.args ?? [])].filter(Boolean).join(' ')
                || m.spec?.url || '-';
              const kind = m.spec?.command ? 'stdio' : (m.spec?.url?.includes('sse') ? 'SSE' : 'HTTP');
              return (
                <div key={m.name} className="rh-mcp-card">
                  <div className="rh-card-head">
                    <div className="rh-card-id">
                      <span className="rh-icon">🧩</span>
                      <span className="rh-name">{m.name}</span>
                    </div>
                    <span className="rh-chip ok">● 运行中 ({kind})</span>
                  </div>
                  <div className="rh-desc">会话覆盖 {m.sessions_overriding || 0} 个
                    {m.session_only && '（仅会话级）'}</div>
                  <div className="rh-mcp-cmd" title={cmd}>{cmd}</div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* ============ 子视图 3：凭证库（只读概览，编辑唯一面=安全 tab） ============ */}
      {sec === 'vault' && (
        <>
          <div className="rh-toolbar">
            <input className="rh-search admin-input" placeholder="搜索平台 / 账号…"
              value={vSearch} onChange={e => setVSearch(e.target.value)} autoComplete="off" />
            <span className="rh-subnav-note" style={{ marginLeft: 'auto' }}>
              <Shield size={13} /> {data.vault.verify.ok
                ? `加密库健康 · ${data.vault.verify.entries} 条 · AES-GCM`
                : `⚠ 校验失败：${data.vault.verify.error ?? '未知'}`}
            </span>
            <button className="btn ghost" onClick={() => { location.hash = '#/admin/security'; }}>
              前往安全 tab 管理 →</button>
          </div>
          <div className="rh-vault-list">
            {vFiltered.length === 0 && <div className="rh-vault-row">
              <span className="rh-rows-empty" style={{ width: '100%' }}>（无匹配条目）</span>
            </div>}
            {vFiltered.map(p => (
              <div key={p.platform} className="rh-vault-row">
                <div className="rh-vault-id">
                  <div className={`rh-vault-ico ${p.has_password ? '' : 'warn'}`}>
                    {p.has_password ? '🔒' : '⚠️'}</div>
                  <div>
                    <div className="rh-vault-name">{p.platform}
                      <span className="rh-id-badge" style={{ marginLeft: 8 }}>
                        {p.user || p.email || '—'}</span></div>
                    <div className="rh-vault-sub">更新 {(p.updated_at || '').slice(0, 10)}</div>
                  </div>
                </div>
                <span className={`rh-chip ${p.has_password ? 'ok' : 'warn'}`}>
                  <Key size={11} /> {p.has_password ? '密码已存' : '密码缺失'}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {/* ===== 抽屉（表单 ↔ Resource.md 双向编辑） ===== */}
      {editing && (
        <>
          <div className="rh-mask" onClick={closeDrawer} />
          <div className="rh-drawer" onKeyDown={e => { if (e.key === 'Escape') closeDrawer(); }}>
            <div className="rh-drawer-head">
              <div className="rh-drawer-title">
                <span className="rh-icon" style={{ width: 36, height: 36, fontSize: 18 }}>{editing.icon || '🔌'}</span>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <h2>{isNew ? '新增资源' : `配置资源 · ${editing.name}`}</h2>
                    <span className="rh-id-badge">id: {editing.id || '—'}</span>
                  </div>
                  <div className="rh-drawer-sub">结构化表单与 Resource.md 双向实时同步</div>
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div className="rh-mode">
                  <button className={`rh-mode-btn ${mode === 'form' ? 'on' : ''}`}
                    onClick={() => switchMode('form')}>🎛️ 表单</button>
                  <button className={`rh-mode-btn ${mode === 'md' ? 'on' : ''}`}
                    onClick={() => switchMode('md')}>📝 MD</button>
                </div>
                <button className="btn ghost sm" onClick={closeDrawer}>✕</button>
              </div>
            </div>

            {isNew && (
              <div className="rh-preset" style={{ margin: '12px 20px 0' }}>
                <span>快速套用异构资源模板：</span>
                <div className="rh-preset-ops">
                  <button className="rh-preset-btn" onClick={() => patch({
                    name: 'Jina 网页提取', id: 'jina', icon: '📄', desc: 'URL 转纯净 Markdown',
                    config: [{ key: 'endpoint', label: '端点', value: 'https://r.jina.ai', env: 'LOADN_SVC_JINA_URL' }],
                  })}>🌐 HTTP 微服务</button>
                  <button className="rh-preset-btn" onClick={() => patch({
                    name: '追加邮箱', id: 'mail_extra', icon: '📮', desc: '收验证码邮箱（IMAP/SMTP）',
                    config: [{ key: 'imap', label: 'IMAP', value: 'imap.example.com:993', env: 'LOADN_MAIL2_IMAP' },
                      { key: 'smtp', label: 'SMTP', value: 'smtp.example.com:465', env: 'LOADN_MAIL2_SMTP' }],
                    secrets: [{ label: '授权码', envKey: 'LOADN_MAIL2_AUTH', vaultRef: 'svc:mail_extra', set: false }],
                  })}>📮 邮箱</button>
                  <button className="rh-preset-btn" onClick={() => patch({
                    name: '模型端点', id: 'llm_api', icon: '👁️', desc: 'OpenAI 兼容推理端点',
                    config: [{ key: 'api', label: 'API', value: 'https://api.example.com/v1', env: 'LOADN_LLM2_API' },
                      { key: 'model', label: '模型', value: 'gpt-4o-mini', env: 'LOADN_LLM2_MODEL' }],
                    secrets: [{ label: 'API Key', envKey: 'LOADN_LLM2_KEY', vaultRef: 'svc:llm_api', set: false }],
                  })}>🤖 模型端点</button>
                  <button className="rh-preset-btn" onClick={() => patch({
                    name: 'ADB 节点', id: 'adb_node', icon: '📱', desc: '局域网 Android 无线调试节点',
                    config: [{ key: 'address', label: '地址', value: '192.168.1.100:5555', env: 'LOADN_ADB2_SERIAL' }],
                  })}>📱 ADB 节点</button>
                </div>
              </div>
            )}

            <div className="rh-drawer-body">
              {mode === 'form' ? (
                <>
                  <div className="rh-form-grid">
                    <div>
                      <label className="rh-label">图标</label>
                      <input className="admin-input" value={editing.icon} style={{ textAlign: 'center' }}
                        onChange={e => patch({ icon: e.target.value })} autoComplete="off" />
                    </div>
                    <div>
                      <label className="rh-label">资源名称 <span className="req">*</span></label>
                      <input className="admin-input" value={editing.name} placeholder="如：AIO 沙箱"
                        onChange={e => patch({ name: e.target.value })} autoComplete="off" />
                    </div>
                    <div>
                      <label className="rh-label">唯一标识 ID <span className="req">*</span></label>
                      <input className="admin-input mono" value={editing.id}
                        placeholder="如：aio_sandbox" disabled={editing.native}
                        onChange={e => patch({ id: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '_') })}
                        autoComplete="off" />
                    </div>
                  </div>
                  <div className="rh-form-grid2">
                    <div>
                      <label className="rh-label">所属分类</label>
                      <select className="admin-input" value={editing.category} disabled={editing.native}
                        onChange={e => patch({ category: e.target.value })}>
                        {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="rh-label">能力简述</label>
                      <input className="admin-input" value={editing.desc} placeholder="一句话说明用途"
                        onChange={e => patch({ desc: e.target.value, note: e.target.value })}
                        autoComplete="off" />
                    </div>
                  </div>

                  <div className="rh-fieldbox">
                    <div className="rh-fieldbox-head">⚙️ 连接与配置参数
                      <button className="rh-linkbtn" onClick={() => patch({
                        config: [...editing.config, {
                          key: `field${editing.config.length + 1}`, label: '新参数', value: '',
                          env: `LOADN_SVC_${(editing.id || 'SVC').toUpperCase().replace(/-/g, '_')}_${editing.config.length + 1}` }] })}>
                        ＋ 添加字段</button>
                    </div>
                    {editing.config.length === 0 && <div className="rh-rows-empty">暂无连接参数（纯凭证驱动资源可留空）</div>}
                    {editing.config.map((c, i) => (
                      <div key={i} className="rh-row">
                        <input className="admin-input" value={c.label} placeholder="字段名"
                          onChange={e => patch({ config: editing.config.map((x, j) => j === i ? { ...x, label: e.target.value } : x) })}
                          autoComplete="off" />
                        <input className="admin-input mono" value={c.value} placeholder="值"
                          onChange={e => patch({ config: editing.config.map((x, j) => j === i ? { ...x, value: e.target.value } : x) })}
                          autoComplete="off" />
                        {editing.native
                          ? <span className="rh-chip dim" title="原生资源由平台集成消费">平台集成</span>
                          : <input className="admin-input mono" value={c.env} placeholder="ENV 名"
                            onChange={e => patch({ config: editing.config.map((x, j) => j === i ? { ...x, env: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_') } : x) })}
                            autoComplete="off" />}
                        <button className="rh-row-x" title="移除"
                          onClick={() => patch({ config: editing.config.filter((_, j) => j !== i) })}>✕</button>
                      </div>
                    ))}
                  </div>

                  <div className="rh-fieldbox">
                    <div className="rh-fieldbox-head">🔐 关联加密凭证（Vault）
                      {!editing.native && <button className="rh-linkbtn" onClick={() => patch({
                        secrets: [...editing.secrets, {
                          label: 'API Key', envKey: `LOADN_SVC_${(editing.id || 'SVC').toUpperCase().replace(/-/g, '_')}_KEY`,
                          vaultRef: `svc:${editing.id || 'x'}`, set: false }] })}>＋ 绑定密钥</button>}
                    </div>
                    {editing.secrets.length === 0 && <div className="rh-rows-empty">无需密钥</div>}
                    {editing.secrets.map((s, i) => {
                      const draftKey = `${editing.id}#${i}`;
                      const vaultOpts = editing.native ? NATIVE_SECRET_KEYS : [...new Set([
                        `svc:${editing.id}`, ...NATIVE_SECRET_KEYS])];
                      return (
                        <div key={i}>
                          <div className="rh-row secret">
                            <input className="admin-input" value={s.label} placeholder="名称"
                              onChange={e => patch({ secrets: editing.secrets.map((x, j) => j === i ? { ...x, label: e.target.value } : x) })}
                              autoComplete="off" />
                            <input className="admin-input mono" value={s.envKey}
                              placeholder="ENV_KEY" disabled={editing.native}
                              onChange={e => patch({ secrets: editing.secrets.map((x, j) => j === i ? { ...x, envKey: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_') } : x) })}
                              autoComplete="off" />
                            <select className="admin-input" value={s.vaultRef} disabled={editing.native}
                              title={`vault_ref（当前 ${s.set ? '已加密' : '未设'}）`}
                              onChange={e => patch({ secrets: editing.secrets.map((x, j) => j === i ? { ...x, vaultRef: e.target.value, set: e.target.value.startsWith('svc:') ? false : secretSet(e.target.value) } : x) })}>
                              {vaultOpts.map(v => <option key={v} value={v}>{v}</option>)}
                            </select>
                            {!editing.native && <button className="rh-row-x" title="移除"
                              onClick={() => patch({ secrets: editing.secrets.filter((_, j) => j !== i) })}>✕</button>}
                          </div>
                          <div style={{ display: 'flex', gap: 7, alignItems: 'center', marginTop: 4 }}>
                            <input className="admin-input" type="password" style={{ flex: 1 }}
                              placeholder={`${s.set ? '输入新值轮换' : '输入密钥值'}（AES-GCM 加密，只写不读）`}
                              value={secretDraft[draftKey] ?? ''}
                              onChange={e => setSecretDraft(d => ({ ...d, [draftKey]: e.target.value }))}
                              autoComplete="new-password" />
                            <button className="btn sm" disabled={!(secretDraft[draftKey] ?? '').trim()}
                              onClick={() => {
                                setSecretDraft(d => ({ ...d, [s.vaultRef]: d[draftKey], [draftKey]: '' }));
                                toast('密钥待保存（点「保存资源定义」落库）');
                              }}>暂存</button>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div>
                    <label className="rh-label">🧠 Agent 调用指南与上下文（Markdown Prompt）</label>
                    <textarea className="admin-input rh-prompt-ta" value={editing.prompt}
                      placeholder={'## 服务调用说明\n在此描述该资源向 Agent 提供的核心能力与注意事项。'}
                      onChange={e => patch({ prompt: e.target.value })} autoComplete="off" />
                  </div>
                </>
              ) : (
                <>
                  <div className="rh-mdwrap">
                    <div className="rh-mdbar">
                      <span>{(editing.id || 'resource').replace(/[^a-z0-9_-]/g, '_')}.resource.md</span>
                      <button className="rh-mdcopy" onClick={() => {
                        void navigator.clipboard.writeText(mdText).then(() => toast('已复制全文'));
                      }}>复制全文</button>
                    </div>
                    <textarea className="rh-mdta" value={mdText} spellCheck={false}
                      onChange={e => onMdInput(e.target.value)} />
                  </div>
                  <div className={`rh-parse-status ${mdOk ? 'ok' : 'bad'}`}>
                    {mdOk ? '✓ YAML Frontmatter 有效，已与表单视图实时同步' : '⚠ 解析失败：检查 --- 包裹与缩进（表单保持最后有效内容）'}
                  </div>
                </>
              )}

              {/* Harness 注入预览 */}
              <div className="rh-envbox">
                <div className="rh-envbox-head">Harness 运行时注入预览
                  <label className="rh-toggle">
                    <input type="checkbox" checked={editing.enabled} disabled={editing.native}
                      onChange={e => patch({ enabled: e.target.checked })} />
                    注入任务环境
                  </label>
                </div>
                <div className="rh-envterm">{envPreview(editing)}</div>
              </div>
            </div>

            <div className="rh-drawer-foot">
              <div className="rh-foot-l">
                {!editing.native && !isNew && <button className="btn ghost danger" onClick={() => void delDrawer()}>删除资源</button>}
                {!editing.native && editing.ping.length > 0 && (
                  <button className="btn ghost" disabled={pinging === `svc:${editing.id}`}
                    onClick={() => void doPing(editing.ping, `svc:${editing.id}`)}>
                    {pinging === `svc:${editing.id}` ? '探测中…' : '⚡ 立即探测'}</button>)}
              </div>
              <div className="rh-foot-r">
                <button className="btn ghost" onClick={closeDrawer}>取消</button>
                <button className="btn" onClick={() => void saveDrawer()}>保存资源定义</button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* ===== 导入弹窗 ===== */}
      {importOpen && (
        <div className="rh-import-mask" onClick={() => setImportOpen(false)}>
          <div className="rh-import" onClick={e => e.stopPropagation()}>
            <h3>导入 Resource.md 资源定义</h3>
            <textarea className="rh-import-ta" value={importText} spellCheck={false}
              placeholder={'---\nid: browserless\nname: 并发浏览器池\n…\n---\n\n## 调用说明'}
              onChange={e => setImportText(e.target.value)} />
            <div className="rh-import-foot">
              <button className="rh-linkbtn" onClick={() => setImportText(
                '---\nid: browserless\nname: 并发浏览器池\ncategory: 自定义服务\nicon: "🕸️"\ndescription: "无头 Chrome 集群"\nenabled: true\nconfig:\n  endpoint:\n    label: "端点"\n    value: "http://127.0.0.1:3000"\n    env: "LOADN_SVC_BROWSERLESS_URL"\nsecrets: []\n---\n\n## 无头浏览器集群\n用于批量渲染与截图抓取。')}>
                填充示例模板</button>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn ghost" onClick={() => setImportOpen(false)}>取消</button>
                <button className="btn" disabled={!importText.trim()} onClick={doImport}>解析并导入</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
