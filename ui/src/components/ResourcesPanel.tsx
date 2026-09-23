// 资源中心（管理中心「资源」tab）：平台原生功能。
// 三分区：服务（端点+密钥状态）/ MCP（外部工具服务）/ 凭证库（AES-GCM）。
// 安全语义：密钥只写不读——界面永远只看到「已加密保存」，值不出后端。
import { useEffect, useState } from 'react';
import { api } from '../api/client';

interface ServiceItem { key: string; label: string; note: string; value: string; kind: string }
interface SecretItem { key: string; set: boolean }
interface McpServer { name: string; spec: Record<string, any>; sessions_overriding: number; session_only?: boolean }
interface VaultPlatform { platform: string; user?: string; email?: string; has_password: boolean; updated_at: string }
interface VaultEdit { platform: string; username?: string; email?: string; phone?: string; twofa?: string; status?: string; notes?: string; password?: string; recovery?: string }
interface Overview {
  services: ServiceItem[];
  secrets: SecretItem[];
  mcp: { servers: McpServer[] };
  vault: { platforms: VaultPlatform[]; verify: { ok: boolean; entries: number; encrypted: boolean; error?: string } };
}

const SECRET_ZH: Record<string, string> = {
  sandbox_api_key: '沙箱 API Key', sms_token: '短信服务 Token',
  mail_auth_code: '主邮箱授权码', vlm_api_key: '视觉模型 Key',
  twocaptcha_key: '2Captcha Key', bocha_key: '博查 Key',
  zhipu_key: '智谱 Key', textr_password: 'Textr 密码',
};
const PING_MAP: Record<string, string> = {
  ocr_url: 'ocr', sandbox_url: 'sandbox', cdp_url: 'sandbox_mcp', proxy: 'proxy',
  sms_url: 'sms', mail_imap: 'mail', vlm_api_base: 'vlm',
  twocaptcha_key: 'twocaptcha', bocha_key: 'bocha', zhipu_key: 'zhipu',
};
const card = { border: '1px solid var(--border,#333)', borderRadius: 8, padding: '10px 12px' };
type Section = 'service' | 'mcp' | 'vault';

