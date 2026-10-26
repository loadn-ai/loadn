// 资源 Hub（管理中心「资源」tab；2026-10-19 首建，2026-10-21 设计稿 v2 批重构）：
// 三子视图——服务与环境资源（分类分组卡片墙）/ MCP 协议端点 / 凭证库（只读概览）。
// v2 增量（设计稿对齐）：SVG 图标库彻底取代 emoji（RhIcon，存量 emoji 回退渲染）+
// 图标选择器 + 名称→ID 智能联动 + label→env 自动推导（LABEL_ENV 映射）+ 快捷
// 参数胶囊 + 蓝图预设 5 卡（高亮态）+ 内联新建加密密钥面板 + vault 下拉状态标签。
// 凭证库统一批（2026-10-26）：凭证子视图收敛只读概览（分类/备注展示+深链跳转），
// 存入/轮换写面退役——唯一管理面=安全中心凭证保险库（#/admin/security/vault）。
// v3（统一资源模型）：原生/自定义双轨合一——除主 LLM 外一切资源同构，卡片数据
// 全量来自 custom_services 条目（预置集成=后端种子条目，可编辑结构/改 ID/删除）。
// 平台消费锚由后端维持（config[].key 命中锚键时值投影进平台集成，免重启）。
// 安全语义不变：密钥值永不出后端（只写不读）；凭证不进任务 env（预览以 vault
// decrypt 形式呈现）。样式类在 index.css「资源 Hub」段（.rh-* 独立命名空间）。
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { toast } from '../stores/toasts';
import {
  Lock, Zap, Key, Shield, Plus, Tool,
  Scan, Box, Globe, Smartphone, MessageSquare, Mail, Phone, Eye,
  Search, Server, Cpu, Database, Terminal, Cloud,
} from './icons';
import { askConfirm } from '../stores/confirm';

interface ServiceItem { key: string; label: string; note: string; value: string; kind: string }
interface SecretItem { key: string; set: boolean }
interface CustomSvc {
  name: string; title?: string; url: string; note: string; category: string; icon: string; desc: string;
  enabled: boolean; prompt: string; key_set: boolean; ping: string[];
  config: { key: string; label: string; value: string; env: string }[];
  secrets: { label: string; env_key: string; vault_ref: string }[];
}
interface McpServer { name: string; spec: Record<string, any>; sessions_overriding: number; session_only?: boolean }
interface VaultPlatform { platform: string; user?: string; email?: string;
  category?: string; notes?: string; has_password: boolean; updated_at: string }
interface Overview {
  services: ServiceItem[];
  secrets: SecretItem[];
  custom: CustomSvc[];
  mcp: { servers: McpServer[] };
  vault: { platforms: VaultPlatform[]; verify: { ok: boolean; entries: number; encrypted: boolean; error?: string } };
}

/** 统一资源视图模型（v3：一切资源同构——预置与自定义无区别） */
interface RhCfgItem { key: string; label: string; value: string; env: string; svcKey?: string }
interface RhSecItem { label: string; envKey: string; vaultRef: string; set: boolean }
interface RhResource {
  id: string; name: string; title?: string; icon: string; category: string;
  desc: string; note: string; enabled: boolean; prompt: string;
  config: RhCfgItem[]; secrets: RhSecItem[]; ping: string[];
}

/* ============ SVG 图标库（设计稿 v2：线性 feather 风，彻底取代 emoji） ============ */
const RH_ICONS = {
  scan: Scan, box: Box, globe: Globe, smartphone: Smartphone, message: MessageSquare,
  mail: Mail, phone: Phone, eye: Eye, search: Search, shield: Shield,
  server: Server, cpu: Cpu, database: Database, terminal: Terminal, cloud: Cloud,
} as const;

/** 资源图标渲染：注册表键 → SVG；存量 emoji 字符串回退为文字渲染 */
function RhIcon({ icon, size = 15 }: { icon: string; size?: number }) {
  const C = (RH_ICONS as Record<string, typeof Server>)[icon];
  if (C) return <C size={size} />;
  return <span style={{ fontSize: size + 2, lineHeight: 1 }}>{icon || '🔌'}</span>;
}

/** 平台锚密钥键（后端 set_res_secret 白名单同款）：预置集成的 vault 消费锚
 *  + svc:<资源名>（本资源密钥）。任何资源都可绑定其中任意一键。 */
const ANCHOR_SECRET_KEYS = ['sandbox_api_key', 'sms_token', 'mail_auth_code', 'vlm_api_key',
  'twocaptcha_key', 'bocha_key', 'zhipu_key', 'textr_password'];
const CATEGORIES = ['基础设施', '通信', 'AI 能力', '搜索', '自定义服务'];

/** label → env 后缀映射（设计稿：改字段类型即重算注入变量名） */
const LABEL_ENV: Record<string, string> = {
  '端点': 'ENDPOINT', '地址': 'ADDRESS', '账号': 'USER', '模型': 'MODEL',
  '代理': 'PROXY', '代理地址': 'PROXY', '手机号': 'PHONE', '超时': 'TIMEOUT',
  '超时(ms)': 'TIMEOUT_MS', 'IMAP': 'IMAP', 'SMTP': 'SMTP', 'API': 'API', 'CDP': 'CDP',
};
/** 由资源 ID + 字段 label 推导注入环境变量名（LOADN_<ID>_<SUFFIX>） */
const envOf = (id: string, label: string) => {
  const up = (id || 'SVC').toUpperCase().replace(/[^A-Z0-9]/g, '_');
  const suf = LABEL_ENV[label.trim()] ?? label.trim().toUpperCase().replace(/[^A-Z0-9]/g, '_');
  return `LOADN_${up}_${suf || 'FIELD'}`;
};
/** 快捷参数胶囊（设计稿：常用字段一键插入） */
const QUICK_CHIPS: [string, string][] = [
  ['端点', 'http://127.0.0.1:8080'], ['代理', 'http://127.0.0.1:7890'],
  ['模型', 'gpt-4o-mini'], ['账号', 'agent@example.com'], ['超时(ms)', '30000'],
];

