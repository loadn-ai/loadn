// 资源中心（管理中心「资源」tab）：平台原生功能。
// 视觉：逻辑服务分组的卡片墙（而非扁平 14 行表）——每卡=一个真实服务，
// 内含端点字段（点击编辑）+密钥 chip（只写不读）+独立测试与状态灯。
// 样式类在 index.css「资源中心」段；安全语义不变：密钥值永不出后端。
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { toast } from '../stores/toasts';
import { Lock, Zap, Key, Shield } from './icons';

interface ServiceItem { key: string; label: string; note: string; value: string; kind: string }
interface SecretItem { key: string; set: boolean }
interface CustomSvc { name: string; url: string; note: string; key_set: boolean }
interface McpServer { name: string; spec: Record<string, any>; sessions_overriding: number; session_only?: boolean }
interface VaultPlatform { platform: string; user?: string; email?: string; has_password: boolean; updated_at: string }
interface Overview {
  services: ServiceItem[];
  secrets: SecretItem[];
  custom: CustomSvc[];
  mcp: { servers: McpServer[] };
  vault: { platforms: VaultPlatform[]; verify: { ok: boolean; entries: number; encrypted: boolean; error?: string } };
}

/** 逻辑服务编组：一张卡 = 一个真实服务（多字段 + 关联密钥） */
interface CardDef {
  id: string; name: string; icon: string; note?: string;
  fields: { key: string; k: string; ph: string }[];     // k=字段短名 ph=占位提示
  secrets: string[];                                     // 关联密钥 key
  ping?: string[];                                       // ping 目标
}
const GROUPS: { title: string; cards: CardDef[] }[] = [
  {
    title: '基础设施', cards: [
      {
        id: 'ocr', name: 'OCR 识别', icon: '🔤', note: '图片 / PDF / Office 文本识别',
        fields: [{ key: 'ocr_url', k: '端点', ph: 'http://127.0.0.1:8686' }], secrets: [], ping: ['ocr'],
      },
      {
        id: 'sandbox', name: 'AIO 沙箱', icon: '🧳', note: '浏览器 / 命令行 / 文件 操作环境',
        fields: [
          { key: 'sandbox_url', k: '端点', ph: 'http://127.0.0.1:21111' },
          { key: 'cdp_url', k: 'CDP', ph: 'http://127.0.0.1:21111/cdp' }],
        secrets: ['sandbox_api_key'], ping: ['sandbox', 'sandbox_mcp'],
      },
      { id: 'proxy', name: '出网代理', icon: '🛰️', note: '出海请求经此转发（clash 等）', fields: [{ key: 'proxy', k: '地址', ph: 'http://127.0.0.1:7890' }], secrets: [], ping: ['proxy'] },
      { id: 'adb', name: 'Android 真机', icon: '📱', note: 'adb 无线调试', fields: [{ key: 'adb_addr', k: '地址', ph: '192.0.2.78:5555' }], secrets: [] },
    ],
  },
  {
    title: '通信', cards: [
      {
        id: 'sms', name: '短信查询', icon: '💬', note: '真机收验证码',
        fields: [
          { key: 'sms_url', k: '端点', ph: 'https://sms.example.test:30443' },
          { key: 'sms_phone', k: '手机号', ph: '+86 138…' }],
        secrets: ['sms_token'], ping: ['sms'],
      },
      {
        id: 'mail', name: '主邮箱', icon: '📮', note: '注册 / 登录收验证码（126/163 需授权码）',
        fields: [
          { key: 'mail_imap', k: 'IMAP', ph: 'imap.126.com:993' },
          { key: 'mail_smtp', k: 'SMTP', ph: 'smtp.126.com:465' },
          { key: 'mail_user', k: '账号', ph: 'you@example.com' }],
        secrets: ['mail_auth_code'], ping: ['mail'],
      },
      {
        id: 'textr', name: 'Textr 虚拟号', icon: '🇺🇸', note: '美国号码收码',
        fields: [{ key: 'textr_email', k: '账号', ph: 'you@example.com' }],
        secrets: ['textr_password'],
      },
    ],
  },
  {
    title: 'AI 能力', cards: [
      {
        id: 'vlm', name: '视觉模型', icon: '👁️', note: '图片理解（截图分析等）',
        fields: [
          { key: 'vlm_api_base', k: 'API', ph: 'https://ark.cn-beijing.volces.com/api/v3' },
          { key: 'vlm_model', k: '模型', ph: 'doubao-seed-2-1-turbo' }],
        secrets: ['vlm_api_key'], ping: ['vlm'],
      },
      {
        id: 'search', name: '网页搜索', icon: '🔎', note: '博查（中文）/ 智谱（档位计费）',
        fields: [{ key: 'zhipu_engine', k: '智谱档', ph: 'search_pro' }],
        secrets: ['bocha_key', 'zhipu_key'], ping: ['bocha', 'zhipu'],
      },
      { id: 'captcha', name: '过验证码', icon: '🧩', note: '2Captcha（图形码 / reCAPTCHA）', fields: [], secrets: ['twocaptcha_key'], ping: ['twocaptcha'] },
    ],
  },
];

