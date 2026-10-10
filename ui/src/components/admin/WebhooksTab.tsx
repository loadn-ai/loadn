// Webhooks 管理页（P3：事件触发入口——列表/新建/编辑/启停/删除，token 展示即触发 URL）
// AC-5.4：补编辑（复用同一表单组件，PATCH 全字段——后端早已支持，前端此前
// 只能删了重建，token 随之吊销、外部调用方全部要换 URL）。
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { toast } from '../../stores/toasts';
import { Plus } from '../icons';
import ChannelsCard from './ChannelsCard';   // AC-5.10e（P2-3）：对话入口与事件入口同域，从资源页归位至此
import { askConfirm } from '../../stores/confirm';

interface Hook {
  id: number; token: string; name: string; profile: string | null;
  prompt_template: string; enabled: number; allowed_ips_json: string | null;
  rate_limit_per_min: number; last_fired_at: string | null; created_at: string;
}

export default function WebhooksTab() {
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Hook | null>(null);
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);   // AC-5.5b：行操作 busy（防双击重复提交）

  async function reload() {
    try {
      const d = await api<{ hooks: Hook[] }>('/api/hooks');
      setHooks(d.hooks);
    } catch (e) { setMsg({ t: `加载失败：${String(e)}`, err: true }); }
  }
  useEffect(() => {
    void reload();
    void (async () => {
      try {
        const d = await api<{ profiles: { name: string }[] }>('/api/profiles');
        setProfiles(d.profiles.map(p => p.name));
      } catch { /* 角色清单加载失败不阻断主列表 */ }
    })();
  }, []);

  function triggerUrl(h: Hook) {
    return `${location.origin}/hooks/${h.token}`;
  }

  async function toggle(h: Hook) {
    setBusyId(h.id);
    try {
      await api(`/api/hooks/${h.id}`, { method: 'PATCH',
        body: JSON.stringify({ enabled: !h.enabled }) });
    } catch (e) { toast(`操作失败：${String(e)}`, false); }
    finally { setBusyId(null); }
    void reload();
  }

  async function del(h: Hook) {
    if (!await askConfirm({ title: `删除 webhook「${h.name}」？token 立即吊销，外部调用将 401。`, danger: true })) return;
    setBusyId(h.id);
    try {
      await api(`/api/hooks/${h.id}`, { method: 'DELETE' });
      toast(`已删除 ${h.name}（token 已吊销）`);
    } catch (e) { toast(`删除失败：${String(e)}`, false); }
    finally { setBusyId(null); }
    void reload();
  }

  function copy(h: Hook) {
    void navigator.clipboard.writeText(triggerUrl(h)).then(() => {
      setCopied(h.id);
      setTimeout(() => setCopied(null), 1500);
    });
  }

  return (
    <div className="admin-body">
      <div className="admin-toolbar">
        <button className="btn sm" onClick={() => setCreating(true)}><Plus size={13} /> 新建</button>
        {msg && <span className={msg.err ? 'admin-msg err' : 'admin-msg'}>{msg.t}</span>}
      </div>
      {creating && <HookForm profiles={profiles} onDone={() => {
        setCreating(false); void reload();
      }} />}
      {editing && <HookForm profiles={profiles} hook={editing} onDone={() => {
        setEditing(null); void reload();
      }} />}
      <div className="admin-list">
        {hooks.map(h => (
          <div key={h.id} className={`hub-card${h.enabled ? '' : ' off'}`}>
            <div className="sk-head">
              <b>{h.name}</b>
              <span className={`sk-src ${h.enabled ? 'local' : 'off-tag'}`}>
                {h.enabled ? '启用' : '已禁用'}
              </span>
              <span className="sk-src ext">{h.profile || 'auto'}</span>
              <span className="sk-src">{h.rate_limit_per_min}/min</span>
            </div>
            <div className="mono sk-desc" title={h.prompt_template}>
              {h.prompt_template.split('\n')[0].slice(0, 120)}
            </div>
            <div className="sk-foot">
              <span className="sk-time">
                {h.last_fired_at ? `最近触发 ${h.last_fired_at.slice(5, 16).replace('T', ' ')}` : '未触发过'}
              </span>
              <span className="sk-actions">
                <button className="link" onClick={() => copy(h)}>
                  {copied === h.id ? '已复制' : '复制 URL'}
                </button>
                <button className="link" onClick={() => setEditing(h)}>编辑</button>
                <button className="link" disabled={busyId === h.id}
                  onClick={() => void toggle(h)}>{h.enabled ? '禁用' : '启用'}</button>
                <button className="link danger-link" disabled={busyId === h.id}
                  onClick={() => void del(h)}>删除</button>
              </span>
            </div>
            <div className="mono sk-desc" style={{ fontSize: 'var(--fs-sm)', opacity: 0.75 }}>
              POST {triggerUrl(h)}
            </div>
          </div>
        ))}
        {hooks.length === 0 && !creating && (
          <div className="panel-empty">还没有 webhook——新建一个，把外部事件（PR/支付/表单）投给 agent。</div>
        )}
      </div>
      <ChannelsCard />
    </div>
  );
}