/** 架构蓝图预设（设计稿 5 卡；secrets 走 svc:<id> 本资源密钥体系） */
const PRESETS: {
  k: string; t: string; s: string; icon: string;
  apply: Partial<RhResource>;
}[] = [
  {
    k: 'http', t: 'HTTP 微服务', s: 'REST / 内部端点', icon: 'globe',
    apply: {
      id: 'jina_reader', name: 'Jina 网页提取', category: '自定义服务', icon: 'globe',
      desc: '网页 URL 转纯净 Markdown 服务',
      config: [
        { key: 'endpoint', label: '端点', value: 'https://r.jina.ai', env: 'LOADN_JINA_READER_ENDPOINT' },
        { key: 'timeout', label: '超时(ms)', value: '15000', env: 'LOADN_JINA_READER_TIMEOUT_MS' },
      ],
      secrets: [],
      prompt: '## Jina 网页正文提取\n当需要读取外部网页正文时，直接拼接 URL 请求 `$LOADN_JINA_READER_ENDPOINT/<目标网址>`。',
    },
  },
  {
    k: 'mail', t: '邮箱收发', s: 'IMAP / SMTP', icon: 'mail',
    apply: {
      id: 'work_mailbox', name: '工作邮箱节点', category: '通信', icon: 'mail',
      desc: 'IMAP 收信与 SMTP 发信通道',
      config: [
        { key: 'imap', label: 'IMAP', value: 'imap.exmail.qq.com:993', env: 'LOADN_WORK_MAILBOX_IMAP' },
        { key: 'smtp', label: 'SMTP', value: 'smtp.exmail.qq.com:465', env: 'LOADN_WORK_MAILBOX_SMTP' },
        { key: 'account', label: '账号', value: 'bot@company.com', env: 'LOADN_WORK_MAILBOX_USER' },
      ],
      secrets: [{ label: '授权码', envKey: 'LOADN_WORKMAIL_PASS', vaultRef: 'svc:work_mailbox', set: false }],
      prompt: '## 企业邮箱收发通道\n支持通过 IMAP 检索最新验证码邮件，或通过 SMTP 发送任务报告。',
    },
  },
  {
    k: 'llm', t: 'AI 模型网关', s: 'BaseURL + Model', icon: 'cpu',
    apply: {
      id: 'rerank_model', name: '语义重排模型', category: 'AI 能力', icon: 'cpu',
      desc: 'BGE-Reranker 向量检索精排接口',
      config: [
        { key: 'api_base', label: 'API', value: 'https://api.siliconflow.cn/v1/rerank', env: 'LOADN_RERANK_MODEL_API' },
        { key: 'model', label: '模型', value: 'BAAI/bge-reranker-v2-m3', env: 'LOADN_RERANK_MODEL_MODEL' },
      ],
      secrets: [{ label: 'API Key', envKey: 'LOADN_RERANK_API_KEY', vaultRef: 'svc:rerank_model', set: false }],
      prompt: '## 语义重排模型 (Reranker)\n在 RAG 召回候选文档后调用该接口进行相关性精排。',
    },
  },
  {
    k: 'adb', t: '物理真机节点', s: 'ADB / 串口设备', icon: 'smartphone',
    apply: {
      id: 'android_node_02', name: '备用测试真机', category: '基础设施', icon: 'smartphone',
      desc: '局域网 ADB 自动化测试节点',
      config: [{ key: 'address', label: '地址', value: '192.168.0.82:5555', env: 'LOADN_ANDROID_NODE_02_ADDRESS' }],
      secrets: [],
      prompt: '## 备用 Android 真机\n通过 `adb -s $LOADN_ANDROID_NODE_02_ADDRESS` 执行移动端 UI 自动化指令。',
    },
  },
  {
    k: 'blank', t: '空白自定义', s: '自由定义键值', icon: 'plus',
    apply: {
      id: 'custom_env', name: '', category: '自定义服务', icon: 'server',
      desc: '', config: [], secrets: [],
      prompt: '## 自定义环境资源\n在此补充调用说明。',
    },
  },
];

type Section = 'service' | 'mcp' | 'vault';
type PingRes = Record<string, { ok: boolean; msg: string; ms?: number }>;