const SECRET_ZH: Record<string, string> = {
  sandbox_api_key: 'API Key', sms_token: 'Token', mail_auth_code: '授权码',
  vlm_api_key: 'API Key', twocaptcha_key: 'API Key',
  bocha_key: '博查 Key', zhipu_key: '智谱 Key', textr_password: '密码',
};
type Section = 'service' | 'mcp' | 'vault';

export default function ResourcesPanel() {
  const [data, setData] = useState<Overview | null>(null);
  const [sec, setSec] = useState<Section>('service');
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editVal, setEditVal] = useState('');
  const [secretInput, setSecretInput] = useState<string | null>(null);
  const [secretVal, setSecretVal] = useState('');
  const [pinging, setPinging] = useState<string | null>(null);
  const [pingRes, setPingRes] = useState<Record<string, { ok: boolean; msg: string; ms?: number }>>({});
  // AC-5.10f（P2-6）：探测历史（后端 ping_history 聚合）——未点探测也能看到
  // 每目标最近一次结果与 24h 失败次数，「昨晚某服务通不通」可回查
  const [hist, setHist] = useState<Record<string, {
    last: { ok: boolean; ms?: number | null; msg?: string | null; ts: string };
    fails_24h: number;
  }>>({});
  const [vSearch, setVSearch] = useState('');
  const [svcAdd, setSvcAdd] = useState<{ name: string; url: string; note: string } | null>(null);

  const [loadErr, setLoadErr] = useState('');
  const loadHist = async () => {
    try {
      const d = await api<{ items: { target: string; last: { ok: boolean; ms?: number | null; msg?: string | null; ts: string }; fails_24h: number }[] }>(
        '/api/admin/resources/history');
      setHist(Object.fromEntries(d.items.map(it => [it.target, it])));
    } catch { /* 历史是增强面：失败不阻断概览 */ }
  };
  const load = async () => {
    try { setData(await api<Overview>('/api/admin/resources')); setLoadErr(''); }
    catch (e) { setLoadErr(`资源概览加载失败：${String(e)}`); }  // 早退分支前必须落独立错误态
    void loadHist();
  };
  useEffect(() => { void load(); }, []);

  // AC-5.5：操作反馈改全局 toast（右上浮层 2.6s 自清，不再挤占面板常驻 banner）
  const flash = (text: string, ok = true) => {
    toast(text, ok);
  };

  const saveService = async (key: string, value: string) => {
    try {
      await api('/api/admin/resources/service', { method: 'POST', body: JSON.stringify({ key, value }) });
      flash('✓ 已保存'); setEditKey(null); await load();
    } catch (e) { flash(`保存失败：${String(e)}`, false); }
  };
  const saveSecret = async (key: string) => {
    const v = secretVal.trim();
    if (!v) return;
    try {
      await api('/api/admin/resources/secret', { method: 'POST', body: JSON.stringify({ key, value: v }) });
      flash('✓ 已加密保存（AES-GCM）'); setSecretInput(null); setSecretVal(''); await load();
    } catch (e) { flash(`保存失败：${String(e)}`, false); }
  };
  const ping = async (card: CardDef) => {
    if (!card.ping?.length) return;
    setPinging(card.id);
    try {
      const d = await api<{ results: Record<string, { ok: boolean; msg: string; ms?: number }> }>(
        '/api/admin/resources/test', { method: 'POST', body: JSON.stringify({ only: card.ping }) });
      setPingRes(r => ({ ...r, ...d.results }));
      void loadHist();   // 本次结果已落库，重拉聚合
    } catch (e) { flash(`探测失败：${String(e)}`, false); }
    finally { setPinging(null); }
  };
  const pingSvc = async (name: string) => {
    setPinging(`svc:${name}`);
    try {
      const d = await api<{ results: Record<string, { ok: boolean; msg: string; ms?: number }> }>(
        '/api/admin/resources/test', { method: 'POST', body: JSON.stringify({ only: [`svc:${name}`] }) });
      setPingRes(r => ({ ...r, ...d.results }));
      void loadHist();
    } catch (e) { flash(`探测失败：${String(e)}`, false); }
    finally { setPinging(null); }
  };
  const upsertCustom = async (body: { name: string; url: string; note?: string }) => {
    try {
      await api('/api/admin/resources/custom', { method: 'POST', body: JSON.stringify(body) });
      flash('✓ 已保存（下一 turn 起注入任务环境）'); setSvcAdd(null); setEditKey(null); await load();
    } catch (e) { flash(`保存失败：${String(e)}`, false); }
  };
  const delCustom = async (name: string) => {
    if (!confirm(`删除自定义服务「${name}」？（vault 中的密钥一并清除）`)) return;
    try {
      await api(`/api/admin/resources/custom?name=${encodeURIComponent(name)}`, { method: 'DELETE' });
      flash('✓ 已删除'); await load();
    } catch (e) { flash(`删除失败：${String(e)}`, false); }
  };
  const pingAll = async () => {
    const all = GROUPS.flatMap(g => g.cards).flatMap(c => c.ping ?? []);
    if (!all.length) return;
    setPinging('__all__');
    try {
      const d = await api<{ results: Record<string, { ok: boolean; msg: string; ms?: number }> }>(
        '/api/admin/resources/test', { method: 'POST', body: JSON.stringify({ only: all }) });
      setPingRes(d.results);
      void loadHist();
    } catch (e) { flash(`探测失败：${String(e)}`, false); }
    finally { setPinging(null); }
  };

  // AC-1.3：凭证库唯一编辑面收敛到安全 tab（八字段 merge 编辑器）；本页只读概览。
  const gotoVaultHome = () => { location.hash = '#/admin/security'; };
  // AC-2.2：MCP 唯一管理面收敛到工具 tab（全类型编辑器）；saveMcp/表单已删。

  if (!data) return loadErr ? (
    <div className="admin-body">
      <div className="admin-msg err">{loadErr}
        <button className="link" style={{ marginLeft: 8 }} onClick={() => void load()}>重试</button>
      </div>
    </div>
  ) : <div className="admin-body muted">加载中…</div>;
  const valOf = (k: string) => data.services.find(s => s.key === k)?.value ?? '';
  const secretSet = (k: string) => data.secrets.find(s => s.key === k)?.set ?? false;
  // 本次探测结果优先，无则回退历史最近一次（卡上常驻）
  const stOf = (p: string): { ok: boolean; msg?: string; ms?: number } | undefined =>
    pingRes[p] ?? (hist[p]
      ? { ok: hist[p].last.ok, msg: hist[p].last.msg ?? undefined, ms: hist[p].last.ms ?? undefined }
      : undefined);
  const cardStatus = (c: CardDef) => {
    if (!c.ping?.length) return null;
    const rs = c.ping.map(p => stOf(p)).filter(Boolean) as { ok: boolean; msg?: string; ms?: number }[];
    if (!rs.length) return <span className="res-status">未测</span>;
    const ok = rs.every(r => r.ok);
    const ms = Math.max(...rs.map(r => r.ms ?? 0));
    const fails = c.ping.reduce((s, p) => s + (hist[p]?.fails_24h ?? 0), 0);
    const lastTs = c.ping.map(p => hist[p]?.last.ts).filter(Boolean).sort().pop();
    return <span className={`res-status ${ok ? 'ok' : 'fail'}`}>
      {ok ? `✓ ${ms ? `${ms}ms` : '正常'}` : `✗ ${rs.find(r => !r.ok)?.msg?.slice(0, 24) ?? '失败'}`}
      {lastTs ? ` · ${lastTs.slice(5, 16).replace('T', ' ')}` : ''}
      {fails > 0 ? ` · 24h败${fails}` : ''}
    </span>;
  };

  const vFiltered = data.vault.platforms.filter(p =>
    !vSearch || p.platform.toLowerCase().includes(vSearch.toLowerCase())
    || (p.user || '').toLowerCase().includes(vSearch.toLowerCase())
    || (p.email || '').toLowerCase().includes(vSearch.toLowerCase()));

  return (
    <div className="admin-body">
      {/* 分区切换 + 汇总 */}
      <div className="res-toolbar">
        {([['service', '服务'], ['mcp', 'MCP'], ['vault', `凭证库`]] as [Section, string][]).map(([k, lb]) => (
          <button key={k} className={`tab ${sec === k ? 'on' : ''}`} onClick={() => setSec(k)}>{lb}</button>
        ))}
        <span className="spacer" />
        <span className="muted" style={{ fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <Lock size={13} /> 密钥 AES-GCM 加密存储 · 明文不落配置、不回显
        </span>
      </div>

      {/* ============ 服务（分组卡片墙） ============ */}
      {sec === 'service' && (
        <>
          <div className="res-toolbar" style={{ marginBottom: 0 }}>
            <button className="res-btn" style={{ fontSize: 12, padding: '5px 12px' }}
              disabled={!!pinging} onClick={() => void pingAll()}>
              {pinging === '__all__' ? '探测中…' : <><Zap size={13} style={{ verticalAlign: -2 }} /> 全部探测</>}
            </button>
            <button className="res-btn" style={{ fontSize: 12, padding: '5px 12px' }}
              onClick={() => setSvcAdd({ name: '', url: '', note: '' })}>＋ 新增服务</button>
            <span className="muted" style={{ fontSize: 12 }}>
              {Object.keys(pingRes).length
                ? `${Object.values(pingRes).filter(r => r.ok).length}/${Object.keys(pingRes).length} 项在线`
                : Object.keys(hist).length
                  ? `最近：${Object.values(hist).filter(h => h.last.ok).length}/${Object.keys(hist).length} 项在线（历史）`
                  : '点卡片上的「测试」逐项探测'}
            </span>
          </div>
          {svcAdd && (
            <div className="res-addbar focused">
              <input placeholder="名称（小写，如 jina-api）" style={{ width: 150 }} value={svcAdd.name}
                onChange={e => setSvcAdd({ ...svcAdd, name: e.target.value })} />
              <input placeholder="端点 https://…（http(s)）" style={{ flex: 1, minWidth: 200 }} value={svcAdd.url}
                onChange={e => setSvcAdd({ ...svcAdd, url: e.target.value })} />
              <input placeholder="备注（可选）" style={{ width: 150 }} value={svcAdd.note}
                onChange={e => setSvcAdd({ ...svcAdd, note: e.target.value })} />
              <button className="res-btn"
                disabled={!svcAdd.name.trim() || !svcAdd.url.trim()}
                onClick={() => void upsertCustom(svcAdd)}>保存</button>
              <button className="res-btn" onClick={() => setSvcAdd(null)}>取消</button>
            </div>
          )}
          {GROUPS.map(g => (
            <div key={g.title}>
              <div className="res-group-title">{g.title}</div>
              <div className="res-grid">
                {g.cards.map(c => (
                  <div key={c.id} className="res-card">
                    <div className="res-head">
                      <span style={{ fontSize: 16 }}>{c.icon}</span>
                      <span className="res-name">{c.name}</span>
                      {cardStatus(c)}
                    </div>
                    {c.note && <div className="res-note" style={{ marginTop: -4 }}>{c.note}</div>}
                    {c.fields.map(f => (
                      <div key={f.key} className="res-field">
                        <span className="res-k">{f.k}</span>
                        {editKey === f.key ? (
                          <input value={editVal} autoFocus placeholder={f.ph}
                            onChange={e => setEditVal(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') void saveService(f.key, editVal);
                              if (e.key === 'Escape') setEditKey(null);
                            }} />
                        ) : (
                          <span className={`res-v ${valOf(f.key) ? '' : 'empty'}`}
                            title={`${valOf(f.key) || f.ph}（点击编辑）`}
                            onClick={() => { setEditKey(f.key); setEditVal(valOf(f.key)); }}>
                            {valOf(f.key) || `（${f.ph}）`}
                          </span>
                        )}
                        {editKey === f.key && (
                          <>
                            <button className="res-btn" onClick={() => void saveService(f.key, editVal)}>存</button>
                            <button className="res-btn" onClick={() => setEditKey(null)}>取消</button>
                          </>
                        )}
                      </div>
                    ))}
                    {c.secrets.length > 0 && (
                      <div className="res-actions">
                        {c.secrets.map(sk => secretInput === sk ? (
                          <span key={sk} style={{ display: 'flex', gap: 4 }}>
                            <input type="password" autoFocus value={secretVal} placeholder="输入新值"
                              style={{
                                background: 'var(--bg3)', border: '1px solid var(--accent)', borderRadius: 7,
                                padding: '3px 8px', fontSize: 12, color: 'var(--text)', outline: 'none', width: 130,
                                boxShadow: '0 0 0 3px var(--accent-ring)',
                              }}
                              onChange={e => setSecretVal(e.target.value)}
                              onKeyDown={e => {
                                if (e.key === 'Enter') void saveSecret(sk);
                                if (e.key === 'Escape') { setSecretInput(null); setSecretVal(''); }
                              }} />
                            <button className="res-btn" onClick={() => void saveSecret(sk)}>存</button>
                          </span>
                        ) : (
                          <span key={sk} className={`res-secret-chip ${secretSet(sk) ? '' : 'unset'}`}
                            title={`${SECRET_ZH[sk]}：点击设置（只写不读，${secretSet(sk) ? '已加密保存' : '未设置'}）`}
                            onClick={() => { setSecretInput(sk); setSecretVal(''); }}>
                            <><Key size={12} style={{ verticalAlign: -2 }} /> {SECRET_ZH[sk]} {secretSet(sk) ? '已加密' : '未设'}</>
                          </span>
                        ))}
                      </div>
                    )}
                    {(c.ping?.length ?? 0) > 0 && (
                      <div className="res-actions">
                        <button className="res-btn" disabled={pinging === c.id}
                          onClick={() => void ping(c)}>{pinging === c.id ? '测试中…' : '测试'}</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
          <div>
            <div className="res-group-title">自定义服务</div>
            <div className="res-grid">
              {data.custom.length === 0 && (
                <div className="res-card" style={{ gridColumn: '1 / -1', borderStyle: 'dashed' }}>
                  <span className="muted" style={{ fontSize: 12 }}>
                    （无自定义服务——上方「＋ 新增服务」添加；端点会以
                    <code> LOADN_SVC_&lt;名称&gt;_URL </code>注入任务环境）
                  </span>
                </div>
              )}
              {data.custom.map(c => (
                <div key={c.name} className="res-card">
                  <div className="res-head">
                    <span style={{ fontSize: 16 }}>🧩</span>
                    <span className="res-name">{c.name}</span>
                    {stOf(`svc:${c.name}`)
                      ? <span className={`res-status ${stOf(`svc:${c.name}`)!.ok ? 'ok' : 'fail'}`}>
                          {stOf(`svc:${c.name}`)!.ok ? '✓ 正常' : `✗ ${stOf(`svc:${c.name}`)!.msg?.slice(0, 24)}`}
                          {hist[`svc:${c.name}`]?.last.ts ? ` · ${hist[`svc:${c.name}`].last.ts.slice(5, 16).replace('T', ' ')}` : ''}
                          {(hist[`svc:${c.name}`]?.fails_24h ?? 0) > 0 ? ` · 24h败${hist[`svc:${c.name}`].fails_24h}` : ''}
                        </span>
                      : <span className="res-status">未测</span>}
                    <button className="res-btn" style={{ marginLeft: 'auto' }}
                      onClick={() => void delCustom(c.name)}>删</button>
                  </div>
                  {c.note && <div className="res-note" style={{ marginTop: -4 }}>{c.note}</div>}
                  <div className="res-field">
                    <span className="res-k">端点</span>
                    {editKey === `custom:${c.name}` ? (
                      <>
                        <input value={editVal} autoFocus
                          onChange={e => setEditVal(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') void upsertCustom({ name: c.name, url: editVal, note: c.note });
                            if (e.key === 'Escape') setEditKey(null);
                          }} />
                        <button className="res-btn"
                          onClick={() => void upsertCustom({ name: c.name, url: editVal, note: c.note })}>存</button>
                        <button className="res-btn" onClick={() => setEditKey(null)}>取消</button>
                      </>
                    ) : (
                      <span className="res-v" title={`${c.url}（点击编辑）`}
                        onClick={() => { setEditKey(`custom:${c.name}`); setEditVal(c.url); }}>
                        {c.url}
                      </span>
                    )}
                  </div>
                  <div className="res-actions">
                    {secretInput === `svc:${c.name}` ? (
                      <span style={{ display: 'flex', gap: 4 }}>
                        <input type="password" autoFocus value={secretVal} placeholder="输入密钥"
                          onChange={e => setSecretVal(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') void saveSecret(`svc:${c.name}`);
                            if (e.key === 'Escape') { setSecretInput(null); setSecretVal(''); }
                          }} />
                        <button className="res-btn" onClick={() => void saveSecret(`svc:${c.name}`)}>存</button>
                      </span>
                    ) : (
                      <span className={`res-secret-chip ${c.key_set ? '' : 'unset'}`}
                        title={`密钥 AES-GCM 加密保管（不注入任务环境）；${c.key_set ? '已加密保存' : '未设置'}`}
                        onClick={() => { setSecretInput(`svc:${c.name}`); setSecretVal(''); }}>
                        <><Key size={12} style={{ verticalAlign: -2 }} /> 密钥 {c.key_set ? '已加密' : '未设'}</>
                      </span>
                    )}
                    <button className="res-btn" disabled={pinging === `svc:${c.name}`}
                      onClick={() => void pingSvc(c.name)}>
                      {pinging === `svc:${c.name}` ? '测试中…' : '测试'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* ============ MCP（AC-2.2：唯一管理面=工具 tab；本页只读概览+跳转） ============ */}
      {sec === 'mcp' && (
        <>
          <div className="res-addbar focused" style={{ marginBottom: 10, cursor: 'pointer' }}
            onClick={() => { location.hash = '#/admin/tools'; }}>
            <span>🧩 MCP server 的增删改已收敛到「工具」tab（stdio/http/sse 全类型编辑器 + 会话覆盖计数）</span>
            <button className="res-btn" style={{ marginLeft: 'auto' }}>前往工具 tab 管理 →</button>
          </div>
          <table className="kv-table" style={{ width: '100%' }}>
            <thead><tr><th style={{ width: 130 }}>名称</th><th>命令</th><th style={{ width: 90 }}>会话覆盖</th></tr></thead>
            <tbody>
              {data.mcp.servers.length === 0 && (
                <tr><td colSpan={3} className="muted" style={{ textAlign: 'center', padding: 20 }}>
                  （无全局 MCP server——前往工具 tab 添加）
                </td></tr>
              )}
              {data.mcp.servers.map(m => (
                <tr key={m.name}>
                  <td><b>{m.name}</b>{m.session_only && <span className="muted" style={{ fontSize: 10.5 }}>（仅会话级）</span>}</td>
                  <td className="mono muted" style={{ fontSize: 11.5 }}>
                    {[m.spec?.command, ...(m.spec?.args ?? [])].filter(Boolean).join(' ') || '-'}
                  </td>
                  <td className="muted">{m.sessions_overriding || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* ============ 凭证库（AC-1.3：只读概览，编辑唯一面=安全 tab） ============ */}
      {sec === 'vault' && (
        <>
          <div className="res-addbar focused" style={{ marginBottom: 10, cursor: 'pointer' }} onClick={gotoVaultHome}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Lock size={14} /> 凭证库的增删改已收敛到「安全」tab（八字段编辑器，同源审计）</span>
            <button className="res-btn" style={{ marginLeft: 'auto' }}>前往安全 tab 管理 →</button>
          </div>
          <div className="res-toolbar" style={{ marginBottom: 0 }}>
            <input className="res-search" placeholder="搜索平台 / 账号…" value={vSearch}
              onChange={e => setVSearch(e.target.value)} />
            <span className="spacer" />
            <span className="muted" style={{ fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              {data.vault.verify.ok
                ? <><Shield size={13} /> 加密库健康 · {data.vault.verify.entries} 条 · AES-GCM</>
                : `⚠ 校验失败：${data.vault.verify.error ?? '未知'}`}
            </span>
          </div>
          <table className="kv-table" style={{ width: '100%' }}>
            <thead><tr><th>平台</th><th>账号</th><th style={{ width: 70 }}>密码</th><th style={{ width: 110 }}>更新</th></tr></thead>
            <tbody>
              {vFiltered.length === 0 && (
                <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 20 }}>（无匹配条目）</td></tr>
              )}
              {vFiltered.map(p => (
                <tr key={p.platform}>
                  <td>{p.platform}</td>
                  <td className="muted">{p.user || p.email || '—'}</td>
                  <td style={{ color: p.has_password ? 'var(--green)' : 'var(--red)' }}>
                    {p.has_password ? '● 已存' : '○ 缺'}
                  </td>
                  <td className="mono" style={{ fontSize: 11.5 }}>{(p.updated_at || '').slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {/* AC-5.10e（P2-3）：渠道卡（Telegram 等对话入口）移往 Webhooks tab（事件入口同域）——
          不再钉在资源页底部，MCP/凭证库分区切换不再出现无关卡 */}
    </div>
  );
}
