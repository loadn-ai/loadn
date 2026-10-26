// 安全中心（管理中心「安全」tab）：产品视角的 W0-W6 姿态呈现。
// 交互模型：每张姿态卡是入口（master）——点开下方详情面板（detail），
// 详情按卡各自取数：沙箱=近期任务隔离记录 / 审批=待审清单 / 出口=域聚合 /
// vault=条目+完整性 / 账本=跳到事件流 / 蜜罐=锁定会话可解锁。
import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import { useStore } from '../stores/sessions';
import { Box, Lock, Crosshair, Globe, Key, FileDoc, Flask, Shield } from './icons';
import { askConfirm } from '../stores/confirm';
import { toast } from '../stores/toasts';

interface Posture {
  sandbox: { mode: string; requested?: string; effective?: string; reason?: string;
    bwrap: number; direct: number; window: number };
  policy: { approval_enforce: string };
  ops?: OpsConfig;
  egress: { mode: string; on_deny?: string; ask_wait_s?: number; allow_count: number };
  canary: { locked_sessions: { sid: string; reason: string }[]; kill_all: boolean };
  vault: { platforms: number };
  audit: { last_id: number; last_ts: string; anchors: number };
}

/** 档位枚举 → 展示（与后端 config.SANDBOX_TIERS 对齐） */
const TIER_ZH: Record<string, string> = {
  'off': '降级档（直跑）',
  'bwrap': 'bwrap 隔离',
  'vm-bwrap': '桌面 VM 执行域',
  'seatbelt': 'mac 原生（seatbelt）',
  'appcontainer': 'win 原生（AppContainer）',
  'remote': '远程执行器',
};
const REASON_ZH: Record<string, string> = {
  'bwrap-unavailable': 'bwrap / user namespace 不可用',
  'seatbelt-not-implemented': 'seatbelt 档尚未实现（长期可选）',
  'appcontainer-not-implemented': 'AppContainer 档尚未实现（长期可选）',
  'remote-not-implemented': '远程执行器档尚未实现',
};

/** 安全运维面可写键（PUT /api/admin/security/ops）——与后端七键对齐 */
interface OpsConfig {
  codemode_enabled: boolean;
  lsp_enabled: boolean;
  shared_readonly: string[];
  resource_bridges: { path: string; mode: string }[];
  approval_ttl_s: number;
  egress_proxy_port: number;
  egress_grant_ttl_s: number;
}

interface AuditEvent {
  id: number; ts: string; type: string;
  sid: string | null; detail_json: string;
}

const TYPE_ZH: Record<string, string> = {
  egress_request: '外发请求', snapshot: '任务启动',
  approval_request: '审批请求', approval_decision: '审批决定',
  permission_decision: '权限决定', skill_scan: 'Skill 扫描',
  canary_hit: '蜜罐命中', kill_switch: '熔断', anomaly: '异常',
  sandbox_violation: '沙箱违规', sandbox_tier: '沙箱档位', rollback: '回滚', policy_change: '策略变更',
};
const FILTERS: { key: string; label: string }[] = [
  { key: '', label: '全部' },
  { key: '__llm', label: '模型调用' },
  { key: 'egress_request', label: '外发请求' },
  { key: 'snapshot', label: '任务启动' },
  { key: 'approval_request', label: '审批' },
  { key: 'permission_decision', label: '权限' },
  { key: 'canary_hit', label: '蜜罐命中' },
  { key: 'kill_switch', label: '熔断' },
  { key: 'anomaly', label: '异常' },
];

function parse(d: string): Record<string, any> {
  try { return JSON.parse(d); } catch { return {}; }
}

function humanize(e: AuditEvent): string {
  const d = parse(e.detail_json);
  switch (e.type) {
    case 'egress_request': {
      if (d.host === 'llm-gw.internal') return '经安全网关调用模型';
      const verdicts: Record<string, string> = {
        allow: '放行', 'allow-grant': '放行（临时授权）',
        'allow-ask': '放行（弹卡批准）', 'allow-warn': '放行（告警档）',
        'allow-open': '放行（放开档）', deny: '拒绝（不在白名单）',
        'ask-timeout': '弹卡超时未裁决', 'denied-by-user': '用户已拒绝',
        'deny-mismatch': '拒绝（域名分片防护）',
      };
      const verdict = verdicts[String(d.decision || '')] ?? String(d.decision);
      return `${d.host} · ${verdict}`;
    }
    case 'snapshot':
      return `${d.engine ?? '?'} 引擎 · ${d.mode === 'bwrap' ? '沙箱内运行' : '⚠ 未隔离直跑'}`;
    case 'approval_request':
      return `${d.summary ?? '敏感操作'} 等待确认码`;
    case 'approval_decision':
      return `${d.summary ?? d.action ?? '敏感操作'} · ${d.decision === 'approved' ? '已批准' : '已拒绝'}`;
    case 'canary_hit':
      return `诱饵凭证被盗用（${d.subject ?? '?'}）——已自动熔断`;
    case 'kill_switch':
      return d.action === 'clear' ? '解除全局熔断' : `触发熔断（停 ${d.stopped ?? '?'} 个任务）`;
    default: {
      const parts = Object.entries(d).filter(([, v]) => typeof v !== 'object')
        .slice(0, 3).map(([k, v]) => `${k}=${String(v).slice(0, 24)}`);
      return parts.join(' ') || '-';
    }
  }
}

/** 连续的内部网关事件折叠成一行（防 llm-gw 刷屏） */
function aggregate(events: AuditEvent[]): (AuditEvent & { count?: number })[] {
  const out: (AuditEvent & { count?: number })[] = [];
  for (const e of events) {
    const d = parse(e.detail_json);
    const isLlm = e.type === 'egress_request' && d.host === 'llm-gw.internal';
    const prev = out[out.length - 1];
    if (isLlm && prev && prev.count && parse(prev.detail_json).host === 'llm-gw.internal') {
      prev.count += 1;
    } else {
      out.push(isLlm ? { ...e, count: 1 } : e);
    }
  }
  return out;
}

/** sid → 会话名链接：审计行只存 sid，而 sid 的 slug 冻结在创建瞬间
 *  （默认「新任务」——titlegen 事后命名不回填），直接展示全是「新任务xxxx」。
 *  从 store 解析当前标题；未命中（归档/已删）回落原始 sid。 */
function SessionLink({ sid, jump, truncate = false }: {
  sid: string | null; jump: (s: string | null) => void; truncate?: boolean;
}) {
  const title = useStore(s => s.sessions.find(x => x.id === sid)?.title);
  if (!sid) return <>—</>;
  const label = title || sid;
  return (
    <a onClick={() => jump(sid)} style={{ cursor: 'pointer' }} title={sid}>
      {truncate && label.length > 20 ? `${label.slice(0, 20)}…` : label}
    </a>
  );
}

// 安全页类化批：容器/控件全收敛为 .sec-* CSS 类（原 card 内联对象已删）
type CardKey = 'sandbox' | 'approvals' | 'egress' | 'vault' | 'audit' | 'canary' | 'target';
/** 子 tab（T9）：总览=姿态卡；各功能页=介绍+具体内容，点 tab 进入 */
type SecTab = 'overview' | CardKey;
const SEC_TABS: [CardKey, string][] = [
  ['sandbox', '沙箱隔离'], ['approvals', '敏感操作审批'], ['target', '目标放行策略'],
  ['egress', '网络出口管控'], ['vault', '凭证保险库'], ['audit', '审计账本'],
  ['canary', '蜜罐诱饵'],
];