/** Resource.md 生成（frontmatter + prompt 正文）——与后端 v2 字段一一对应 */
function genResourceMd(r: RhResource): string {
  const q = (s: string) => `"${String(s ?? '').replace(/"/g, '\\"')}"`;
  let y = '---\n'
    + `id: ${r.id}\nname: ${q(r.name)}\ncategory: ${q(r.category)}\n`
    + `icon: ${q(r.icon || 'server')}\ndescription: ${q(r.desc || r.note || '')}\n`
    + `enabled: ${r.enabled}\n`;
  if (r.ping.length && !(r.ping.length === 1 && r.ping[0] === `svc:${r.id}`)) {
    y += `ping: ${q(r.ping.join(', '))}\n`;      // 探测目标（缺省 svc:自身不写）
  }
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
    name: scalar.name || scalar.id || '新资源',
    icon: scalar.icon || 'server',
    category: CATEGORIES.includes(scalar.category) ? scalar.category : (scalar.category || '自定义服务'),
    desc: scalar.description || '', note: scalar.description || '',
    enabled: scalar.enabled !== 'false',
    ping: (scalar.ping || '').split(',').map(s => s.trim()).filter(Boolean),
    prompt: m[2].trim(), config, secrets,
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
  // 抽屉态（origId：编辑前的条目名——保存时 id 变化走 rename 迁移）
  const [origId, setOrigId] = useState('');
  const [editing, setEditing] = useState<RhResource | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [mode, setMode] = useState<'form' | 'md'>('form');
  const [mdText, setMdText] = useState('');
  const [mdOk, setMdOk] = useState(true);
  const [secretDraft, setSecretDraft] = useState<Record<string, string>>({});
  const [iconPickerOpen, setIconPickerOpen] = useState(false);
  const [idTouched, setIdTouched] = useState(false);
  const [presetSel, setPresetSel] = useState('http');
  const [inlineVaultOpen, setInlineVaultOpen] = useState(false);
  const [ivLabel, setIvLabel] = useState('');
  const [ivEnv, setIvEnv] = useState('');
  const [ivVal, setIvVal] = useState('');
  const pickerRef = useRef<HTMLDivElement | null>(null);
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
  // 图标选择器：外部点击关闭（capture 阶段，避免先 onBlur 后失灵）
  useEffect(() => {
    if (!iconPickerOpen) return;
    const h = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setIconPickerOpen(false);
    };
    document.addEventListener('mousedown', h, true);
    return () => document.removeEventListener('mousedown', h, true);
  }, [iconPickerOpen]);

  if (!data) return loadErr ? (
    <div className="admin-body">
      <div className="admin-msg err">{loadErr}
        <button className="link" style={{ marginLeft: 8 }} onClick={() => void load()}>重试</button>
      </div>
    </div>
  ) : <div className="admin-body muted">加载中…</div>;

  const secretSet = (k: string) => data.secrets.find(s => s.key === k)?.set ?? false;

  /** 全部资源 → 统一视图合集（v3：预置+自定义同为 custom 条目，量级 ~20 直接算） */
  const all: RhResource[] = data.custom.map(c => ({
    id: c.name, name: c.title || c.name, title: c.title || '', icon: c.icon || 'server',
    category: CATEGORIES.includes(c.category) ? c.category : (c.category || '自定义服务'),
    desc: c.desc || c.note || '', note: c.note || '',
    enabled: c.enabled !== false, prompt: c.prompt || '',
    ping: (c.ping && c.ping.length) ? c.ping : [`svc:${c.name}`],
    config: (c.config || []).map(x => ({ ...x })),
    secrets: (c.secrets || []).map(s => ({
      label: s.label, envKey: s.env_key, vaultRef: s.vault_ref,
      set: s.vault_ref.startsWith('svc:')
        ? (s.vault_ref === `svc:${c.name}` ? c.key_set
           : (data.custom.find(o => o.name === s.vault_ref.slice(4))?.key_set ?? false))
        : secretSet(s.vault_ref),
    })),
  }));

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
  const pingCard = (r: RhResource) => void doPing(r.ping, r.id);
  const pingAll = () => void doPing(all.flatMap(r => r.ping), '__all__');

  /** 卡片状态徽章（本次探测优先→历史回退；附 24h 败数） */
  const badgeOf = (r: RhResource) => {
    if (!r.ping.length) return <span className="rh-badge untested">—</span>;
    if (pinging === r.id) return <span className="rh-badge testing">探测中…</span>;
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
    setIconPickerOpen(false); setIdTouched(false); setPresetSel('http');
    setInlineVaultOpen(false); setOrigId('');
    setEditing({
      id: '', name: '', icon: 'server', category: '自定义服务',
      desc: '', note: '', enabled: true, prompt: '', config: [], secrets: [], ping: [],
      ...preset,
    });
  };
  const openEdit = (r: RhResource) => {
    setIsNew(false); setMode('form'); setSecretDraft({});
    setIconPickerOpen(false); setIdTouched(true); setInlineVaultOpen(false);
    setOrigId(r.id);
    setEditing(JSON.parse(JSON.stringify(r)));
  };
  const closeDrawer = () => { setEditing(null); setMdText(''); setIconPickerOpen(false); };
  const switchMode = (m: 'form' | 'md') => {
    if (!editing) return;
    if (m === 'md') { setMdText(genResourceMd(editing)); setMdOk(true); }
    setMode(m);
  };
  const onMdInput = (text: string) => {
    setMdText(text);
    const parsed = parseResourceMd(text);
    if (parsed) { setEditing(parsed); setMdOk(true); }
    else setMdOk(false);
  };
  const patch = (p: Partial<RhResource>) => setEditing(e => e ? { ...e, ...p } : e);

  /** 名称输入：新增态且用户未手改 ID 时，ascii 化智能联动生成 slug */
  const onNameInput = (v: string) => {
    if (!editing) return;
    const p: Partial<RhResource> = { name: v };
    if (isNew && !idTouched) {
      const a = v.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
      if (a.length >= 2) p.id = a;
    }
    patch(p);
  };
  /** 字段 label 变更：env 为空或仍是旧 label 的推导值时自动重算（手改优先） */
  const onCfgLabel = (i: number, label: string) => {
    if (!editing) return;
    setEditing(e => {
      if (!e) return e;
      const cfg = e.config.map((c, j) => {
        if (j !== i) return c;
        const wasAuto = !c.env || c.env === envOf(e.id, c.label);
        const env = wasAuto ? envOf(e.id, label) : c.env;
        const suf = LABEL_ENV[label.trim()] ?? label.trim().toUpperCase().replace(/[^A-Z0-9]/g, '_');
        return { ...c, label, env, key: (suf || `field${j + 1}`).toLowerCase() };
      });
      return { ...e, config: cfg };
    });
  };
  /** 快捷胶囊/追加字段：按资源 ID 推导 env 名 */
  const addCfg = (label: string, value: string) => {
    if (!editing) return;
    const env = envOf(editing.id, label);
    const key = env.split('_').slice(2).join('_').toLowerCase() || `field${editing.config.length + 1}`;
    patch({ config: [...editing.config, { key, label, value, env }] });
  };
  const applyPreset = (k: string) => {
    const p = PRESETS.find(x => x.k === k);
    if (!p || !editing) return;
    setPresetSel(k);
    if (k === 'blank') {
      patch({ id: 'custom_env', name: '', category: '自定义服务', icon: 'server',
        desc: '', config: [], secrets: [], prompt: '## 自定义环境资源\n在此补充调用说明。' });
    } else {
      patch({ ...p.apply, note: p.apply.desc ?? '' });
    }
    toast('已套用架构蓝图，可继续按需微调');
  };
  /** 内联新建密钥（设计稿：标签/env 名/明文 → svc:<资源id> 加密绑定，保存时落库） */
  const openInlineVault = () => {
    if (!editing) return;
    setIvLabel('API Key');
    setIvEnv(`LOADN_${(editing.id || 'SVC').toUpperCase().replace(/[^A-Z0-9]/g, '_')}_API_KEY`);
    setIvVal('');
    setInlineVaultOpen(true);
  };
  const saveInlineVault = () => {
    if (!editing) return;
    const label = ivLabel.trim() || 'API Key';
    const envKey = (ivEnv.trim() || 'LOADN_API_KEY').toUpperCase();
    if (!ivVal.trim()) { toast('请输入需要加密存储的密钥明文', false); return; }
    const ref = `svc:${editing.id || 'custom'}`;
    setSecretDraft(d => ({ ...d, [ref]: ivVal.trim() }));
    patch({ secrets: [...editing.secrets, { label, envKey, vaultRef: ref, set: false }] });
    setInlineVaultOpen(false);
    toast(`密钥已暂存（AES-GCM，点「保存资源定义」加密落库并绑定）`);
  };

  /** vault 引用选项与状态标签（svc: 前缀=本资源密钥；原生键=平台密钥） */
  const vaultOptLabel = (ref: string) => ref.startsWith('svc:')
    ? `${ref}（本资源密钥）`
    : `${ref}（${secretSet(ref) ? '已加密' : '未设'}）`;
  const vaultRefSet = (ref: string, curId: string) => {
    if (ref.startsWith('svc:')) {
      const nm = ref.slice(4);
      return nm === curId
        ? !!secretDraft[ref].trim() || (data.custom.find(c => c.name === nm)?.key_set ?? false)
        : data.custom.find(c => c.name === nm)?.key_set ?? false;
    }
    return secretSet(ref);
  };

  /** 保存（统一走 custom v3 API）：所有资源同构；ID 变化走 rename_from
   *  （后端迁移条目+vault svc: 密钥）；密钥草稿按 vault_ref 落库 */
  const saveDrawer = async () => {
    if (!editing) return;
    if (!editing.id.trim() || !editing.name.trim()) { toast('资源名称与唯一 ID 必填', false); return; }
    if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(editing.id)) {
      toast('ID 需为小写 slug（字母/数字开头，可含 - _，≤32 位）', false); return;
    }
    try {
      const renamed = !isNew && origId && origId !== editing.id;
      if (renamed && !await askConfirm({
        title: `资源 ID 「${origId}」→「${editing.id}」？`, body: '条目与其 svc: 密钥将随行迁移。', danger: true,
      })) return;
      await api('/api/admin/resources/custom', {
        method: 'POST',
        body: JSON.stringify({
          name: editing.id, title: editing.name,
          ...(renamed ? { rename_from: origId } : {}),
          url: editing.config.find(c =>
            c.value.startsWith('http://') || c.value.startsWith('https://'))?.value || '',
          note: editing.note || editing.desc, category: editing.category,
          icon: editing.icon, desc: editing.desc, enabled: editing.enabled,
          prompt: editing.prompt, ping: editing.ping,
          config: editing.config.map(c => ({
            key: c.key || c.env.toLowerCase(), label: c.label, value: c.value, env: c.env })),
          secrets: editing.secrets.map(s => ({
            label: s.label, env_key: s.envKey, vault_ref: s.vaultRef })),
        }),
      });
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
    if (!editing) return;
    if (!await askConfirm({
      title: `删除资源「${editing.name}」？（svc: 密钥一并清除）`,
      body: '若是预置集成卡：平台集成（如搜索/OCR）继续用既有配置运行，卡片可随时重建恢复管理面。',
      danger: true })) return;
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

  /** Harness 注入预览（统一语义：config→env 注入；密钥恒为 vault 解密形式，
   *  不进任务 env——预置集成的锚字段同样按 env 注入，参数对 agent 可见） */
  const envPreview = (r: RhResource) => {
    if (!r.enabled) return <><span className="c"># 该资源当前已停用（不注入）</span></>;
    if (!r.config.length && !r.secrets.length) return <><span className="c"># 暂无可注入字段</span></>;
    return <>
      {r.config.length > 0 && <><span className="c"># 1. Harness 注入的环境变量（下一 turn 起）</span><br /></>}
      {r.config.map((c, i) => (<span key={i}>
        <span className="k">export</span> <span className="v">{c.env}</span>=&quot;{c.value}&quot;<br />
      </span>))}
      {r.secrets.length > 0 && <><span className="c"># 2. 密钥：平台保险库解密供集成调用（不进任务 env；agent 经 vault 命令现取）</span><br /></>}
      {r.secrets.map((s, i) => (<span key={i}>
        <span className="k">export</span> <span className="s">{s.envKey}</span>=<span className="s">&quot;$(vault decrypt {s.vaultRef})&quot;</span><br />
      </span>))}
    </>;
  };

  const vFiltered = data.vault.platforms.filter(p =>
    !vSearch || p.platform.toLowerCase().includes(vSearch.toLowerCase())
    || (p.user || '').toLowerCase().includes(vSearch.toLowerCase())
    || (p.email || '').toLowerCase().includes(vSearch.toLowerCase())
    || (p.category || '').toLowerCase().includes(vSearch.toLowerCase())
    || (p.notes || '').toLowerCase().includes(vSearch.toLowerCase()));

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
              {[...new Set(['all', ...CATEGORIES,
                ...all.map(r => r.category).filter(c => !CATEGORIES.includes(c))])].map(c => (
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

          {/* 动态分类集合：预置分类顺序优先，自由输入的自定义分类追尾 */}
          {[...new Set([...CATEGORIES.filter(c => all.some(r => r.category === c)),
            ...all.map(r => r.category).filter(c => !CATEGORIES.includes(c))])].map(cat => {
            if (catFilter !== 'all' && catFilter !== cat) return null;
            const cards = shown.filter(r => r.category === cat);
            if (!cards.length) return null;
            return (
              <div key={cat}>
                <div className="rh-group-title">{cat}<span className="rh-count">({cards.length})</span></div>
                <div className="rh-grid">
                  {cards.length === 0 && cat === '自定义服务' && (
                    <div className="rh-empty" style={{ gridColumn: '1 / -1' }} onClick={() => openCreate()}>
                      <div className="rh-empty-l">
                        <div className="rh-empty-plus"><Plus size={16} /></div>
                        <div>
                          <div className="rh-empty-t">新增自定义外部环境或导入 Resource.md</div>
                          <div className="rh-empty-s">支持选择架构蓝图、动态配置参数、内联创建并加密绑定 Vault 密钥</div>
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
                              <span className="rh-icon"><RhIcon icon={r.icon} /></span>
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
                                <Key size={11} /> {s.label} {s.set ? '已加密' : '未设'}</span>
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
            <div className="rh-mcp-hero-id">
              <span className="rh-mcp-hero-ico"><Tool size={18} /></span>
              <div>
                <div className="rh-mcp-hero-t">MCP (Model Context Protocol) 与 Resource.md 协同工作</div>
                <div className="rh-mcp-hero-s">
                  MCP 提供标准化的工具 Schema 暴露；「服务与环境资源」通过 <code>Resource.md</code>
                  为 Harness 提供环境变量注入、加密凭证绑定与非标基础设施的调用上下文。
                </div>
              </div>
            </div>
            <button className="btn" onClick={() => { setSec('service'); openCreate(); }}>新增环境资源</button>
          </div>
          <div className="res-addbar focused" style={{ margin: '10px 0', cursor: 'pointer' }}
            onClick={() => { location.hash = '#/admin/tools'; }}>
            <span>MCP server 的增删改收敛在「工具」tab（stdio/http/sse 全类型编辑器 + 会话覆盖计数）</span>
            <button className="btn ghost" style={{ marginLeft: 'auto' }}>前往工具 tab 管理 →</button>
          </div>
          <div className="rh-mcp-grid">
            {data.mcp.servers.length === 0 && (
              <div className="rh-empty" style={{ gridColumn: '1 / -1' }} onClick={() => { location.hash = '#/admin/tools'; }}>
                <div className="rh-empty-l">
                  <div className="rh-empty-plus"><Tool size={16} /></div>
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
                      <span className="rh-icon"><RhIcon icon="terminal" /></span>
                      <span className="rh-name">{m.name}</span>
                    </div>
                    <span className={`rh-chip ${m.session_only ? 'warn' : 'ok'}`}>
                      {m.session_only ? '会话级' : '运行中'} ({kind})</span>
                  </div>
                  <div className="rh-desc">会话覆盖 {m.sessions_overriding || 0} 个</div>
                  <div className="rh-mcp-cmd" title={cmd}>{cmd}</div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* ============ 子视图 3：凭证库（只读概览；唯一管理面=安全 tab 凭证保险库） ============ */}
      {sec === 'vault' && (
        <>
          <div className="rh-toolbar">
            <input className="rh-search admin-input" placeholder="搜索平台 / 账号 / 备注…"
              value={vSearch} onChange={e => setVSearch(e.target.value)} autoComplete="off" />
            <span className="rh-subnav-note" style={{ marginLeft: 'auto' }}>
              <Shield size={13} /> {data.vault.verify.ok
                ? `加密库健康 · ${data.vault.verify.entries} 条 · AES-GCM`
                : `⚠ 校验失败：${data.vault.verify.error ?? '未知'}`}
            </span>
            <button className="btn" onClick={() => { location.hash = '#/admin/security/vault'; }}>
              <Plus size={13} /> 新增 / 编辑凭证</button>
          </div>
          <div className="rh-subnav-note" style={{ marginBottom: 10 }}>
            凭证库唯一管理面在安全中心（新增/编辑/分类/备注/删除都在那里）；此处为绑定视角概览，
            资源卡抽屉里仍可内联创建并加密绑定 svc: 密钥。</div>

          <div className="rh-vault-list">
            {vFiltered.length === 0 && <div className="rh-vault-row">
              <span className="rh-rows-empty" style={{ width: '100%' }}>（无匹配条目）</span>
            </div>}
            {vFiltered.map(p => (
              <div key={p.platform} className="rh-vault-row">
                <div className="rh-vault-id">
                  <div className={`rh-vault-ico ${p.has_password ? '' : 'warn'}`}>
                    {p.has_password ? <Lock size={13} /> : <Key size={13} />}</div>
                  <div>
                    <div className="rh-vault-name">{p.platform}
                      {p.category?.trim() && <span className="rh-id-badge" style={{ marginLeft: 8 }}>
                        {p.category}</span>}
                      <span className="rh-id-badge" style={{ marginLeft: 8 }}>
                        {p.user || p.email || '—'}</span></div>
                    <div className="rh-vault-sub">
                      {p.notes?.trim() ? `${p.notes} · ` : ''}更新 {(p.updated_at || '').slice(0, 10)}</div>
                  </div>
                </div>
                <div className="rh-vault-ops">
                  <span className={`rh-chip ${p.has_password ? 'ok' : 'warn'}`}>
                    <Key size={11} /> {p.has_password ? '密码已存' : '密码缺失'}</span>
                  <button className="btn ghost sm"
                    onClick={() => { location.hash = '#/admin/security/vault'; }}>管理 →</button>
                </div>
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
                <span className="rh-icon" style={{ width: 36, height: 36 }}>
                  <RhIcon icon={editing.icon} size={18} /></span>
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
                    onClick={() => switchMode('form')}>可视化配置</button>
                  <button className={`rh-mode-btn ${mode === 'md' ? 'on' : ''}`}
                    onClick={() => switchMode('md')}>Resource.md</button>
                </div>
                <button className="btn ghost sm" onClick={closeDrawer}>✕</button>
              </div>
            </div>

            {isNew && (
              <div className="rh-preset-wrap">
                <label className="rh-preset-label">选择资源架构蓝图（自动生成标准字段与环境变量映射）</label>
                <div className="rh-preset-grid">
                  {PRESETS.map(p => (
                    <button key={p.k} type="button"
                      className={`rh-preset-card ${presetSel === p.k ? 'on' : ''}`}
                      onClick={() => applyPreset(p.k)}>
                      {p.icon === 'plus' ? <Plus size={16} /> : <RhIcon icon={p.icon} size={16} />}
                      <span className="rh-preset-t">{p.t}</span>
                      <span className="rh-preset-s">{p.s}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="rh-drawer-body">
              {mode === 'form' ? (
                <>
                  <div className="rh-form-grid">
                    <div className="rh-iconfield" ref={pickerRef}>
                      <label className="rh-label">图标</label>
                      <button type="button" className="rh-iconpicker-trigger"
                        title="选择图标"
                        onClick={() => setIconPickerOpen(v => !v)}>
                        <RhIcon icon={editing.icon} size={16} />
                      </button>
                      {iconPickerOpen && (
                        <div className="rh-iconpicker">
                          {Object.keys(RH_ICONS).map(k => (
                            <button key={k} type="button" title={k}
                              className={`rh-iconpick-btn ${editing.icon === k ? 'on' : ''}`}
                              onClick={() => { patch({ icon: k }); setIconPickerOpen(false); }}>
                              <RhIcon icon={k} size={16} />
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <div>
                      <label className="rh-label">资源名称 <span className="req">*</span></label>
                      <input className="admin-input" value={editing.name} placeholder="如：AIO 沙箱 / 内部知识库"
                        onChange={e => onNameInput(e.target.value)} autoComplete="off" />
                    </div>
                    <div>
                      <label className="rh-label">
                        唯一标识 ID <span className="req">*</span>
                        <span className="rh-label-hint">小写字母与下划线{isNew && !idTouched ? ' · 输入名称自动生成' : ''}{!isNew && origId ? ' · 修改即改名（svc: 密钥随行迁移）' : ''}</span>
                      </label>
                      <input className="admin-input mono" value={editing.id}
                        placeholder="如：aio_sandbox"
                        onChange={e => {
                          setIdTouched(true);
                          patch({ id: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '_') });
                        }}
                        autoComplete="off" />
                    </div>
                  </div>
                  <div className="rh-form-grid2">
                    <div>
                      <label className="rh-label">所属分类（可自由输入）</label>
                      <input className="admin-input" value={editing.category} list="rh-cat-list"
                        placeholder="基础设施 / 通信 / AI 能力 / 自定义服务…"
                        onChange={e => patch({ category: e.target.value })}
                        autoComplete="off" />
                      <datalist id="rh-cat-list">
                        {CATEGORIES.map(c => <option key={c} value={c} />)}
                      </datalist>
                    </div>
                    <div>
                      <label className="rh-label">能力简述（显示在卡片副标题）</label>
                      <input className="admin-input" value={editing.desc} placeholder="一句话说明该外部环境向 Agent 提供的能力…"
                        onChange={e => patch({ desc: e.target.value, note: e.target.value })}
                        autoComplete="off" />
                    </div>
                  </div>

                  <div className="rh-fieldbox">
                    <div className="rh-fieldbox-head">
                      <span>连接与环境参数（Config Fields）
                        <button className="rh-linkbtn" style={{ marginLeft: 10 }}
                          onClick={() => addCfg('', '')}>＋ 添加字段</button></span>
                      <span className="rh-fieldbox-sub">定义连接地址、端口或模型档位，自动映射为 Harness 环境变量；字段存取键命中平台锚键时同步驱动平台集成</span>
                    </div>
                    <div className="rh-quickrow">
                      <span className="rh-quick-lb">常用快速插入:</span>
                      {QUICK_CHIPS.map(([lb, dv]) => (
                        <button key={lb} type="button" className="rh-quick-chip"
                          onClick={() => addCfg(lb, dv)}>＋ {lb}</button>
                      ))}
                    </div>
                    {editing.config.length > 0 && (
                      <div className="rh-rowhead">
                        <span>显示标签</span><span>参数值</span>
                        <span>注入环境变量名 (Env)</span><span />
                      </div>
                    )}
                    {editing.config.length === 0 && <div className="rh-rows-empty">暂无连接参数（可点击上方快捷胶囊或「＋ 添加字段」）</div>}
                    {editing.config.map((c, i) => (
                      <div key={i} className="rh-row">
                        <input className="admin-input" value={c.label} placeholder="如：端点"
                          onChange={e => onCfgLabel(i, e.target.value)} autoComplete="off" />
                        <input className="admin-input mono" value={c.value} placeholder="http://127.0.0.1:8080"
                          onChange={e => patch({ config: editing.config.map((x, j) => j === i ? { ...x, value: e.target.value } : x) })}
                          autoComplete="off" />
                        <input className="admin-input mono env" value={c.env} placeholder="LOADN_SVC_URL"
                          title={c.key ? `存取键：${c.key}` : undefined}
                          onChange={e => patch({ config: editing.config.map((x, j) => j === i ? { ...x, env: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_') } : x) })}
                          autoComplete="off" />
                        <button className="rh-row-x" title="移除"
                          onClick={() => patch({ config: editing.config.filter((_, j) => j !== i) })}>✕</button>
                      </div>
                    ))}
                  </div>

                  <div className="rh-fieldbox">
                    <div className="rh-fieldbox-head">
                      <span>安全凭证与密钥（Vault Secrets）</span>
                      <span className="rh-fieldbox-ops">
                        <button className="rh-linkbtn green" onClick={() => {
                          if (!inlineVaultOpen) openInlineVault(); else setInlineVaultOpen(false);
                        }}>＋ 新建加密密钥</button>
                        <button className="rh-linkbtn" onClick={() => patch({
                          secrets: [...editing.secrets, {
                            label: 'API Key', envKey: `LOADN_${(editing.id || 'SVC').toUpperCase().replace(/[^A-Z0-9]/g, '_')}_API_KEY`,
                            vaultRef: `svc:${editing.id || 'x'}`, set: false }] })}>＋ 绑定已有密钥</button>
                      </span>
                    </div>

                    {inlineVaultOpen && (
                      <div className="rh-inlinevault">
                        <div className="rh-inlinevault-t">
                          <span><Shield size={13} /> 创建新密钥并自动绑定到当前资源</span>
                          <button className="rh-linkbtn" onClick={() => setInlineVaultOpen(false)}>收起</button>
                        </div>
                        <div className="rh-inlinevault-grid">
                          <div>
                            <label className="rh-label">凭证显示标签</label>
                            <input className="admin-input" value={ivLabel} placeholder="如：API Key / 授权码"
                              onChange={e => setIvLabel(e.target.value)} autoComplete="off" />
                          </div>
                          <div>
                            <label className="rh-label">注入环境变量名</label>
                            <input className="admin-input mono" value={ivEnv} placeholder="LOADN_SVC_API_KEY"
                              onChange={e => setIvEnv(e.target.value)} autoComplete="off" />
                          </div>
                          <div>
                            <label className="rh-label">密钥明文（AES-GCM 加密）</label>
                            <input className="admin-input mono" type="password" value={ivVal} placeholder="输入 Token 或密码…"
                              onChange={e => setIvVal(e.target.value)} autoComplete="new-password" />
                          </div>
                        </div>
                        <div className="rh-inlinevault-foot">
                          <span className="rh-inlinevault-hint mono">
                            将以 vault_ref <code>svc:{editing.id || 'custom'}</code> 加密存入凭证库</span>
                          <button className="btn" onClick={saveInlineVault}>加密存入并绑定</button>
                        </div>
                      </div>
                    )}

                    {editing.secrets.length > 0 && (
                      <div className="rh-rowhead secret">
                        <span>凭证标签</span><span>注入环境变量</span><span>引用 Vault 加密项（vault_ref）</span><span />
                      </div>
                    )}
                    {editing.secrets.length === 0 && <div className="rh-rows-empty">当前免密访问，如需鉴权请「＋ 新建加密密钥」或「＋ 绑定已有密钥」</div>}
                    {editing.secrets.map((s, i) => (
                      <div key={i}>
                        <div className="rh-row secret">
                          <input className="admin-input" value={s.label} placeholder="名称"
                            onChange={e => patch({ secrets: editing.secrets.map((x, j) => j === i ? { ...x, label: e.target.value } : x) })}
                            autoComplete="off" />
                          <input className="admin-input mono" value={s.envKey}
                            placeholder="ENV_KEY"
                            onChange={e => patch({ secrets: editing.secrets.map((x, j) => j === i ? { ...x, envKey: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_') } : x) })}
                            autoComplete="off" />
                          <select className="admin-input" value={s.vaultRef}
                            title={`vault_ref（当前 ${s.set ? '已加密' : '未设'}）`}
                            onChange={e => patch({ secrets: editing.secrets.map((x, j) => j === i ? {
                              ...x, vaultRef: e.target.value,
                              set: e.target.value.startsWith('svc:')
                                ? vaultRefSet(e.target.value, editing.id)
                                : secretSet(e.target.value) } : x) })}>
                            {[...new Set([`svc:${editing.id || 'x'}`, ...ANCHOR_SECRET_KEYS, s.vaultRef])]
                              .filter(v => v.startsWith('svc:') || ANCHOR_SECRET_KEYS.includes(v))
                              .map(v => <option key={v} value={v}>{vaultOptLabel(v)}</option>)}
                          </select>
                          <button className="rh-row-x" title="解绑密钥"
                            onClick={() => patch({ secrets: editing.secrets.filter((_, j) => j !== i) })}>✕</button>
                        </div>
                        <div style={{ display: 'flex', gap: 7, alignItems: 'center', marginTop: 4 }}>
                          <input className="admin-input" type="password" style={{ flex: 1 }}
                            placeholder={`${s.set ? '输入新值轮换' : '输入密钥值'}（AES-GCM 加密，只写不读；点「保存资源定义」落库）`}
                            value={secretDraft[s.vaultRef] ?? ''}
                            onChange={e => setSecretDraft(d => ({ ...d, [s.vaultRef]: e.target.value }))}
                            autoComplete="new-password" />
                        </div>
                      </div>
                    ))}
                  </div>

                  <div>
                    <label className="rh-label">连通性探测目标（可选）
                      <span className="rh-label-hint">逗号分隔；预置锚名（ocr / bocha / zhipu / sms…）或 svc:资源名，留空=探测自身</span></label>
                    <input className="admin-input mono" value={editing.ping.join(', ')}
                      placeholder="如：bocha, zhipu（留空=svc:自身）"
                      onChange={e => patch({
                        ping: e.target.value.split(',').map(t => t.trim()).filter(Boolean) })}
                      autoComplete="off" />
                  </div>

                  <div>
                    <label className="rh-label">Agent 调用指南与上下文（Markdown Prompt）
                      <span className="rh-label-hint">按需注入 Model System Prompt</span></label>
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
                      }}>复制源码</button>
                    </div>
                    <textarea className="rh-mdta" value={mdText} spellCheck={false}
                      onChange={e => onMdInput(e.target.value)} />
                  </div>
                  <div className={`rh-parse-status ${mdOk ? 'ok' : 'bad'}`}>
                    {mdOk ? '✓ YAML Frontmatter 有效，已与可视化配置视图实时同步' : '⚠ 解析失败：检查 --- 包裹与缩进（表单保持最后有效内容）'}
                  </div>
                </>
              )}

              {/* Harness 注入预览 */}
              <div className="rh-envbox">
                <div className="rh-envbox-head">Harness 运行时注入预览
                  <label className="rh-toggle">
                    <input type="checkbox" checked={editing.enabled}
                      onChange={e => patch({ enabled: e.target.checked })} />
                    启用并注入任务环境
                  </label>
                </div>
                <div className="rh-envterm">{envPreview(editing)}</div>
              </div>
            </div>

            <div className="rh-drawer-foot">
              <div className="rh-foot-l">
                {!isNew && <button className="btn ghost danger" onClick={() => void delDrawer()}>删除资源</button>}
                {editing.ping.length > 0 && (
                  <button className="btn ghost" disabled={pinging === editing.id}
                    onClick={() => void doPing(editing.ping, editing.id)}>
                    {pinging === editing.id ? '探测中…' : <><Zap size={13} style={{ verticalAlign: -2 }} /> 连通性探测</>}</button>)}
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
                '---\nid: browserless\nname: 并发浏览器池\ncategory: 自定义服务\nicon: "cloud"\ndescription: "无头 Chrome 集群"\nenabled: true\nconfig:\n  endpoint:\n    label: "端点"\n    value: "http://127.0.0.1:3000"\n    env: "LOADN_SVC_BROWSERLESS_URL"\nsecrets: []\n---\n\n## 无头浏览器集群\n用于批量渲染与截图抓取。')}>
                填充标准示例模板</button>
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

