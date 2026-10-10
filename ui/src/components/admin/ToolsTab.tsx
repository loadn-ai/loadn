// 工具页（全局 MCP servers + 内建开关）——从 AdminPanel 拆出（v0.6.12）
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { toast } from '../../stores/toasts';
import { Plus } from '../icons';
import type { McpServer, ProfileTools } from './shared';
import { askConfirm } from '../../stores/confirm';

export default function ToolsTab() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [profiles, setProfiles] = useState<ProfileTools[]>([]);
  const [builtin, setBuiltin] = useState<string[]>([]);
  const [editing, setEditing] = useState<McpServer | 'new' | null>(null);
  const [msg, setMsg] = useState<{ t: string; err?: boolean } | null>(null);

  async function reload() {
    try {
      const d = await api<{ servers: McpServer[]; profiles: ProfileTools[]; builtin_tools: string[] }>('/api/tools');
      setServers(d.servers); setProfiles(d.profiles); setBuiltin(d.builtin_tools);
    } catch (e) { setMsg({ t: `加载失败：${String(e)}`, err: true }); }
  }
  useEffect(() => { void reload(); }, []);

  async function del(s: McpServer) {
    if (!await askConfirm({ title: `删除全局 MCP server「${s.name}」？（会话级覆盖不受影响）`, danger: true })) return;
    try {
      await api(`/api/tools/mcp/${encodeURIComponent(s.name)}`, { method: 'DELETE' });
      toast(`已删除 ${s.name}`);
    } catch (e) { toast(String(e), false); }
    void reload();
  }

  async function toggleTool(p: ProfileTools, tool: string) {
    const cur = p.disallowed_tools;
    const next = cur.includes(tool) ? cur.filter(t => t !== tool) : [...cur, tool];
    try {
      await api(`/api/tools/profile/${encodeURIComponent(p.name)}`, {
        method: 'PUT', body: JSON.stringify({ disallowed_tools: next }),
      });
    } catch (e) { toast(String(e), false); }
    void reload();
  }

  return (
    <div className="admin-body">
      {editing !== null ? (
        <McpForm init={editing === 'new' ? null : editing} onDone={() => { setEditing(null); void reload(); }} />
      ) : (
        <>
          <div className="admin-toolbar">
            <button className="btn sm primary" onClick={() => setEditing('new')}><Plus size={13} /> 添加 MCP server</button>
            {msg && <span className={msg.err ? 'admin-msg err' : 'admin-msg'}>{msg.t}</span>}
          </div>
          <div className="tbl-wrap">
          <table className="mcp-table">
            <thead><tr><th>名称</th><th>类型</th><th>命令 / URL</th><th>会话覆盖</th><th /></tr></thead>
            <tbody>
              {servers.map(s => (
                <tr key={s.name}>
                  <td>{s.name}{s.session_only && <span className="sk-src local">仅会话级</span>}</td>
                  <td><span className="sk-src local">{s.spec.type ?? 'stdio'}</span></td>
                  <td className="mono-cell">{s.spec.command
                    ? [s.spec.command, ...(s.spec.args ?? [])].join(' ')
                    : s.spec.url ?? '—'}</td>
                  <td>{s.sessions_overriding || '—'}</td>
                  <td>
                    <button className="link" onClick={() => setEditing(s)}>编辑</button>{' '}
                    <button className="link danger-link" onClick={() => void del(s)}>删除</button>
                  </td>
                </tr>
              ))}
              {servers.length === 0 && <tr><td colSpan={5} className="muted">未配置全局 MCP server</td></tr>}
            </tbody>
          </table>
          </div>
          <h4 className="admin-h4">内建工具开关 <span className="muted">（按角色禁用，写 registry.yaml，新会话生效）</span></h4>
          {/* AC-5.10b D10：转置为「工具为行、角色为列」——工具 20+ 时不再横向膨胀，角色列固定可对照 */}
          <div className="tbl-wrap">
          <table className="mcp-table">
            <thead><tr><th>内建工具 ＼ 角色</th>{profiles.map(p => <th key={p.name}>{p.name}</th>)}</tr></thead>
            <tbody>
              {builtin.map(t => (
                <tr key={t}>
                  <td className="mono-cell">{t}</td>
                  {profiles.map(p => {
                    const off = p.disallowed_tools.includes(t);
                    return (
                      <td key={p.name}>
                        <button className={`chip ${off ? '' : 'on'}`}
                          onClick={() => void toggleTool(p, t)}>{off ? '已禁用' : '启用'}</button>
                      </td>
                    );
                  })}
                </tr>
              ))}
              {builtin.length === 0 && <tr><td colSpan={Math.max(profiles.length + 1, 2)} className="muted">无内建工具</td></tr>}
            </tbody>
          </table>
          </div>
        </>
      )}
    </div>
  );
}

function McpForm({ init, onDone }: { init: McpServer | null; onDone: () => void }) {
  const spec = init?.spec ?? {};
  const [name, setName] = useState(init?.name ?? '');
  const [type, setType] = useState<string>(spec.type ?? 'stdio');
  const [command, setCommand] = useState(spec.command ?? '');
  const [args, setArgs] = useState((spec.args ?? []).join('\n'));
  const [url, setUrl] = useState(spec.url ?? '');
  const [envJson, setEnvJson] = useState(JSON.stringify(spec.env ?? {}, null, 2));
  const [err, setErr] = useState('');

  async function save() {
    let s: Record<string, any> = { type };
    try {
      if (type === 'stdio') {
        s = { type, command, args: args.split('\n').map((x: string) => x.trim()).filter(Boolean) };
        const env = JSON.parse(envJson || '{}');
        if (Object.keys(env).length) s.env = env;
      } else {
        s = { type, url };
        const headers = JSON.parse(envJson || '{}');
        if (Object.keys(headers).length) s.headers = headers;
      }
      await api(`/api/tools/mcp/${encodeURIComponent(name.trim())}`, {
        method: 'PUT', body: JSON.stringify({ spec: s }),
      });
      onDone();
    } catch (e) { setErr(String(e)); }
  }

  return (
    <div className="new-skill-form">
      <label>名称</label>
      <input value={name} onChange={e => setName(e.target.value)} placeholder="例：context7" disabled={!!init && !init.session_only} />
      <label>类型</label>
      <div className="skill-row">
        {['stdio', 'http', 'sse'].map(t => (
          <button key={t} className={`chip ${type === t ? 'on' : ''}`} onClick={() => setType(t)}>{t}</button>
        ))}
      </div>
      {type === 'stdio' ? <>
        <label>command</label>
        <input value={command} onChange={e => setCommand(e.target.value)} placeholder="npx / uvx / 绝对路径" />
        <label>args（每行一个）</label>
        <textarea value={args} onChange={e => setArgs(e.target.value)} rows={3} placeholder={'-y\n@mcp/context7-mcp'} />
        <label>env（JSON）</label>
        <textarea className="mono" value={envJson} onChange={e => setEnvJson(e.target.value)} rows={4}>{}</textarea>
      </> : <>
        <label>url</label>
        <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://mcp.example.com/sse" />
        <label>headers（JSON）</label>
        <textarea className="mono" value={envJson} onChange={e => setEnvJson(e.target.value)} rows={4}>{}</textarea>
      </>}
      <div className="modal-foot">
        <button className="btn ghost sm" onClick={onDone}>取消</button>
        <button className="btn primary sm" disabled={!name.trim()} onClick={() => void save()}>保存（全局生效 + 重写会话 .mcp.json）</button>
      </div>
      {err && <div className="admin-err">{err}</div>}
    </div>
  );
}