export default function ResourcesPanel() {
  const [data, setData] = useState<Overview | null>(null);
  const [sec, setSec] = useState<Section>('service');
  const [msg, setMsg] = useState('');
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editVal, setEditVal] = useState('');
  const [secretInput, setSecretInput] = useState<Record<string, string>>({});
  const [pinging, setPinging] = useState(false);
  const [pingRes, setPingRes] = useState<Record<string, { ok: boolean; msg: string }> | null>(null);
  const [vEdit, setVEdit] = useState<VaultEdit | null>(null);
  const [mcpEdit, setMcpEdit] = useState<{ name: string; command: string; args: string } | null>(null);

  const load = async () => {
    try { setData(await api<Overview>('/api/admin/resources')); }
    catch (e) { setMsg(`读取失败：${String(e)}`); }
  };
  useEffect(() => { void load(); }, []);

  const flash = (t: string) => { setMsg(t); setTimeout(() => setMsg(''), 2500); };

  const saveService = async (key: string, value: string) => {
    try {
      await api('/api/admin/resources/service', { method: 'POST', body: JSON.stringify({ key, value }) });
      flash('✓ 已保存'); setEditKey(null); await load();
    } catch (e) { flash(`保存失败：${String(e)}`); }
  };
  const saveSecret = async (key: string) => {
    const v = (secretInput[key] ?? '').trim();
    if (!v) return;
    try {
      await api('/api/admin/resources/secret', { method: 'POST', body: JSON.stringify({ key, value: v }) });
      flash('✓ 已加密保存（AES-GCM，不落配置文件）');
      setSecretInput(s => ({ ...s, [key]: '' })); await load();
    } catch (e) { flash(`保存失败：${String(e)}`); }
  };
  const ping = async (only: string[]) => {
    setPinging(true); setPingRes(null);
    try {
      const d = await api<{ results: Record<string, { ok: boolean; msg: string }> }>(
        '/api/admin/resources/test', { method: 'POST', body: JSON.stringify({ only }) });
      setPingRes(d.results);
    } catch (e) { flash(`探测失败：${String(e)}`); }
    finally { setPinging(false); }
  };
  const pingTargets = (keys: string[]) =>
    [...new Set(keys.map(k => PING_MAP[k]).filter(Boolean))];

  const saveVaultEntry = async () => {
    if (!vEdit?.platform?.trim()) return;
    const body: Record<string, string> = { platform: vEdit.platform.trim() };
    for (const f of ['username', 'email', 'phone', 'twofa', 'status', 'notes', 'password', 'recovery'] as const) {
      const v = (vEdit as any)[f];
      if (v && String(v).trim()) body[f] = String(v).trim();
    }
    try {
      await api('/api/admin/vault/entry', { method: 'POST', body: JSON.stringify(body) });
      flash('✓ 已入加密库'); setVEdit(null); await load();
    } catch (e) { flash(`保存失败：${String(e)}`); }
  };
  const delVaultEntry = async (platform: string) => {
    if (!confirm(`删除凭证「${platform}」？此操作不可撤销。`)) return;
    try {
      await api(`/api/admin/vault/entry/${encodeURIComponent(platform)}`, { method: 'DELETE' });
      await load();
    } catch (e) { flash(`删除失败：${String(e)}`); }
  };
  const saveMcp = async () => {
    if (!mcpEdit?.name?.trim() || !mcpEdit.command?.trim()) return;
    let args: string[] = [];
    try { args = mcpEdit.args ? JSON.parse(mcpEdit.args) : []; } catch { flash('args 需为 JSON 数组'); return; }
    try {
      await api(`/api/tools/mcp/${encodeURIComponent(mcpEdit.name.trim())}`, {
        method: 'PUT', body: JSON.stringify({ command: mcpEdit.command, args }) });
      flash('✓ 已保存（新会话生效）'); setMcpEdit(null); await load();
    } catch (e) { flash(`保存失败：${String(e)}`); }
  };

  if (!data) return <div className="pad muted">加载中…</div>;
  const secretMap = Object.fromEntries(data.secrets.map(s => [s.key, s.set]));

  return (
    <div className="pad">
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        {([['service', '服务'], ['mcp', 'MCP'], ['vault', `凭证库（${data.vault.platforms.length}）`]] as [Section, string][]).map(([k, lb]) => (
          <button key={k} className={`tab ${sec === k ? 'on' : ''}`} onClick={() => setSec(k)}>{lb}</button>
        ))}
        <span className="muted" style={{ fontSize: 12, marginLeft: 'auto' }}>
          密钥全部 AES-GCM 加密存储，明文不落配置文件、不回显界面
        </span>
      </div>
      {msg && <div style={{ ...card, marginBottom: 10, fontSize: 13 }}>{msg}</div>}

      {/* ============ 服务 ============ */}
      {sec === 'service' && (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <button disabled={pinging} onClick={() => void ping(pingTargets(data.services.map(s => s.key)))}>
              {pinging ? '探测中…' : '一键探测全部'}
            </button>
            {pingRes && <span className="muted" style={{ fontSize: 12, alignSelf: 'center' }}>
              {Object.entries(pingRes).map(([k, v]) => `${k}:${v.ok ? '✓' : '✗'}`).join(' · ')}
            </span>}
          </div>
          <table className="kv-table" style={{ width: '100%' }}>
            <thead><tr><th style={{ width: 150 }}>服务</th><th>端点 / 参数</th><th style={{ width: 130 }}>密钥</th><th style={{ width: 60 }}></th></tr></thead>
            <tbody>
              {data.services.map(sv => {
                const sk = ({ ocr_url: 'sandbox_api_key', sandbox_url: 'sandbox_api_key', cdp_url: 'sandbox_api_key', sms_url: 'sms_token', mail_imap: 'mail_auth_code', mail_smtp: 'mail_auth_code', mail_user: 'mail_auth_code', vlm_api_base: 'vlm_api_key', textr_email: 'textr_password', zhipu_engine: 'zhipu_key' } as Record<string, string>)[sv.key];
                return (
                  <tr key={sv.key}>
                    <td>
                      <b style={{ fontSize: 13 }}>{sv.label}</b>
                      {sv.note && <div className="muted" style={{ fontSize: 11 }}>{sv.note}</div>}
                    </td>
                    <td>
                      {editKey === sv.key ? (
                        <span style={{ display: 'flex', gap: 6 }}>
                          <input style={{ flex: 1 }} value={editVal} autoFocus
                            onChange={e => setEditVal(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') void saveService(sv.key, editVal); if (e.key === 'Escape') setEditKey(null); }} />
                          <button onClick={() => void saveService(sv.key, editVal)}>存</button>
                          <button onClick={() => setEditKey(null)}>取消</button>
                        </span>
                      ) : (
                        <a style={{ cursor: 'pointer' }} title="点击编辑"
                          onClick={() => { setEditKey(sv.key); setEditVal(sv.value); }}>
                          {sv.value || <span className="muted">（未配置）</span>}
                        </a>
                      )}
                    </td>
                    <td>
                      {sk && (
                        secretInput[sk] !== undefined ? (
                          <span style={{ display: 'flex', gap: 4 }}>
                            <input type="password" style={{ width: 90 }} autoFocus
                              placeholder="新值" value={secretInput[sk]}
                              onChange={e => setSecretInput(s => ({ ...s, [sk]: e.target.value }))}
                              onKeyDown={e => { if (e.key === 'Enter') void saveSecret(sk); if (e.key === 'Escape') setSecretInput(s => { const n = { ...s }; delete n[sk]; return n; }); }} />
                            <button onClick={() => void saveSecret(sk)}>存</button>
                          </span>
                        ) : (
                          <a style={{ cursor: 'pointer', color: secretMap[sk] ? '#3aa675' : 'var(--accent,#e5484d)', fontSize: 12 }}
                            title="点击设置（只写不读）"
                            onClick={() => setSecretInput(s => ({ ...s, [sk]: '' }))}>
                            {secretMap[sk] ? '🔑 已加密保存' : '＋ 设置密钥'}
                          </a>
                        )
                      )}
                    </td>
                    <td>
                      {PING_MAP[sv.key] && <button disabled={pinging}
                        onClick={() => void ping(pingTargets([sv.key]))}>测</button>}
                    </td>
                  </tr>
                );
              })}
              {/* 纯密钥行（无端点）：2captcha/bocha/zhipu */}
              {(['twocaptcha_key', 'bocha_key', 'zhipu_key'] as const).map(k => (
                <tr key={k}>
                  <td><b style={{ fontSize: 13 }}>{SECRET_ZH[k]}</b></td>
                  <td className="muted">仅密钥（无端点）</td>
                  <td>
                    {secretInput[k] !== undefined ? (
                      <span style={{ display: 'flex', gap: 4 }}>
                        <input type="password" style={{ width: 90 }} autoFocus placeholder="新值"
                          value={secretInput[k] ?? ''} onChange={e => setSecretInput(s => ({ ...s, [k]: e.target.value }))}
                          onKeyDown={e => { if (e.key === 'Enter') void saveSecret(k); if (e.key === 'Escape') setSecretInput(s => { const n = { ...s }; delete n[k]; return n; }); }} />
                        <button onClick={() => void saveSecret(k)}>存</button>
                      </span>
                    ) : (
                      <a style={{ cursor: 'pointer', color: secretMap[k] ? '#3aa675' : 'var(--accent,#e5484d)', fontSize: 12 }}
                        onClick={() => setSecretInput(s => ({ ...s, [k]: '' }))}>
                        {secretMap[k] ? '🔑 已加密保存' : '＋ 设置密钥'}
                      </a>
                    )}
                  </td>
                  <td><button disabled={pinging} onClick={() => void ping([PING_MAP[k]])}>测</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* ============ MCP ============ */}
      {sec === 'mcp' && (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <button onClick={() => setMcpEdit({ name: '', command: '', args: '' })}>＋ 添加 MCP Server</button>
            <span className="muted" style={{ fontSize: 12, alignSelf: 'center' }}>
              stdio 服务，工具以 mcp__&lt;server&gt;__&lt;tool&gt; 出现；改动新会话生效
            </span>
          </div>
          {mcpEdit && (
            <div style={{ ...card, marginBottom: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <input placeholder="名称（如 browser）" style={{ width: 140 }} value={mcpEdit.name}
                onChange={e => setMcpEdit({ ...mcpEdit, name: e.target.value })} />
              <input placeholder="命令（如 npx）" style={{ width: 140 }} value={mcpEdit.command}
                onChange={e => setMcpEdit({ ...mcpEdit, command: e.target.value })} />
              <input placeholder='参数 JSON 数组（如 ["-y","mcp-server"]）' style={{ flex: 1, minWidth: 220 }} value={mcpEdit.args}
                onChange={e => setMcpEdit({ ...mcpEdit, args: e.target.value })} />
              <button onClick={() => void saveMcp()}>保存</button>
              <button onClick={() => setMcpEdit(null)}>取消</button>
            </div>
          )}
          <table className="kv-table" style={{ width: '100%' }}>
            <thead><tr><th style={{ width: 140 }}>名称</th><th>命令</th><th style={{ width: 110 }}>会话覆盖</th><th style={{ width: 60 }}></th></tr></thead>
            <tbody>
              {data.mcp.servers.length === 0 && (
                <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 16 }}>（无全局 MCP server）</td></tr>
              )}
              {data.mcp.servers.map(m => (
                <tr key={m.name}>
                  <td><b>{m.name}</b>{m.session_only && <span className="muted" style={{ fontSize: 11 }}>（仅会话级）</span>}</td>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {[m.spec?.command, ...(m.spec?.args ?? [])].filter(Boolean).join(' ') || '-'}
                  </td>
                  <td className="muted">{m.sessions_overriding || '-'}</td>
                  <td>{!m.session_only && (
                    <button onClick={() => { if (confirm(`删除 MCP server ${m.name}？`)) void api(`/api/tools/mcp/${encodeURIComponent(m.name)}`, { method: 'DELETE' }).then(load).catch(e => flash(String(e))); }}>删</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* ============ 凭证库 ============ */}
      {sec === 'vault' && (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
            <button onClick={() => setVEdit({ platform: '' })}>＋ 添加凭证</button>
            <span className="muted" style={{ fontSize: 12 }}>
              {data.vault.verify.ok
                ? `加密库健康（${data.vault.verify.entries} 条 · AES-GCM）`
                : `⚠ 校验失败：${data.vault.verify.error ?? '未知'}`}
            </span>
          </div>
          {vEdit && (
            <div style={{ ...card, marginBottom: 10 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <input placeholder="平台名（如 GitHub）" style={{ width: 150 }} value={vEdit.platform ?? ''}
                  onChange={e => setVEdit({ ...vEdit, platform: e.target.value })} />
                <input placeholder="用户名" style={{ width: 130 }} value={vEdit.username ?? ''}
                  onChange={e => setVEdit({ ...vEdit, username: e.target.value })} />
                <input placeholder="邮箱" style={{ width: 160 }} value={vEdit.email ?? ''}
                  onChange={e => setVEdit({ ...vEdit, email: e.target.value })} />
                <input type="password" placeholder="密码（只写）" style={{ width: 130 }} value={vEdit.password ?? ''}
                  onChange={e => setVEdit({ ...vEdit, password: e.target.value })} />
                <input placeholder="备注" style={{ flex: 1, minWidth: 140 }} value={vEdit.notes ?? ''}
                  onChange={e => setVEdit({ ...vEdit, notes: e.target.value })} />
              </div>
              <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                <button onClick={() => void saveVaultEntry()}>保存（加密）</button>
                <button onClick={() => setVEdit(null)}>取消</button>
                <span className="muted" style={{ fontSize: 11, alignSelf: 'center' }}>
                  取用走审批门（loadn-web r account）；明文永不经过界面/网络
                </span>
              </div>
            </div>
          )}
          <table className="kv-table" style={{ width: '100%' }}>
            <thead><tr><th>平台</th><th>账号</th><th style={{ width: 70 }}>密码</th><th style={{ width: 110 }}>更新</th><th style={{ width: 50 }}></th></tr></thead>
            <tbody>
              {data.vault.platforms.map(p => (
                <tr key={p.platform}>
                  <td>
                    <a style={{ cursor: 'pointer' }} title="点击编辑（密码留空=不改）"
                      onClick={() => setVEdit({ platform: p.platform, username: p.user, email: p.email })}>
                      {p.platform}
                    </a>
                  </td>
                  <td className="muted">{p.user || p.email || '-'}</td>
                  <td style={{ color: p.has_password ? '#3aa675' : 'var(--accent,#e5484d)' }}>
                    {p.has_password ? '已存' : '缺'}
                  </td>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(p.updated_at || '').slice(0, 10)}</td>
                  <td><button onClick={() => void delVaultEntry(p.platform)}>删</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