/** 新建 / 编辑共用表单。编辑态（hook 给定）：全字段回填、提交走 PATCH——
 * token 恒不变（换 token = 删了重建）；取消时未保存改动 confirm。 */
function HookForm({ profiles, hook, onDone }: {
  profiles: string[]; hook?: Hook; onDone: () => void }) {
  const editing = !!hook;
  const ipsInit = (() => {
    try { return hook?.allowed_ips_json ? (JSON.parse(hook.allowed_ips_json) as string[]).join(',') : ''; }
    catch { return ''; }
  })();
  const [name, setName] = useState(hook?.name || '');
  const [tpl, setTpl] = useState(hook?.prompt_template || '处理这个事件：{{payload}}');
  const [profile, setProfile] = useState(hook?.profile || 'auto');
  const [ips, setIps] = useState(ipsInit);
  const [rate, setRate] = useState(hook?.rate_limit_per_min ?? 6);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const initial = { name, tpl, profile, ips, rate };
  const cancel = async () => {
    const changed = name !== initial.name || tpl !== initial.tpl
      || profile !== initial.profile || ips !== initial.ips || rate !== initial.rate;
    if (editing && changed && !await askConfirm({ title: '未保存的修改将丢弃，确认取消？', danger: true })) return;
    onDone();
  };
  return (
    <div className="new-skill-form">
      <div className="sk-head"><b>{editing ? `编辑 #${hook!.id} · ${hook!.name}` : '新建 webhook'}</b>
        {editing && <span className="muted" style={{ fontSize: 'var(--fs-md)' }}>（token 不变，外部 URL 无需更换）</span>}
      </div>
      <input placeholder="名称（例：PR 合并处理）" value={name} onChange={e => setName(e.target.value)} />
      <textarea className="mono" rows={4} value={tpl} onChange={e => setTpl(e.target.value)}
        placeholder="prompt 模板，须含 {{payload}} 占位符（payload 原样嵌入，不求值）" />
      <div style={{ display: 'flex', gap: 8 }}>
        <select value={profile} onChange={e => setProfile(e.target.value)}>
          <option value="auto">auto（按内容匹配）</option>
          {profiles.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <input type="number" min={1} max={600} value={rate}
          onChange={e => setRate(Number(e.target.value))} title="限流（次/分钟）" />
        <input placeholder="IP 白名单（可选，逗号分隔）" value={ips} onChange={e => setIps(e.target.value)} />
      </div>
      <div className="modal-foot">
        <button className="btn ghost sm" onClick={cancel}>取消</button>
        <button className="btn primary sm" disabled={!name.trim() || !tpl.includes('{{payload}}') || busy}
          onClick={() => void (async () => {
            setBusy(true);
            try {
              const body = { name: name.trim(), prompt_template: tpl,
                profile, rate_limit_per_min: rate,
                allowed_ips: ips.trim() ? ips : null };
              if (editing) {
                await api(`/api/hooks/${hook!.id}`, { method: 'PATCH', body: JSON.stringify(body) });
              } else {
                await api('/api/hooks', { method: 'POST', body: JSON.stringify(body) });
              }
              onDone();
            } catch (e) { setErr(String(e)); }
            finally { setBusy(false); }
          })()}>{busy ? '保存中…' : editing ? '保存' : '创建'}</button>
      </div>
      {err && <div className="admin-err">{err}</div>}
    </div>
  );
}