export default function SecurityPanel() {
  const [posture, setPosture] = useState<Posture | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState('');
  const [verify, setVerify] = useState<{ ok: boolean; text: string } | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState('');
  const [open, setOpen] = useState<CardKey | null>(null);   // 卡片展开态（overview 内）
  // 子 tab 导航（T9）；初始读深链 #/admin/security/<sub>（凭证库统一批：
  // 资源页「管理凭证」等跨页跳转直达子 tab，省一次手动点）
  const [tab, setTabState] = useState<SecTab>(() => {
    const m = location.hash.match(/^#\/admin\/security\/(\w+)/);
    return m && SEC_TABS.some(([k]) => k === m[1]) ? (m[1] as SecTab) : 'overview';
  });
  // 切子 tab 同步 hash（replaceState 不触发 hashchange；AdminPanel 只解析
  // 第一段 security，第二段归本组件——两个监听互不干扰）
  const setTab = (t: SecTab) => {
    setTabState(t);
    const q = location.hash.split('?')[1];
    history.replaceState(null, '', t === 'overview'
      ? `#/admin/security${q ? '?' + q : ''}`
      : `#/admin/security/${t}${q ? '?' + q : ''}`);
  };
  const [approveN, setApproveN] = useState(-1);              // tab 徽章：待审数（-1=未拉到不显示）
  const [targetN, setTargetN] = useState(-1);                // tab 徽章：策略数
  const armTimer = useRef<number | null>(null);
  const openSession = useStore(s => s.openSession);

  const load = async () => {
    try {
      setPosture(await api<Posture>('/api/admin/security'));
      setEvents((await api<{ events: AuditEvent[] }>('/api/admin/audit?n=60')).events);
    } catch { /* 忽略（403 会走 TokenGate） */ }
  };
  // tab 计数徽章（轻量补拉，失败静默——徽章只是辅助信息）
  const loadCounts = () => {
    void api<{ pending: unknown[] }>('/api/admin/approvals')
      .then(d => setApproveN(d.pending?.length ?? 0)).catch(() => { });
    void api<{ policies: unknown[] }>('/api/admin/target-policy')
      .then(d => setTargetN(d.policies?.length ?? 0)).catch(() => { });
  };
  useEffect(() => {
    void load(); loadCounts();
    const t = setInterval(() => void load(), 10000);
    return () => { clearInterval(t); if (armTimer.current) clearTimeout(armTimer.current); };
  }, []);

  const doVerify = async () => {
    setVerifying(true); setVerify(null);
    try {
      const d = await api<{ problems: string[] }>('/api/admin/audit/verify', { method: 'POST' });
      setVerify(d.problems.length === 0
        ? { ok: true, text: `账本完整：哈希链逐行一致，${posture?.audit.anchors ?? '?'} 个日锚点全部命中` }
        : { ok: false, text: `发现 ${d.problems.length} 处异常：\n${d.problems.slice(0, 5).join('\n')}` });
    } catch (e) {
      setVerify({ ok: false, text: `校验请求失败：${e}\n（若提示 admin required，输入 token 后重试）` });
    } finally { setVerifying(false); }
  };

  const doKillAll = async () => {
    if (!armed) {
      setArmed(true);
      armTimer.current = window.setTimeout(() => setArmed(false), 5000);
      return;
    }
    if (armTimer.current) clearTimeout(armTimer.current);
    setArmed(false); setBusy('正在熔断…');
    try {
      const d = await api<{ stopped_turns: number }>('/api/admin/kill-all', { method: 'POST' });
      setVerify(null);
      toast(`已熔断：停止 ${d.stopped_turns} 个任务，调度暂停，新任务被拒。\n排查完成后点「解除熔断」恢复。`);
    } catch (e) { toast(`熔断失败: ${e}`, false); }
    finally { setBusy(''); void load(); }
  };

  const doClear = async () => {
    setBusy('正在恢复…');
    try {
      const d = await api<{ unlocked: string[]; kept_locked: { sid: string }[] }>('/api/admin/kill-all/clear', { method: 'POST' });
      const kept = d.kept_locked?.length
        ? `\n注意：${d.kept_locked.length} 个会话因蜜罐命中保持锁定（真实警报，需人工核后解锁）` : '';
      toast(`已恢复：调度继续，解锁 ${d.unlocked?.length ?? 0} 个会话。${kept}`);
    } catch (e) { toast(`恢复失败: ${e}`, false); }
    finally { setBusy(''); void load(); }
  };

  const jump = (sid: string | null) => {
    // AC-3.4：统一跳转协议（与 TasksTab/ActivityTab 一致）——hash 清空 + openSession
    if (!sid) return;
    location.hash = '';
    void openSession(sid);
  };

  const toggle = (k: CardKey) => {
    // T9：卡片点击直达功能子 tab（audit 同样——事件流在审计页里）
    setTab(k);
    setOpen(open === k ? null : k);
  };

  if (!posture) return <div className="admin-body muted">加载中…</div>;

  // 档位三态：effective 是实际生效档（旧后端无该字段时回落 mode）
  const sbxReq = posture.sandbox.requested ?? posture.sandbox.mode;
  const sbxEff = posture.sandbox.effective ?? posture.sandbox.mode;
  const sbxReason = posture.sandbox.reason ?? '';
  const sandboxOk = sbxEff === 'bwrap' || sbxEff === 'vm-bwrap';
  // 请求了隔离但被降档（探测失败/未实现）→ 黄条显著告警；显式 off 是用户选择 → 灰态+诚实标注
  const sandboxWarn = !sandboxOk && !!sbxReason;
  const sandboxTop = sandboxOk
    ? sbxEff === 'vm-bwrap'
      ? `VM 满档 · bwrap · 近 ${posture.sandbox.window} 个任务全覆盖`
      : `已隔离 · 近 ${posture.sandbox.window} 个任务全覆盖`
    : sandboxWarn
      ? `降级：${TIER_ZH[sbxReq] ?? sbxReq} 未生效，当前直跑`
      : '降级档（直跑）· 本机文件未隔离';
  const sandboxHint = sandboxOk
    ? sbxEff === 'vm-bwrap' ? '执行域整个运行在桌面 Linux 虚拟机内，与服务器安全语义逐字节一致。' : undefined
    : sandboxWarn
      ? `原因：${REASON_ZH[sbxReason] ?? sbxReason}。可在下方安全运维面切换可用档位（下一任务起生效）。`
      : '引擎在本机直跑。审批/出口管控/审计/蜜罐仍然生效，但文件不隔离：在下方安全运维面切为 bwrap 即可开启（下一任务起生效）。';
  const policyOk = posture.policy.approval_enforce === 'enforce';
  const policyWarn = posture.policy.approval_enforce === 'warn';
  const egressOk = posture.egress.mode === 'enforce';
  const egressWarn = posture.egress.mode === 'warn';
  // off=用户显式放开 → 灰态+诚实标注（同沙箱降级档的呈现语义）
  const egressTop = egressOk
    ? `${posture.egress.on_deny === 'ask' ? '强制 · 弹卡确认' : '强制'} · 白名单 ${posture.egress.allow_count} 个域`
    : egressWarn
      ? `告警 · 白名单 ${posture.egress.allow_count} 个域（放行+逐条记录）`
      : '已放开 · 全放行（仍走代理审计）';
  const egressHint = egressOk
    ? (posture.egress.on_deny === 'ask'
        ? `白名单外的域会弹审批卡等你裁决（等待 ${posture.egress.ask_wait_s ?? 120}s），批准即放行。`
        : undefined)
    : egressWarn
      ? '白名单外的域放行但逐条告警。可在下方「出口策略」切回强制。'
      : '所有外联直通（审计仍逐条记录）。可在下方「出口策略」切回强制/告警。';
  // AC-5.10b：emoji→feather 线性图标（路亮/暗主题与各平台渲染一致）
  // tone=hero 左缘色条态（safe 绿/warn 黄/danger 红/'' 靛）；badge=徽章色类
  const cards: { key: CardKey; name: string; ok: boolean; warn?: boolean; icon: ReactNode;
    tone?: '' | 'safe' | 'warn' | 'danger'; badge?: string;
    top: string; sub: string; hint?: string; danger?: boolean }[] = [
    {
      key: 'sandbox', name: '沙箱隔离', ok: sandboxOk, warn: sandboxWarn, icon: <Box size={15} />,
      tone: sandboxOk ? 'safe' : 'warn', badge: sandboxOk ? 'ok' : 'warn',
      top: sandboxTop,
      sub: 'AI 执行的命令被关在隔离环境里，碰不到系统其它文件与真实网络。档位在明细里可切（下一任务起生效）。',
      hint: sandboxHint,
    },
    {
      key: 'approvals', name: '敏感操作审批', ok: policyOk, warn: policyWarn, icon: <Lock size={15} />,
      tone: policyWarn ? 'warn' : '', badge: policyOk ? '' : 'warn',
      top: policyOk ? '强制（高危操作须输确认码）' : policyWarn ? '仅告警（不拦截）' : posture.policy.approval_enforce,
      sub: '发邮件、动账号、付款类操作执行前需要你输确认码放行，AI 无法自行通过。TTL 可在明细里调。',
      hint: policyOk ? undefined : '当前不拦截：在下方明细把 approval_enforce 切为 enforce。',
    },
    {
      // 七轮补 UI（设定审计#1）：P10 per-target 三档策略的管理面——此前
      // 全链零 UI（只能直接调 API），「始终允许」建了策略也没处看/删
      key: 'target', name: '目标放行策略', ok: true, icon: <Crosshair size={15} />,
      tone: '', badge: 'neutral',
      top: '常设放行/拒绝清单',
      sub: '审批卡的「始终允许」落在这里：按目标域名/动作类/确切参数三档窄化，可改档可删。',
    },
    {
      key: 'egress', name: '网络出口管控', ok: egressOk, warn: egressWarn, icon: <Globe size={15} />,
      tone: egressOk ? 'safe' : 'warn', badge: egressOk ? 'ok' : 'warn',
      top: egressTop,
      sub: 'AI 的所有对外请求经过代理；模型调用走内部网关，凭证不进沙箱。策略在下方「出口策略」即时可调。',
      hint: egressHint,
    },
    {
      key: 'vault', name: '凭证保险库', ok: posture.vault.platforms > 0, icon: <Key size={15} />,
      tone: posture.vault.platforms > 0 ? 'safe' : 'warn',
      badge: posture.vault.platforms > 0 ? 'ok' : 'warn',
      top: `${posture.vault.platforms} 组账号 · AES-GCM 加密`,
      sub: '各类账号密码加密落盘，明文不出现在代码、配置和日志里。',
      hint: posture.vault.platforms > 0 ? undefined : '尚无凭证入库：用 loadn-web r account --set 添加。',
    },
    {
      key: 'audit', name: '审计账本', ok: true, icon: <FileDoc size={15} />,
      tone: '', badge: 'purple',
      top: `防篡改 · 已记 ${posture.audit.last_id} 条 · ${posture.audit.anchors} 个锚点`,
      sub: '敏感操作全部入链式账本，任何人（包括管理员）改一行都会被校验发现。点卡跳到事件流。',
    },
    {
      key: 'canary', name: '蜜罐诱饵', ok: !posture.canary.kill_all, icon: <Flask size={15} />,
      tone: (posture.canary.kill_all || posture.canary.locked_sessions.length > 0) ? 'danger' : 'safe',
      badge: (posture.canary.kill_all || posture.canary.locked_sessions.length > 0) ? 'danger' : 'ok',
      top: posture.canary.kill_all ? '全局熔断激活中！'
        : posture.canary.locked_sessions.length
          ? `${posture.canary.locked_sessions.length} 个会话触发警报`
          : '哨兵正常 · 未命中',
      sub: '每个会话埋了假凭证：提示词注入或人为盗用一旦使用，立刻熔断该会话。',
      danger: posture.canary.kill_all || posture.canary.locked_sessions.length > 0,
    },
  ];
  /** tab 徽章计数（真实数据：-1=未拉到不显示） */
  const tierShort: Record<string, string> = { off: '直跑', bwrap: 'bwrap', 'vm-bwrap': 'vm' };
  const kNum = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  const countFor = (k: CardKey): string | null => {
    switch (k) {
      case 'sandbox': return tierShort[sbxEff] ?? sbxEff;
      case 'approvals': return approveN >= 0 ? String(approveN) : null;
      case 'target': return targetN >= 0 ? String(targetN) : null;
      case 'egress': return String(posture.egress.allow_count);
      case 'vault': return String(posture.vault.platforms);
      case 'audit': return kNum(posture.audit.last_id);
      case 'canary': return posture.canary.kill_all ? '熔断'
        : posture.canary.locked_sessions.length ? `⚠${posture.canary.locked_sessions.length}` : '正常';
    }
  };

  const isLlmEvent = (e: AuditEvent) =>
    e.type === 'egress_request' && parse(e.detail_json).host === 'llm-gw.internal';
  const visible = events.filter(e => {
    if (filter === '__llm') return isLlmEvent(e);
    if (filter === 'approval_request')
      return e.type === 'approval_request' || e.type === 'approval_decision';
    if (filter) return e.type === filter;
    return true;
  });
  const rows: (AuditEvent & { count?: number })[] =
    filter === '__llm' ? visible : aggregate(visible);

  return (
    <div className="admin-body">
      {/* ---- 子 tab 栏：白底容器+渐变 active+计数徽章（设计稿 security-subnav） ---- */}
      <div className="sh-subnav" role="tablist" aria-label="安全功能分区">
        <button role="tab" aria-selected={tab === 'overview'}
                className={`sh-subtab ${tab === 'overview' ? 'on' : ''}`}
                onClick={() => { setTab('overview'); setOpen(null); }}>总览</button>
        {SEC_TABS.map(([k, label]) => {
          const c = cards.find(x => x.key === k);
          const n = countFor(k);
          return (
            <button key={k} role="tab" aria-selected={tab === k}
                    className={`sh-subtab ${tab === k ? 'on' : ''}${c?.danger ? ' danger' : ''}`}
                    onClick={() => { setTab(k); setOpen(null); }}>
              {c?.danger ? '⚠ ' : ''}{label}
              {n && <span className="sh-tabcount">{n}</span>}
            </button>
          );
        })}
      </div>
      {posture.canary.kill_all && (
        <div className="sec-killbar">
          ⚠ <b>全局熔断激活中</b>：调度已暂停、新任务被拒、原有任务已停止。这是紧急刹车，
          排查完原因后到总览页点「解除熔断」恢复。
        </div>
      )}
      {tab !== 'overview' ? (() => {
        /* ---- 功能页：hero 横幅（图标+badge+warn-tip+左缘色条） + 具体内容 ---- */
        const c = cards.find(x => x.key === tab)!;
        return (<>
          <div className={`sh-hero ${c.tone ?? ''}`}>
            <div className="sh-hero-left">
              <div className="sh-hero-icon">{c.icon}</div>
              <div>
                <div className="sh-hero-title">{c.name}
                  <span className={`sh-badge ${c.badge ?? ''}`}>{c.top}</span>
                </div>
                <div className="sh-hero-desc">{c.sub}</div>
                {c.hint && <div className="sh-hero-tip">→ {c.hint}</div>}
              </div>
            </div>
          </div>
          {tab !== 'audit' && (
            <div className="sec-detail">
              {tab === 'sandbox' && <SandboxDetail events={events} jump={jump} tier={posture.sandbox} />}
              {tab === 'sandbox' && posture.ops && (
                <OpsDetail ops={posture.ops} reload={load}
                  approvalEnforce={posture.policy.approval_enforce} />)}
              {tab === 'approvals' && <ApprovalsDetail events={events} jump={jump} />}
              {tab === 'target' && <TargetPolicyDetail />}
              {tab === 'egress' && <EgressDetail />}
              {tab === 'vault' && <VaultDetail />}
              {tab === 'canary' && <CanaryDetail locked={posture.canary.locked_sessions} reload={load} jump={jump} />}
            </div>
          )}
          {tab === 'audit' && (<>
            <div className="sh-card">
              <div className="sh-card-head">
                <div className="sh-card-titlewrap">
                  <span className="sh-card-title">操作记录</span>
                  <span className="sh-card-sub">全部敏感动作的防篡改流水（{posture.audit.last_id} 条中的最近一段）</span>
                  <a className="link" title="台账 tab 双源合并视图（运营台账 + 审计账本，AC-2.4）"
                    onClick={() => { location.hash = '#/admin/activity'; }}>查看完整流水 →</a>
                </div>
                <select value={filter} onChange={e => setFilter(e.target.value)}
                  autoComplete="off">
                  {FILTERS.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
                </select>
              </div>
              <div className="sh-card-body flush">
                <table className="sh-table">
                  <thead><tr><th style={{ width: 110 }}>时间</th><th style={{ width: 100 }}>类型</th><th style={{ width: 150 }}>会话</th><th>内容</th></tr></thead>
                  <tbody>
                    {rows.length === 0 && (
                      <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 20 }}>
                        （该类型暂无记录）
                      </td></tr>
                    )}
                    {rows.map(r => {
                      const d = parse(r.detail_json);
                      const isLlm = r.type === 'egress_request' && d.host === 'llm-gw.internal';
                      return (
                        <tr key={r.id} style={isLlm ? { opacity: 0.65 } : undefined}>
                          <td className="num">{(r.ts || '').slice(5, 19).replace('T', ' ')}</td>
                          <td>{isLlm ? '模型调用' : (TYPE_ZH[r.type] ?? r.type)}</td>
                          <td><SessionLink sid={r.sid} jump={jump} truncate /></td>
                          <td>{r.count ? humanize(r) + ` × ${r.count}` : humanize(r)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </>)}
        </>);
      })() : (<>
      {/* ---- 总览：态势 hero + 六大卡（点击直达子 tab） ---- */}
      {(() => {
        const bad = cards.filter(c => c.danger).length;
        const warnN = cards.filter(c => !c.danger && c.warn).length;
        return (
          <div className={`sh-hero ${bad ? 'danger' : warnN ? 'warn' : 'safe'}`}>
            <div className="sh-hero-left">
              <div className="sh-hero-icon"><Shield size={19} /></div>
              <div>
                <div className="sh-hero-title">安全态势总览 · {cards.length} 大防线实时运作中
                  {bad > 0 && <span className="sh-badge danger">⚠ {bad} 项告警需处理</span>}
                  {warnN > 0 && <span className="sh-badge warn">{warnN} 项待加固</span>}
                  {!bad && !warnN && <span className="sh-badge ok">全部正常</span>}
                </div>
                <div className="sh-hero-desc">
                  纵深防御：进程沙箱 + 敏感动作人工确认码 + 域名级出口网关 + AES-GCM 凭证保险库
                  + 哈希链防篡改账本 + 蜜罐诱饵。点击下方任意模块卡直达明细配置。
                </div>
              </div>
            </div>
          </div>
        );
      })()}
      {/* ---- ov 大卡：hover 上浮，点击直达功能子 tab ---- */}
      <div className="sh-ovgrid">
        {cards.map(c => (
          <div key={c.key} onClick={() => toggle(c.key)} className="sh-ovcard">
            <div>
              <div className="sh-ovcard-top">
                <span className="sh-ovcard-title">{c.icon}{c.name}</span>
                <span className={`sh-badge ${c.badge ?? ''}`}>{c.top}</span>
              </div>
              <div className="sh-ovcard-desc">{c.sub}</div>
              {c.hint && <div className="sh-hero-tip" style={{ marginTop: 10 }}>→ {c.hint}</div>}
            </div>
            <div className="sh-ovcard-foot">
              <span>查看明细与配置</span><span>→</span>
            </div>
          </div>
        ))}
      </div>

      {/* ---- 操作区（普通操作左组 / 紧急操作右组，视觉分离） ---- */}
      <div className="sec-opsbar">
        <button className="btn sm" onClick={doVerify} disabled={verifying}>
          {verifying ? '校验中…' : '校验账本是否被篡改'}
        </button>
        {posture.canary.kill_all ? (
          <button className="btn sm danger" onClick={doClear} disabled={!!busy}>
            {busy || '解除熔断，恢复运行'}
          </button>
        ) : (
          <button className="btn sm danger" data-armed={armed || undefined}
            onClick={doKillAll} disabled={!!busy}>
            {busy || (armed ? '⚠ 再点一次确认熔断（5 秒内）' : '全局紧急停止')}
          </button>
        )}
        {verify && (
          <div className={`sec-ops-note ${verify.ok ? 'ok' : 'bad'}`}>{verify.text}</div>
        )}
      </div>
      </>)}
    </div>
  );
}

/* ================= 卡片详情 ================= */

/** 安全运维面编辑器（PUT /api/admin/security/ops）：七键局部写。
 *  简单键即改即存；沙箱档位下一任务起生效（spawn 期消费）；授权面列表行编辑。
 *  红线键（审计链/确认码门/信任门）不在此面——宪法不可配。 */
function OpsDetail({ ops, reload, approvalEnforce }: {
  ops: OpsConfig; reload: () => void; approvalEnforce: string;
}) {
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [sandbox, setSandbox] = useState<string>('');
  const [ttl, setTtl] = useState('');
  const [grantTtl, setGrantTtl] = useState('');
  const [proxyPort, setProxyPort] = useState('');
  const [shared, setShared] = useState(ops.shared_readonly.join('\n'));
  const [bridges, setBridges] = useState(
    ops.resource_bridges.map(b => `${b.mode} ${b.path}`).join('\n'));
  useEffect(() => {   // 父层重载 posture 后同步本地编辑态
    setShared(ops.shared_readonly.join('\n'));
    setBridges(ops.resource_bridges.map(b => `${b.mode} ${b.path}`).join('\n'));
  }, [ops]);

  const put = (patch: Record<string, unknown>) => {
    setBusy('saving'); setErr('');
    void api('/api/admin/security/ops', {
      method: 'PUT', body: JSON.stringify(patch),
    }).then(() => { setBusy(''); reload(); })
      .catch((e: unknown) => { setBusy(''); setErr(String(e)); reload(); });
  };
  const toggle = (k: 'codemode_enabled' | 'lsp_enabled', v: boolean) => put({ [k]: v });
  const saveSandbox = () =>
    sandbox && put({ sandbox });
  const saveTtl = () => {
    const n = parseInt(ttl, 10);
    if (!Number.isNaN(n)) put({ approval_ttl_s: n });
  };
  const saveGrantTtl = () => {
    const n = parseInt(grantTtl, 10);
    if (!Number.isNaN(n)) put({ egress_grant_ttl_s: n });
  };
  const saveProxyPort = () => {
    const n = parseInt(proxyPort, 10);
    if (!Number.isNaN(n)) put({ egress_proxy_port: n });
  };
  const saveLists = () => {
    const sr = shared.split('\n').map(s => s.trim()).filter(Boolean);
    const rb: { path: string; mode: string }[] = [];
    for (const ln of bridges.split('\n')) {
      const m = ln.trim().match(/^(ro|rw|dev)\s+(.+)$/);
      if (m) rb.push({ mode: m[1], path: m[2] });
    }
    put({ shared_readonly: sr, resource_bridges: rb });
  };
  return (
    <div className="sh-card">
      <div className="sh-card-head">
        <div className="sh-card-titlewrap">
          <span className="sh-card-title">安全运维面</span>
          <span className="sh-card-sub">yaml round-trip 持久化 · 全部热生效：沙箱档位自下一任务起，其余立即</span>
        </div>
        <span className="sh-badge ok">config.yaml 已同步</span>
      </div>
      <div className="sh-card-body">
        <div className="sh-ops">
          {err && <div className="admin-msg err" style={{ marginBottom: 10 }}>写入被拒：{err}</div>}
          <div className="sh-ops-row">
            <span className="sh-ops-k">沙箱档位</span>
            <div className="sh-ops-inline">
              <select value={sandbox} onChange={e => setSandbox(e.target.value)} defaultValue=''
                autoComplete="off" style={{ minWidth: 250 }}>
                <option value='' disabled>选择档位…</option>
                {Object.entries(TIER_ZH).map(([v, label]) =>
                  <option key={v} value={v}>{v} — {label}</option>)}
              </select>
              <button className="mini-btn" disabled={!sandbox || busy === 'saving'}
                onClick={saveSandbox}>切换（下一任务起生效）</button>
            </div>
          </div>
          <div className="sh-ops-row">
            <span className="sh-ops-k">审批策略</span>
            <div className="sh-ops-inline">
              <div className="sh-seg">
                {([['enforce', '强制（确认码拦截）'], ['warn', '仅告警（放行+留痕）']] as [string, string][]).map(
                  ([v, label]) => (
                    <button key={v} className={`sh-seg-btn${approvalEnforce === v ? ' on' : ''}`}
                      disabled={busy === 'saving'} onClick={() => put({ approval_enforce: v })}
                      title={v === 'warn'
                        ? '高危操作放行但逐条告警入账本（灰度期用；确认码通道保留）'
                        : '高危操作执行前须输确认码，AI 无法自行通过'}>{label}</button>
                  ))}
              </div>
              <span className="sh-ops-hint">热生效</span>
            </div>
          </div>
          <div className="sh-ops-row">
            <span className="sh-ops-k">codemode</span>
            <div className="sh-ops-inline">
              <label className="sh-switch">
                <input type="checkbox" checked={ops.codemode_enabled}
                  onChange={e => toggle('codemode_enabled', e.target.checked)} autoComplete="off" />
                <span className="sh-switch-track" />
                <span className="sh-ops-hint">{ops.codemode_enabled ? '已开（Python AST 白名单域）' : '关（默认）'}</span>
              </label>
            </div>
          </div>
          <div className="sh-ops-row">
            <span className="sh-ops-k">LSP 回注</span>
            <div className="sh-ops-inline">
              <label className="sh-switch">
                <input type="checkbox" checked={ops.lsp_enabled}
                  onChange={e => toggle('lsp_enabled', e.target.checked)} autoComplete="off" />
                <span className="sh-switch-track" />
                <span className="sh-ops-hint">{ops.lsp_enabled ? '已开（编辑后查询诊断）' : '关（默认）'}</span>
              </label>
            </div>
          </div>
          <div className="sh-ops-row">
            <span className="sh-ops-k">审批 TTL</span>
            <div className="sh-ops-inline">
              <input className="sec-num" type="number" placeholder={String(ops.approval_ttl_s)}
                autoComplete="off" value={ttl} onChange={e => setTtl(e.target.value)} />
              <span className="sh-ops-hint">60-86400 秒</span>
              <button className="mini-btn" disabled={!ttl || busy === 'saving'}
                onClick={saveTtl}>保存</button>
            </div>
          </div>
          <div className="sh-ops-row">
            <span className="sh-ops-k">放行 TTL</span>
            <div className="sh-ops-inline">
              <input className="sec-num" type="number" placeholder={String(ops.egress_grant_ttl_s)}
                autoComplete="off" value={grantTtl} onChange={e => setGrantTtl(e.target.value)} />
              <span className="sh-ops-hint">300-86400 秒</span>
              <button className="mini-btn" disabled={!grantTtl || busy === 'saving'}
                onClick={saveGrantTtl}>保存</button>
            </div>
          </div>
          <div className="sh-ops-row">
            <span className="sh-ops-k">代理端口</span>
            <div className="sh-ops-inline">
              <input className="sec-num" type="number" placeholder={String(ops.egress_proxy_port)}
                autoComplete="off" value={proxyPort} onChange={e => setProxyPort(e.target.value)} />
              <span className="sh-ops-hint">0 = 随机（生产可固定）</span>
              <button className="mini-btn" disabled={proxyPort === '' || busy === 'saving'}
                onClick={saveProxyPort}>保存</button>
            </div>
          </div>
          <div className="sh-ops-row top">
            <span className="sh-ops-k" style={{ paddingTop: 4 }}>授权面</span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: '100%' }}>
              <div className="sh-ops-inline" style={{ justifyContent: 'space-between' }}>
                <span className="sh-ops-hint">
                  <span className="sh-pill">shared_readonly</span> 每行一个绝对路径（ro）；
                  <span className="sh-pill">resource_bridges</span> 为 <span className="sh-pill">ro|rw|dev</span> 加空格加路径
                </span>
                <button className="mini-btn" disabled={busy === 'saving'}
                  onClick={saveLists}>保存列表</button>
              </div>
              <textarea className="sec-textarea" value={shared} onChange={e => setShared(e.target.value)}
                autoComplete="off"
                placeholder="跨项目只读共享根（每行一个绝对路径）" />
              <textarea className="sec-textarea tall" value={bridges} onChange={e => setBridges(e.target.value)}
                autoComplete="off"
                placeholder={'ro /data/pub\nrw /data/scratch\ndev /dev/dri'} />
            </div>
          </div>
          {busy === 'saving' && <div className="sh-ops-hint" style={{ marginTop: 8 }}>写入中…</div>}
        </div>
      </div>
    </div>
  );
}

function SandboxDetail({ events, jump, tier }: {
  events: AuditEvent[]; jump: (s: string | null) => void;
  tier: { requested?: string; effective?: string; reason?: string; mode: string };
}) {
  const snaps = events.filter(e => e.type === 'snapshot').slice(0, 12);
  const req = tier.requested ?? tier.mode;
  const eff = tier.effective ?? tier.mode;
  const reason = tier.reason ?? '';
  return (
    <div className="sh-card">
      <div className="sh-card-head">
        <div className="sh-card-titlewrap">
          <span className="sh-card-title">近期任务的隔离记录</span>
          <span className="sh-card-sub">点会话名跳转；「直跑」=未进沙箱（应排查）</span>
        </div>
        <span className={`sh-badge ${eff === 'bwrap' || eff === 'vm-bwrap' ? 'ok' : 'warn'}`}>
          当前档位：{TIER_ZH[eff] ?? eff}
        </span>
      </div>
      <div className="sh-card-body">
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-dim)', margin: '0 0 10px', lineHeight: 1.7 }}>
          {req !== eff && `（请求 ${TIER_ZH[req] ?? req}${reason ? ` · ${REASON_ZH[reason] ?? reason}，已降档` : ''}）`}
          档位在 <span className="sh-pill">config.yaml</span> 的 <span className="sh-pill">security.sandbox</span> 配置
          （枚举：<span className="sh-pill">off / bwrap / vm-bwrap / seatbelt / appcontainer / remote</span>），
          设置面切换后自下一任务起生效（在跑任务的沙箱形态已定型，不受影响）。
        </div>
        <table className="sh-table">
          <thead><tr><th style={{ width: 120 }}>时间</th><th style={{ width: 90 }}>引擎</th><th style={{ width: 120 }}>隔离</th><th>会话</th></tr></thead>
          <tbody>
            {snaps.length === 0 && <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 20 }}>（近期无任务）</td></tr>}
            {snaps.map(s => {
              const d = parse(s.detail_json);
              const ok = d.mode === 'bwrap';
              return (
                <tr key={s.id}>
                  <td className="num">{(s.ts || '').slice(5, 19).replace('T', ' ')}</td>
                  <td><span className="sh-pill">{d.engine ?? '?'}</span></td>
                  <td>{ok ? <span className="sh-badge ok">bwrap 强隔离</span> : <span className="sh-badge warn">⚠ 直跑（未进沙箱）</span>}</td>
                  <td><SessionLink sid={s.sid} jump={jump} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ApprovalsDetail({ events, jump }: { events: AuditEvent[]; jump: (s: string | null) => void }) {
  const [pending, setPending] = useState<{ id: number; sid: string; summary: string; created_at: string; ttl_s: number }[]>([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState<number | null>(null);
  const [sweeping, setSweeping] = useState(false);
  const loadPending = () => {
    void api<{ pending: typeof pending }>('/api/admin/approvals')
      .then(d => setPending(d.pending))
      .catch(e => setErr(String(e)));
  };
  useEffect(loadPending, []);
  // 设计稿「一键清理已超时记录」：后端 sweep（pending 超 TTL 批量置
  // expired，幂等）——列表读取已惰性清扫，这里是显式收尾入口
  const sweep = () => {
    setSweeping(true); setErr('');
    void api<{ expired: number }>('/api/admin/approvals/sweep', { method: 'POST' })
      .then(d => { setSweeping(false); loadPending();
        toast(d.expired > 0 ? `已清理 ${d.expired} 条超时审批` : '没有已超时的待审记录'); })
      .catch(e => { setSweeping(false); setErr(`清理失败：${String(e)}`); });
  };
  // AC-4.2：管理面驳回（decide approve=false，admin 角色后端放行）——
  // 批准语义不变（会话内确认码输入，防伪造）；高危堆积时止损有门
  const deny = async (aid: number) => {
    if (!await askConfirm({ title: `驳回审批 #${aid}？agent 侧立即收到否决（可重新规划替代方案）。`, danger: true })) return;
    setBusy(aid); setErr('');
    void api(`/api/approvals/${aid}/decide`, {
      method: 'POST', body: JSON.stringify({ approve: false }),
    }).then(d => {
      if ((d as { ok?: boolean }).ok) setPending(p => p.filter(x => x.id !== aid));
      else setErr(`驳回未生效：${JSON.stringify(d)}`);
    }).catch(e => setErr(`驳回失败：${String(e)}`))
      .finally(() => setBusy(null));
  };
  const ttlLeft = (p: { created_at: string; ttl_s: number }) => {
    const age = (Date.now() - new Date(p.created_at.includes('T') ? p.created_at : p.created_at.replace(' ', 'T')).getTime()) / 1000;
    const m = Math.round((p.ttl_s - age) / 60);
    return m > 0 ? `≈${m} 分钟` : '已超时';
  };
  const decisions = events.filter(e => e.type === 'approval_decision').slice(0, 8);
  return (
    <div>
      {err && <div className="admin-msg err" style={{ marginBottom: 10 }}>{err}</div>}
      <div className="sh-card">
        <div className="sh-card-head">
          <div className="sh-card-titlewrap">
            <span className="sh-card-title">待审清单</span>
            <span className="sh-card-sub">批准 = 会话内输入确认码（防伪造）；驳回 = 此处一键（AC-4.2）</span>
          </div>
          {pending.length > 0 && <span className="sh-badge">{pending.length} 条待审</span>}
          <button className="mini-btn" disabled={sweeping}
            onClick={sweep} title="超时未裁决的 pending 批量置 expired（幂等）">
            {sweeping ? '清理中…' : '一键清理已超时'}</button>
        </div>
        <div className="sh-card-body flush">
          <table className="sh-table">
            <thead><tr><th style={{ width: 56 }}>#</th><th style={{ width: 110 }}>时间</th><th>操作摘要</th><th style={{ width: 90 }}>剩余</th><th style={{ width: 150 }}>会话</th><th style={{ width: 72, textAlign: 'right' }}>操作</th></tr></thead>
            <tbody>
              {pending.length === 0 && <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 24 }}>（没有等待审批的操作）</td></tr>}
              {pending.map(p => (
                <tr key={p.id}>
                  <td className="num">{p.id}</td>
                  <td className="num">{(p.created_at || '').slice(5, 19).replace('T', ' ')}</td>
                  <td>{p.summary}</td>
                  <td><span className="sh-badge neutral">{ttlLeft(p)}</span></td>
                  <td><SessionLink sid={p.sid} jump={jump} truncate /></td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="mini-btn" disabled={busy === p.id}
                      onClick={() => deny(p.id)}>{busy === p.id ? '…' : '驳回'}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {decisions.length > 0 && (
        <div className="sh-card">
          <div className="sh-card-head">
            <div className="sh-card-titlewrap">
              <span className="sh-card-title">最近的审批决定</span>
            </div>
          </div>
          <div className="sh-card-body" style={{ fontSize: 'var(--fs-sm)', lineHeight: 2 }}>
            {decisions.map(d => (
              <div key={d.id}>
                <span style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }} className="muted">{(d.ts || '').slice(5, 19).replace('T', ' ')}</span>{' '}
                {humanize(d)}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function EgressDetail() {
  const [rows, setRows] = useState<{ host: string; n: number; denied: number; last: string }[]>([]);
  const [mode, setMode] = useState('');
  const [onDeny, setOnDeny] = useState('');
  const [askWait, setAskWait] = useState('');
  const [allow, setAllow] = useState<string[]>([]);
  const [grants, setGrants] = useState<{ sid: string; host: string; expires_at: string }[]>([]);
  const [busy, setBusy] = useState('');
  const [newHost, setNewHost] = useState('');
  const load = () => {
    void api<{ events: { ts: string; host: string; decision: string }[]; mode: string; on_deny?: string; ask_wait_s?: number; allow: string[]; grants: { sid: string; host: string; expires_at: string }[] }>('/api/admin/egress?n=100')
      .then(d => {
        setMode(d.mode); setOnDeny(d.on_deny ?? ''); setAskWait(String(d.ask_wait_s ?? ''));
        setAllow(d.allow ?? []); setGrants(d.grants ?? []);
        const m = new Map<string, { n: number; denied: number; last: string }>();
        for (const e of d.events) {
          if (e.host === 'llm-gw.internal') continue;
          const cur = m.get(e.host) ?? { n: 0, denied: 0, last: '' };
          cur.n++; if (!String(e.decision).startsWith('allow')) cur.denied++;
          cur.last = e.ts; m.set(e.host, cur);
        }
        setRows([...m.entries()].sort((a, b) => b[1].n - a[1].n).map(([host, v]) => ({ host, ...v })));
      })
      .catch(() => { });
  };
  useEffect(load, []);
  // 安全面策略反馈（AC-1.4）：写失败必须可见——此前静默吞错，用户以为改成功了
  const [polMsg, setPolMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const allowHost = async (host: string) => {
    if (!await askConfirm({ title: `将 ${host} 加入出口白名单？热生效（免重启），所有会话对该域的外发即放行。`, danger: false })) return;
    setBusy(host); setPolMsg(null);
    void api<{ allow: string[] }>('/api/admin/egress/allow', {
      method: 'POST', body: JSON.stringify({ host }),
    }).then(d => { setAllow(d.allow ?? []); setBusy('');
      setPolMsg({ t: `✓ ${host} 已入白名单（热生效）` }); })
      .catch(e => { setBusy(''); setPolMsg({ t: `放行失败：${String(e)}`, err: true }); });
  };
  /** 白名单池尾部手动添加（设计稿语义）：复用 allowHost（含确认弹窗）+清空输入 */
  const addAllow = async (host: string) => {
    await allowHost(host);
    setNewHost('');
  };
  const removeHost = (host: string) => {
    setBusy(host); setPolMsg(null);
    void api<{ allow: string[] }>(`/api/admin/egress/allow?host=${encodeURIComponent(host)}`, {
      method: 'DELETE',
    }).then(d => { setAllow(d.allow ?? []); setBusy('');
      setPolMsg({ t: `✓ ${host} 已移出白名单（热生效）` }); })
      .catch(e => { setBusy(''); setPolMsg({ t: `移除失败：${String(e)}`, err: true }); });
  };
  const revokeGrant = (sid: string, host: string) => {
    setBusy(host);
    void api('/api/admin/egress/grant/revoke', {
      method: 'POST', body: JSON.stringify({ sid, host }),
    }).then(() => { setBusy(''); load(); })   // C7：先等 revoke 落库再刷新
      .catch(e => { setBusy(''); setPolMsg({ t: `撤销授权失败：${String(e)}`, err: true }); });
  };
  /** 出口策略写（持久化+热生效）：mode 三态 / 拦截时两态 / 弹卡等待秒 */
  const putPolicy = (patch: Record<string, unknown>) => {
    setBusy('policy'); setPolMsg(null);
    void api('/api/admin/egress/policy', {
      method: 'PUT', body: JSON.stringify(patch),
    }).then(load).catch(e => {
      setBusy(''); setPolMsg({ t: `策略保存失败：${String(e)}`, err: true });
    });
  };
  const MODE_OPTS: { v: string; label: string; tip: string }[] = [
    { v: 'enforce', label: '强制', tip: '白名单外按下方「拦截时」策略处理' },
    { v: 'warn', label: '告警', tip: '白名单外放行但逐条告警（灰度期用）' },
    { v: 'off', label: '放开', tip: '全放行（仍走代理审计+凭证网关）' },
  ];
  return (
    <div className="sh-card">
      <div className="sh-card-head">
        <div className="sh-card-titlewrap">
          <span className="sh-card-title">窗口内外发的目标域</span>
          <span className="sh-card-sub">mode={mode || '-'} · 观测与管控一体（原「流量」tab 已并入此处）</span>
        </div>
        <span className={`sh-badge ${mode === 'enforce' ? 'ok' : mode === 'warn' ? '' : 'warn'}`}>
          {mode === 'enforce' ? '强制管控' : mode === 'warn' ? '告警模式' : '已放开（仍审计）'}
        </span>
      </div>
      <div className="sh-card-body">
        {/* 出口策略热切换条（保存即热生效） */}
        <div className="sh-policybar" style={{ marginBottom: 14 }}>
          <div style={{ minWidth: 260, flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 'var(--fs-md)', marginBottom: 4 }}>出口策略（保存即热生效，不杀在跑任务）</div>
            <div className="sh-ops-hint">任务级放开在会话「属性」面板（任务外联档位）；这里改的是全局基线。</div>
          </div>
          <div className="sh-ops-inline">
            <div className="sh-seg">
              {MODE_OPTS.map(o => (
                <button key={o.v} title={o.tip} disabled={busy === 'policy'}
                  className={`sh-seg-btn${mode === o.v ? ' on' : ''}`}
                  onClick={() => putPolicy({ mode: o.v })}>{o.label}</button>
              ))}
            </div>
          </div>
        </div>
        {mode === 'enforce' && (
          <div className="sh-ops-inline" style={{ marginBottom: 12, paddingLeft: 4 }}>
            <span className="sh-ops-hint" style={{ fontWeight: 600 }}>拦截时：</span>
            <div className="sh-seg">
              <button title="直接 403（agent 可见提示与申请指引）" disabled={busy === 'policy'}
                className={`sh-seg-btn${onDeny === 'deny' ? ' on-danger' : ''}`}
                onClick={() => putPolicy({ on_deny: 'deny' })}>直接拒绝</button>
              <button title="自动弹审批卡等你裁决，批准即放行本请求（默认）" disabled={busy === 'policy'}
                className={`sh-seg-btn${onDeny === 'ask' ? ' on' : ''}`}
                onClick={() => putPolicy({ on_deny: 'ask' })}>弹卡确认</button>
            </div>
            {onDeny === 'ask' && (
              <>
                <span className="sh-ops-hint">等待</span>
                <input className="sec-num sm" type="number" min={15} max={600}
                  autoComplete="off" value={askWait}
                  onChange={e => setAskWait(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && +askWait >= 15 && +askWait <= 600)
                      putPolicy({ ask_wait_s: +askWait });
                  }} />
                <span className="sh-ops-hint">秒（15-600，回车保存）</span>
              </>
            )}
          </div>
        )}
        {polMsg && <div className={polMsg.err ? 'admin-msg err' : 'admin-msg'} style={{ marginBottom: 10 }}>{polMsg.t}</div>}
        <table className="sh-table">
          <thead><tr><th>域名</th><th style={{ width: 80 }}>次数</th><th style={{ width: 120 }}>判定</th><th style={{ width: 130 }}>最近</th><th style={{ width: 110, textAlign: 'right' }}>操作</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={5} className="muted" style={{ textAlign: 'center', padding: 20 }}>（窗口内没有对外请求）</td></tr>}
            {rows.map(r => (
              <tr key={r.host}>
                <td className="num" style={{ fontWeight: 600 }}>{r.host}</td>
                <td className="num">{r.n}</td>
                <td>{r.denied
                  ? <span className="sh-badge danger">拒绝 {r.denied}/{r.n}</span>
                  : <span className="sh-badge ok">放行</span>}</td>
                <td className="num">{(r.last || '').slice(5, 19).replace('T', ' ')}</td>
                <td style={{ textAlign: 'right' }}>
                  {r.denied > 0 && !allow.includes(r.host) && (
                    <button
                      className="mini-btn"
                      disabled={busy === r.host}
                      onClick={() => allowHost(r.host)}
                      title="加入出口白名单（热生效，免重启）">+ 入白名单</button>
                  )}
                  {allow.includes(r.host) && <span className="sh-badge ok">已白名单</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* 白名单胶囊池（点 × 移除即热生效） */}
      <div className="sh-pool">
        <span className="sh-ops-hint" style={{ fontWeight: 600, marginRight: 4 }}>
          白名单（{allow.length} 域，点 × 移除即热生效）：
        </span>
        {allow.map(h => (
          <span key={h} className="sh-chip">
            {h}
            <button className="x" onClick={() => removeHost(h)}
              title={`移除 ${h}`}>×</button>
          </span>
        ))}
        {allow.length === 0 && <span className="sh-ops-hint">-</span>}
        <span className="sh-ops-inline" style={{ marginLeft: 'auto' }}>
          <input value={newHost} placeholder="添加白名单域…" autoComplete="off"
            style={{ width: 160, fontSize: 'var(--fs-sm)', fontFamily: 'var(--mono)' }}
            onChange={e => setNewHost(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && newHost.trim()) void addAllow(newHost.trim()); }} />
          <button className="mini-btn" disabled={!newHost.trim() || busy === newHost.trim()}
            onClick={() => void addAllow(newHost.trim())}>+ 加入</button>
        </span>
      </div>
      {grants.length > 0 && (
        <div className="sh-pool">
          <span className="sh-ops-hint" style={{ fontWeight: 600, marginRight: 4 }}>
            审批式临时授权（任务级限时，到期自动收回）：
          </span>
          {grants.map(g => (
            <span key={`${g.sid}:${g.host}`} className="sh-chip">
              {g.host} · {g.sid.slice(-6)} · 至 {g.expires_at.slice(5, 16).replace('T', ' ')}
              <button className="x" onClick={() => revokeGrant(g.sid, g.host)}
                title="立即收回（不等到期）">×</button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** vault 编辑器的字段布局（与后端 vault.FIELDS 对齐；secret 类掩码输入）。
 *  category（凭证库统一批）：自定义分类，datalist 建议由 VaultDetail 渲染 */
const VAULT_ROWS: { k: string; label: string; secret?: boolean }[] = [
  { k: 'username', label: '用户名' },
  { k: 'password', label: '密码', secret: true },
  { k: 'recovery', label: '恢复码', secret: true },
  { k: 'email', label: '邮箱' },
  { k: 'phone', label: '手机' },
  { k: 'twofa', label: '2FA' },
  { k: 'status', label: '状态' },
  { k: 'category', label: '分类' },
  { k: 'notes', label: '备注' },
];

function VaultDetail() {
  type Plat = { platform: string; user?: string; email?: string;
    category?: string; notes?: string; has_password: boolean; updated_at: string };
  type ResSecret = { key: string; set: boolean; bound: boolean;
    source: string; category?: string; desc?: string };
  const [data, setData] = useState<{ platforms: Plat[];
    resource_secrets?: ResSecret[];
    verify: { ok: boolean; entries: number; encrypted: boolean; error?: string } } | null>(null);
  const [editing, setEditing] = useState<string>('');
  const [q, setQ] = useState('');   // 本地搜索（平台/账号/备注）
  const [cat, setCat] = useState('');   // 分类筛选（''=全部；'__none'=未分类）
  const [creating, setCreating] = useState(false);   // 新增条目输入行开关
  const [newName, setNewName] = useState('');
  const load = () => {
    void api<typeof data>('/api/admin/vault').then(setData).catch(() => { });
  };
  useEffect(load, []);
  if (!data) return <div className="muted">读取中…</div>;
  const kw = q.trim().toLowerCase();
  const shownAll = data.platforms.filter(p =>
    !kw || p.platform.toLowerCase().includes(kw)
    || (p.user || p.email || '').toLowerCase().includes(kw)
    || (p.notes || '').toLowerCase().includes(kw));
  // 分类筛选 chips：动态集合（全部/未分类/各分类），按条数排序
  const catCount = new Map<string, number>();
  for (const p of data.platforms) {
    const c = (p.category || '').trim() || '__none';
    catCount.set(c, (catCount.get(c) ?? 0) + 1);
  }
  const cats = [...catCount.entries()].sort((a, b) => b[1] - a[1]);
  const shown = !cat ? shownAll
    : shownAll.filter(p => ((p.category || '').trim() || '__none') === cat);
  const resSecrets = data.resource_secrets ?? [];
  // 新增条目：保存前不在 platforms 里——合成临时行+编辑器（后端 put
  // 为 upsert，首次保存即建条目；重名前置校验避免误覆盖）
  const isNew = !!editing && !data.platforms.some(p => p.platform === editing);
  const startCreate = () => {
    const name = newName.trim();
    if (!name) return;
    if (data.platforms.some(p => p.platform === name)) {
      toast(`已有 ${name} 条目：在列表中点「编辑」即可`, false);
      return;
    }
    setCreating(false); setNewName(''); setEditing(name);
  };
  return (
    <div className="sh-card">
      <datalist id="vault-cat-suggest">
        {cats.filter(([c]) => c !== '__none').map(([c]) => <option key={c} value={c} />)}
      </datalist>
      <div className="sh-card-head">
        <div className="sh-card-titlewrap">
          <span className="sh-card-title">入库凭证（{data.platforms.length} 组）· 唯一管理面</span>
          <span className={`sh-badge ${data.verify.ok ? 'ok' : 'danger'}`}>
            {data.verify.ok ? '✓ 加密格式校验通过' : `⚠ 校验失败：${data.verify.error ?? '未知'}`}
          </span>
          <span className="sh-card-sub">点条目直接编辑；密钥字段留空=不改、输入即覆盖。agent 取用仍走审批门。</span>
        </div>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="搜索平台 / 账号 / 备注…"
          autoComplete="off" style={{ width: 180 }} />
        <button className="mini-btn" onClick={() => { setCreating(c => !c); setNewName(''); }}>
          {creating ? '取消新增' : '+ 新增凭证'}</button>
      </div>
      {cats.length > 1 && (
        <div className="sh-catrow">
          <button className={`sh-catchip ${!cat ? 'on' : ''}`} onClick={() => setCat('')}>
            全部 {data.platforms.length}</button>
          {cats.map(([c, n]) => (
            <button key={c} className={`sh-catchip ${cat === c ? 'on' : ''}`}
              onClick={() => setCat(cat === c ? '' : c)}>
              {c === '__none' ? '未分类' : c} {n}</button>
          ))}
        </div>
      )}
      {creating && (
        <div className="sh-pool">
          <span className="sh-ops-hint" style={{ fontWeight: 600 }}>新条目平台名：</span>
          <input value={newName} placeholder="如 github / deepseek_api" autoComplete="off"
            style={{ flex: 1, minWidth: 200, fontFamily: 'var(--mono)' }}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && newName.trim()) startCreate(); }} />
          <button className="btn sm" disabled={!newName.trim()}
            onClick={startCreate}>创建并编辑</button>
        </div>
      )}
      <div className="sh-card-body flush">
        <table className="sh-table">
          <thead><tr><th>平台</th><th style={{ width: 90 }}>分类</th><th>账号</th><th>备注</th><th style={{ width: 90 }}>密码状态</th><th style={{ width: 110 }}>更新日期</th><th style={{ width: 72, textAlign: 'right' }}>操作</th></tr></thead>
          <tbody>
            {shown.length === 0 && <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 24 }}>（无匹配凭证）</td></tr>}
            {isNew && (
              <Fragment>
                <tr>
                  <td style={{ fontFamily: 'var(--mono)', fontWeight: 600 }}>{editing}</td>
                  <td className="muted">—</td>
                  <td className="muted num">—</td>
                  <td className="muted">—</td>
                  <td><span className="sh-badge">新建</span></td>
                  <td className="muted num">—</td>
                  <td style={{ textAlign: 'right' }}><button className="mini-btn" onClick={() => setEditing('')}>收起</button></td>
                </tr>
                <VaultEditor platform={editing} onDone={() => { setEditing(''); load(); }} isNew />
              </Fragment>
            )}
            {shown.map(p => (
              <Fragment key={p.platform}>
                <tr>
                  <td style={{ fontFamily: 'var(--mono)', fontWeight: 600 }}>{p.platform}</td>
                  <td>{p.category?.trim()
                    ? <span className="sh-badge">{p.category}</span>
                    : <span className="muted">—</span>}</td>
                  <td className="muted num">{p.user || p.email || '—'}</td>
                  <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={p.notes || ''}>{p.notes || <span className="muted">—</span>}</td>
                  <td>{p.has_password ? <span className="sh-badge ok">已存</span> : <span className="sh-badge danger">缺</span>}</td>
                  <td className="num">{(p.updated_at || '').slice(0, 10)}</td>
                  <td style={{ textAlign: 'right' }}><button className="mini-btn" onClick={() => setEditing(editing === p.platform ? '' : p.platform)}>
                    {editing === p.platform ? '收起' : '编辑'}
                  </button></td>
                </tr>
                {editing === p.platform && (
                  <VaultEditor platform={p.platform} onDone={() => { setEditing(''); load(); }} />)}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {resSecrets.length > 0 && (
        <div className="sh-ressec">
          <div className="sh-ressec-head">
            <span className="sh-card-title" style={{ fontSize: 'inherit' }}>资源密钥（{resSecrets.length}）</span>
            <span className="sh-card-sub">资源卡绑定消费的平台集成密钥——在资源 Hub 的资源卡里轮换/管理</span>
            <button className="mini-btn" onClick={() => { location.hash = '#/admin/resources'; }}>前往资源 Hub →</button>
          </div>
          <div className="sh-ressec-body">
            {resSecrets.map(r => (
              <div key={r.key} className="sh-resrow">
                <span className="mono" style={{ fontSize: 'var(--fs-xs)' }}>{r.key}</span>
                <span>{r.source}{r.bound ? '' : <span className="sh-badge danger" style={{ marginLeft: 6 }}>源卡已删</span>}</span>
                {r.category?.trim() ? <span className="sh-badge">{r.category}</span> : <span className="muted">—</span>}
                {r.set
                  ? <span className="sh-badge ok">已加密</span>
                  : <span className="sh-badge warn">未设置</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** 行内编辑器：拉掩码视图回填明文字段；提交只送改动的键（merge 语义） */
function VaultEditor({ platform, onDone, isNew = false }: { platform: string; onDone: () => void; isNew?: boolean }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [orig, setOrig] = useState<Record<string, string>>({});
  const [meta, setMeta] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (isNew) {   // 新建：无后端条目，全空初始化（首次保存即创建）
      const init: Record<string, string> = {};
      for (const r of VAULT_ROWS) init[r.k] = '';
      setDraft(init); setOrig(init);
      return;
    }
    void api<Record<string, string>>(`/api/admin/vault/${encodeURIComponent(platform)}`)
      .then(d => {
        setMeta({ password: d.password ?? '', recovery: d.recovery ?? '' });
        const init: Record<string, string> = {};
        for (const r of VAULT_ROWS) {
          init[r.k] = r.secret ? '' : (typeof d[r.k] === 'string' ? d[r.k] : '');
        }
        setDraft(init); setOrig(init);
      }).catch(() => { });
  }, [platform, isNew]);
  const save = () => {
    const fields: Record<string, string> = {};
    for (const r of VAULT_ROWS) {
      const v = (draft[r.k] ?? '').trim();
      if (r.secret) { if (v) fields[r.k] = v; continue; }   // 密钥：留空=不改
      if (v !== (orig[r.k] ?? '').trim()) fields[r.k] = v;  // 明文：diff 即改（空串=清除）
    }
    if (!Object.keys(fields).length) { onDone(); return; }
    setBusy(true);
    void api(`/api/admin/vault/${encodeURIComponent(platform)}`, {
      method: 'PUT', body: JSON.stringify({ fields }),
    }).then(onDone).catch(e => toast(`保存失败：${e instanceof Error ? e.message : e}`, false))
      .finally(() => setBusy(false));
  };
  const del = async () => {
    if (!await askConfirm({ title: `删除 ${platform} 条目？不可恢复（AES 密文一并删除）。`, danger: true })) return;
    setBusy(true);
    void api(`/api/admin/vault/${encodeURIComponent(platform)}`, { method: 'DELETE' })
      .then(onDone).catch(e => toast(`删除失败：${e instanceof Error ? e.message : e}`, false))
      .finally(() => setBusy(false));
  };
  return (
    <tr className="sh-vrow"><td colSpan={7}>
      <div className="sh-vdrawer">
        <div className="sh-vgrid">
          {VAULT_ROWS.map(r => (
            <div key={r.k} className="sh-vfield">
              <label>{r.label}{r.secret ? '（留空=不改）' : ''}</label>
              <input style={{ width: '100%' }}
                type={r.secret ? 'password' : 'text'} autoComplete="off"
                list={r.k === 'category' ? 'vault-cat-suggest' : undefined}
                placeholder={r.k === 'category' ? '如 邮箱 / 模型 / 搜索 / 支付' : r.secret
                  ? (meta?.[r.k] ? `已设置（${String(meta[r.k]).includes('位') ? meta[r.k] : '留空不改，输入即覆盖'}）` : '未设置')
                  : ''}
                onChange={e => setDraft(d => ({ ...d, [r.k]: e.target.value }))}
                value={draft[r.k] ?? ''} />
            </div>
          ))}
        </div>
        <div className="sh-ops-inline">
          <button className="btn ghost" disabled={busy} onClick={save}>{busy ? '保存中…' : '保存'}</button>
          <button className="mini-btn" disabled={busy} onClick={onDone}>取消</button>
          <button className="mini-btn danger-link"
            disabled={busy} onClick={del}>删除条目</button>
        </div>
      </div>
    </td></tr>
  );
}

function CanaryDetail({ locked, reload, jump }: {
  locked: { sid: string; reason: string }[];
  reload: () => Promise<void>; jump: (s: string | null) => void;
}) {
  const [busy, setBusy] = useState('');
  const unlock = async (sid: string) => {
    if (!await askConfirm({ title: `解锁会话 ${sid}？只有确认过警报原因后才应解锁（蜜罐命中=有人/有注入在用诱饵凭证）。`, danger: false })) return;
    setBusy(sid);
    try { await api(`/api/sessions/${sid}/unlock`, { method: 'POST' }); await reload(); }
    catch (e) { toast(`解锁失败: ${e}`, false); }
    finally { setBusy(''); }
  };
  return (
    <div className="sh-card">
      <div className="sh-card-head">
        <div className="sh-card-titlewrap">
          <span className="sh-card-title">{locked.length ? `${locked.length} 个会话因警报锁定` : '没有触发警报的会话'}</span>
          <span className="sh-card-sub">诱饵凭证被使用=提示词注入或盗用的强信号，解锁前先核会话里发生了什么</span>
        </div>
        <span className={`sh-badge ${locked.length ? 'danger' : 'ok'}`}>
          {locked.length ? '⚠ 警报待处理' : '哨兵正常 · 未命中'}
        </span>
      </div>
      <div className="sh-card-body">
        {locked.length === 0 ? (
          <div className="sh-empty">
            <div className="sh-empty-icon">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
            </div>
            <div className="sh-empty-title">全部正常 · 0 个会话触发蜜罐熔断</div>
            <div className="sh-empty-desc">
              诱饵埋在每个会话的 <span className="sh-pill">notes/.canary_tokens.md</span>，任何工具读到并使用都会立即熔断。
            </div>
          </div>
        ) : (
          <table className="sh-table">
            <thead><tr><th>会话</th><th>触发原因</th><th style={{ width: 90, textAlign: 'right' }}>操作</th></tr></thead>
            <tbody>
              {locked.map(l => (
                <tr key={l.sid}>
                  <td><SessionLink sid={l.sid} jump={jump} /></td>
                  <td className="muted">{l.reason}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="mini-btn" disabled={busy === l.sid}
                      onClick={() => void unlock(l.sid)}>{busy === l.sid ? '解锁中…' : '解锁'}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}


/** 七轮补 UI：P10 per-target 三档策略管理卡（list/改档/删——全链此前
 *  零 UI；suggestions 是 SKILL.md targets 声明的只读建议）。 */
function TargetPolicyDetail() {
  interface Policy {
    id: number; match: string; kind: string; mode: string;
    scope_note?: string; created_from?: string;
  }
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [suggestions, setSuggestions] = useState<Policy[]>([]);
  const [msg, setMsg] = useState('');
  const [adding, setAdding] = useState({ match: '', kind: 'host', mode: 'always' });
  const load = async () => {
    try {
      const d = await api<{ policies: Policy[]; suggestions: Policy[] }>(
        '/api/admin/target-policy');
      setPolicies(d.policies);
      setSuggestions(d.suggestions || []);
    } catch (e) {
      setMsg(`加载失败：${e instanceof Error ? e.message : e}`);
    }
  };
  useEffect(() => { void load(); }, []);
  const modeZh: Record<string, string> = { always: '常设放行', never: '永久拒绝', ask: '每次询问' };
  const kindZh: Record<string, string> = { host: '域名', action: '动作' };
  const segCls = (m: string) => m === 'always' ? 'on-ok' : m === 'never' ? 'on-danger' : 'on';
  return (
    <div className="sh-card">
      <div className="sh-card-head">
        <div className="sh-card-titlewrap">
          <span className="sh-card-title">目标放行/拒绝策略（{policies.length}）</span>
          <span className="sh-card-sub">审批卡「始终允许」落档于此；点击档位即热改，三档窄化即时生效</span>
        </div>
        <button className="mini-btn" onClick={() => void load()}>↻ 刷新</button>
      </div>
      <div className="sh-card-body flush">
        <table className="sh-table">
          <thead><tr><th style={{ width: 110 }}>粒度类型</th><th>匹配目标（Host 或 动作:指纹）</th><th style={{ width: 300 }}>常设判定档位（点击即热改）</th><th style={{ width: 64, textAlign: 'right' }}>操作</th></tr></thead>
          <tbody>
            {policies.length === 0 && (
              <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 24 }}>
                暂无策略——审批卡「始终允许」或下方手工添加。
              </td></tr>
            )}
            {policies.map(p => (
              <tr key={p.id}>
                <td><span className="sh-badge neutral">[{kindZh[p.kind] || p.kind}]</span></td>
                <td className="num" style={{ fontWeight: 600 }}>
                  {p.match}
                  {p.scope_note ? <span className="muted" style={{ fontWeight: 400 }}>（{p.scope_note}）</span> : null}
                  {p.created_from?.startsWith('approval:')
                    ? <span className="muted" style={{ fontWeight: 400 }}> · 来自审批卡</span> : null}
                </td>
                <td>
                  <div className="sh-seg">
                    {(['always', 'ask', 'never'] as const).map(m => (
                      <button key={m} className={`sh-seg-btn${p.mode === m ? ` ${segCls(m)}` : ''}`}
                        onClick={async () => {
                          await api(`/api/admin/target-policy/${p.id}`,
                            { method: 'PATCH', body: JSON.stringify({ mode: m }) });
                          await load();
                        }}>{modeZh[m]}</button>
                    ))}
                  </div>
                </td>
                <td style={{ textAlign: 'right' }}>
                  <button className="mini-btn danger-link"
                    onClick={async () => {
                      if (!await askConfirm({ title: `删除策略「${p.match}」？`, danger: true })) return;
                      await api(`/api/admin/target-policy/${p.id}`, { method: 'DELETE' });
                      await load();
                    }}>删</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* 底部新增策略栏 */}
        <div className="sh-pool">
          <input value={adding.match} placeholder="输入 host 或 动作:指纹，如 *.openai.com" autoComplete="off"
            onChange={e => setAdding(a => ({ ...a, match: e.target.value }))}
            style={{ flex: 1, minWidth: 220 }} />
          <select value={adding.kind} autoComplete="off"
            onChange={e => setAdding(a => ({ ...a, kind: e.target.value }))}>
            <option value="host">[域名] 目标域名</option>
            <option value="action">[动作] 敏感动作</option>
          </select>
          <select value={adding.mode} autoComplete="off"
            onChange={e => setAdding(a => ({ ...a, mode: e.target.value }))}>
            <option value="always">常设放行</option>
            <option value="ask">每次询问</option>
            <option value="never">永久拒绝</option>
          </select>
          <button className="btn sm" onClick={async () => {
            if (!adding.match.trim()) return;
            try {
              await api('/api/admin/target-policy',
                { method: 'POST', body: JSON.stringify(adding) });
              setAdding(a => ({ ...a, match: '' }));
              await load();
            } catch (e) { setMsg(`添加失败：${e instanceof Error ? e.message : e}`); }
          }}>+ 添加策略</button>
        </div>
        {suggestions.length > 0 && (
          <div className="sh-pool">
            <span className="sh-ops-hint" style={{ fontWeight: 600, marginRight: 4 }}>
              skill 声明的网络目标（只读参考）：
            </span>
            {suggestions.slice(0, 8).map(s => (
              <span key={s.match} className="sh-chip"
                title={`来自 ${s.created_from}`}>{s.match}</span>
            ))}
          </div>
        )}
        {msg ? <div className="form-msg" style={{ margin: '10px 16px' }}>{msg}</div> : null}
      </div>
    </div>
  );
}
