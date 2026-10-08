// Webhooks 管理页（P3：事件触发入口——列表/新建/启停/删除，token 展示即触发 URL）
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { Plus } from '../icons';

interface Hook {
  id: number; token: string; name: string; profile: string | null;
  prompt_template: string; enabled: number; allowed_ips_json: string | null;
  rate_limit_per_min: number; last_fired_at: string | null; created_at: string;
}

export default function WebhooksTab() {
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState('');
  const [copied, setCopied] = useState<number | null>(null);

  async function reload() {
    const d = await api<{ hooks: Hook[] }>('/api/hooks');
    setHooks(d.hooks);
  }
  useEffect(() => {
    void reload();
    void (async () => {
      const d = await api<{ profiles: { name: string }[] }>('/api/profiles');
      setProfiles(d.profiles.map(p => p.name));
    })();
  }, []);

  function triggerUrl(h: Hook) {
    return `${location.origin}/hooks/${h.token}`;
  }

  async function toggle(h: Hook) {
    try {
      await api(`/api/hooks/${h.id}`, { method: 'PATCH',
        body: JSON.stringify({ enabled: !h.enabled }) });
    } catch (e) { setMsg(`操作失败：${String(e)}`); }
    void reload();
  }

  async function del(h: Hook) {
    if (!confirm(`删除 webhook「${h.name}」？token 立即吊销，外部调用将 401。`)) return;
    try {
      await api(`/api/hooks/${h.id}`, { method: 'DELETE' });
      setMsg(`已删除 ${h.name}（token 已吊销）`);
    } catch (e) { setMsg(`删除失败：${String(e)}`); }
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
        {msg && <span className="admin-msg">{msg}</span>}
      </div>
      {creating && <NewHookForm profiles={profiles} onDone={() => {
        setCreating(false); void reload();
      }} />}
      <div className="hub-results">
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
                <button className="link" onClick={() => void toggle(h)}>{h.enabled ? '禁用' : '启用'}</button>
                <button className="link danger-link" onClick={() => void del(h)}>删除</button>
              </span>
            </div>
            <div className="mono sk-desc" style={{ fontSize: 11, opacity: 0.75 }}>
              POST {triggerUrl(h)}
            </div>
          </div>
        ))}
        {hooks.length === 0 && !creating && (
          <div className="panel-empty">还没有 webhook——新建一个，把外部事件（PR/支付/表单）投给 agent。</div>
        )}
      </div>
    </div>
  );
}


function NewHookForm({ profiles, onDone }: { profiles: string[]; onDone: () => void }) {
  const [name, setName] = useState('');
  const [tpl, setTpl] = useState('处理这个事件：{{payload}}');
  const [profile, setProfile] = useState('auto');
  const [ips, setIps] = useState('');
  const [rate, setRate] = useState(6);
  const [err, setErr] = useState('');
  return (
    <div className="new-skill-form">
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
        <button className="btn ghost sm" onClick={onDone}>取消</button>
        <button className="btn primary sm" disabled={!name.trim() || !tpl.includes('{{payload}}')}
          onClick={() => void (async () => {
            try {
              await api('/api/hooks', { method: 'POST', body: JSON.stringify({
                name: name.trim(), prompt_template: tpl,
                profile, rate_limit_per_min: rate,
                allowed_ips: ips.trim() ? ips : null }) });
              onDone();
            } catch (e) { setErr(String(e)); }
          })()}>创建</button>
      </div>
      {err && <div className="admin-err">{err}</div>}
    </div>
  );
}
