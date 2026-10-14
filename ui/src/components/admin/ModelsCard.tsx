// 模型库卡（T10）：多模型 CRUD + 默认标记 + 一键应用（写引擎 provider 配置）。
// 密钥只写不读：列表显示 ***尾4；编辑时空串=保持不变。
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { toast } from '../../stores/toasts';
import { askConfirm } from '../../stores/confirm';
import { Plus } from '../icons';

interface ModelRow {
  id: number; name: string; engine: string; provider: string;
  base_url: string; model: string;
  api_key_masked: string; is_default: number; enabled: number; notes: string;
}

type Draft = {
  name: string; provider: string; base_url: string; api_key: string;
  model: string; notes: string; is_default: boolean;
};

const EMPTY: Draft = { name: '', provider: 'openai', base_url: '',
  api_key: '', model: '', notes: '', is_default: false };

export default function ModelsCard() {
  const [rows, setRows] = useState<ModelRow[]>([]);
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState('');

  async function reload() {
    try {
      const d = await api<{ models: ModelRow[] }>('/api/models');
      setRows(d.models);
    } catch (e) { toast(`模型库加载失败：${String(e)}`, false); }
  }
  useEffect(() => { void reload(); }, []);

  function startNew() {
    setDraft({ ...EMPTY, is_default: rows.length === 0 });
    setEditing('new');
  }
  function startEdit(r: ModelRow) {
    setDraft({ name: r.name, provider: r.provider, base_url: r.base_url,
      api_key: '', model: r.model, notes: r.notes, is_default: !!r.is_default });
    setEditing(r.id);
  }

  async function save() {
    const isNew = editing === 'new';
    try {
      await api(isNew ? '/api/models' : `/api/models/${editing}`, {
        method: isNew ? 'POST' : 'PATCH',
        body: JSON.stringify(draft) });
      toast(`已保存「${draft.name}」`);
      setEditing(null);
      await reload();
    } catch (e) { toast(String(e), false); }
  }

  async function setDefault(r: ModelRow) {
    try {
      await api(`/api/models/${r.id}`, { method: 'PATCH',
        body: JSON.stringify({ is_default: true }) });
      await reload();
    } catch (e) { toast(String(e), false); }
  }

  async function apply(r: ModelRow) {
    setBusy(`apply-${r.id}`);
    try {
      const d = await api<{ applied: string; model: string }>(
        `/api/models/${r.id}/apply`, { method: 'POST' });
      toast(`已应用「${d.applied}」为引擎默认模型（${d.model}）——新会话即用`);
    } catch (e) { toast(String(e), false); }
    finally { setBusy(''); }
  }

  async function toggle(r: ModelRow) {
    try {
      await api(`/api/models/${r.id}`, { method: 'PATCH',
        body: JSON.stringify({ enabled: !r.enabled }) });
      await reload();
    } catch (e) { toast(String(e), false); }
  }

  async function del(r: ModelRow) {
    if (!await askConfirm({ title: `删除模型「${r.name}」？（不影响已应用的引擎配置）`, danger: true })) return;
    try {
      await api(`/api/models/${r.id}`, { method: 'DELETE' });
      toast(`已删除 ${r.name}`);
      await reload();
    } catch (e) { toast(String(e), false); }
  }

  return (
    <div className="setting-card">
      <h4>模型库 <span className="muted">（多模型登记：端点/密钥/模型 ID；设默认联动新会话，应用即刻切换引擎配置）</span></h4>
      {editing !== null ? (
        <div className="new-skill-form">
          <label>名称</label>
          <input value={draft.name} maxLength={60}
            placeholder="如：deepseek-v3 / glm-4.7"
            onChange={e => setDraft({ ...draft, name: e.target.value })} autoComplete="off"/>
          <label>Provider 类型</label>
          <div className="skill-row">
            {['openai', 'anthropic', 'fake'].map(p => (
              <button key={p} className={`chip ${draft.provider === p ? 'on' : ''}`}
                onClick={() => setDraft({ ...draft, provider: p })}>{p}</button>
            ))}
          </div>
          <label>Base URL</label>
          <input value={draft.base_url} placeholder="https://api.deepseek.com（openai 兼容端点）"
            onChange={e => setDraft({ ...draft, base_url: e.target.value })} autoComplete="off"/>
          <label>API Key {editing !== 'new' && <span className="muted">（已存 {rows.find(r => r.id === editing)?.api_key_masked || '—'}；留空=不改）</span>}</label>
          <input value={draft.api_key} type="password" placeholder="sk-…"
            onChange={e => setDraft({ ...draft, api_key: e.target.value })} autoComplete="new-password"/>
          <label>模型 ID</label>
          <input value={draft.model} placeholder="deepseek-chat / glm-4.7"
            onChange={e => setDraft({ ...draft, model: e.target.value })} autoComplete="off"/>
          <label>备注</label>
          <input value={draft.notes} maxLength={200}
            onChange={e => setDraft({ ...draft, notes: e.target.value })} autoComplete="off"/>
          <div className="skill-row">
            <button className={`chip ${draft.is_default ? 'on' : ''}`}
              onClick={() => setDraft({ ...draft, is_default: !draft.is_default })}>
              {draft.is_default ? '★ 默认模型' : '设为默认'}
            </button>
          </div>
          <div className="modal-foot">
            <button className="btn ghost sm" onClick={() => setEditing(null)}>取消</button>
            <button className="btn primary sm"
              disabled={!draft.name.trim() || !draft.model.trim()}
              onClick={() => void save()}>保存</button>
          </div>
        </div>
      ) : (
        <>
          <div className="setting-row">
            <button className="btn sm primary" onClick={startNew}>
              <Plus size={13} /> 添加模型
            </button>
            <span className="muted">新会话默认用「默认模型」（未显式选引擎时）；「应用」把配置即刻写进引擎。</span>
          </div>
          <div className="tbl-wrap">
            <table className="mcp-table">
              <thead><tr>
                <th>名称</th><th>Provider</th><th>模型 ID</th><th>端点</th><th>密钥</th>
                <th>默认</th><th>状态</th><th>操作</th>
              </tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.id}>
                    <td>{r.name}</td>
                    <td><span className="sk-src local">{r.provider}</span></td>
                    <td className="mono-cell">{r.model}</td>
                    <td className="mono-cell" title={r.base_url}>
                      {(r.base_url || '—').slice(0, 38)}</td>
                    <td className="mono-cell">{r.api_key_masked || '—'}</td>
                    <td>
                      <button className={`chip ${r.is_default ? 'on' : ''}`} title="默认模型：新会话联动"
                        onClick={() => void setDefault(r)}>
                        {r.is_default ? '★ 默认' : '设默认'}
                      </button>
                    </td>
                    <td>
                      <button className={`chip ${r.enabled ? 'on' : ''}`}
                        onClick={() => void toggle(r)}>{r.enabled ? '启用' : '停用'}</button>
                    </td>
                    <td>
                      <button className="link" title="把该模型写进引擎 provider 配置（即刻生效，新会话即用）"
                        disabled={busy === `apply-${r.id}` || !r.enabled}
                        onClick={() => void apply(r)}>应用</button>{' '}
                      <button className="link" onClick={() => startEdit(r)}>编辑</button>{' '}
                      <button className="link danger-link" onClick={() => void del(r)}>删除</button>
                    </td>
                  </tr>
                ))}
                {!rows.length && (
                  <tr><td colSpan={8} className="muted">
                    还没有登记模型——添加后可一键切换主模型，不必再手改配置文件。
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
