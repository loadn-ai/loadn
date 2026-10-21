import { useCallback, useEffect, useRef, useState } from 'react';
import { useStore } from '../stores/sessions';
import type { ArtifactInfo, TurnInfo } from '../stores/sessions';
import { api, withToken } from '../api/client';
import PropertiesPanel from './PropertiesPanel';
import { Folder, FileDoc, ChevronDown, ChevronRight, Download } from './icons';
import { toast } from '../stores/toasts';
import { askConfirm } from '../stores/confirm';

type Tab = 'properties' | 'artifacts' | 'files';

/** 交付物/产出物联动筛选条件（store.artifactScope） */
type ArtScope = number | 'global' | null;

/** 联动筛选共用过滤：交付物（产物清单）与产出物（工作区树）按同一条件
 *  派生。null=全部；'global'=无 agent 归属（主控问答/用户导出）；number=
 *  子任务 id——归属经 turn→subtask join（与产物 tab 分组同源，口径一致） */
function scopeArtifacts(
  artifacts: ArtifactInfo[], turns: TurnInfo[], scope: ArtScope,
): ArtifactInfo[] {
  if (scope == null) return artifacts;
  if (scope === 'global') return artifacts.filter(a => !a.agent_id);
  const subOf = new Map(turns.map(t => [t.id, t.subtask_id ?? null]));
  return artifacts.filter(a => a.turn_id != null && subOf.get(a.turn_id) === scope);
}

export default function RightPanel({ onOpenFile }: { onOpenFile: (path: string) => void }) {
  // tab 状态在 store：侧边栏「属性」入口要能从外部切过来
  const tab = useStore(s => s.rightTab);
  const setTabRaw = useStore(s => s.setRightTab);
  const loadArtifacts = useStore(s => s.loadArtifacts);
  // 切到产物 tab：拉新（中文标题/摘要是收尾后数秒落地的后台回填）；
  // 若仍有缺摘要行，8s 后补拉一次（兜底触发刚好在这次 GET 里点火）
  const setTab = (t: Tab) => {
    setTabRaw(t);
    if (t === 'artifacts') {
      void loadArtifacts().then(() => {
        if (useStore.getState().artifacts.some(a => !a.summary))
          setTimeout(() => void useStore.getState().loadArtifacts(), 8000);
      });
    }
  };
  // 字段级订阅：live turn 每个 delta 都 set 全店，无 selector 的整店订阅会让
  // 550 张产物卡每 token 重渲染一遍，面板直接卡死（"产物打不开"的根因）
  const artifacts = useStore(s => s.artifacts);
  const currentSid = useStore(s => s.currentSid);
  const turns = useStore(s => s.turns);
  const scope = useStore(s => s.artifactScope);
  const [tree, setTree] = useState<{ path: string; dir: boolean; size: number | null }[]>([]);

  // 文件管理写操作（编辑/新建/删除/重命名/上传）后的即时刷新通道：
  // 服务端写面已失效树缓存，这里主动重取（不等 8s 轮询）。
  const refreshTree = useCallback(async () => {
    if (!currentSid) return;
    try {
      const d = await api<{ tree: typeof tree }>(`/api/sessions/${encodeURIComponent(currentSid)}/tree`);
      setTree(d.tree);
    } catch { /* 会话切换竞态 */ }
  }, [currentSid]);

  useEffect(() => {
    if (!currentSid) return;
    let stop = false;
    const load = async () => {
      try {
        const d = await api<{ tree: typeof tree }>(`/api/sessions/${encodeURIComponent(currentSid)}/tree`);
        if (!stop) setTree(d.tree);
      } catch { /* 会话切换竞态 */ }
    };
    void load();
    const t = setInterval(load, 8000);
    return () => { stop = true; clearInterval(t); };
  }, [currentSid, artifacts.length]);

  return (
    <aside className="right-panel">
      <div className="panel-tabs">
        {(['properties', 'artifacts', 'files'] as Tab[]).map(t => (
          <button key={t} className={`tab ${tab === t ? 'on' : ''}`} onClick={() => setTab(t)}>
            {t === 'properties' ? '属性'
              : t === 'artifacts'
                ? scope == null ? `产物 (${artifacts.length})`
                  : `产物 (${scopeArtifacts(artifacts, turns, scope).length}/${artifacts.length})`
                : '工作区'}
          </button>
        ))}
      </div>
      <div className="panel-body">
        {tab === 'properties' && <PropertiesPanel />}
        {tab !== 'properties' && <ScopeFilterRow />}
        {tab === 'artifacts' && <ArtifactsTab onOpenFile={onOpenFile} />}
        {tab === 'files' && <WorkspaceTab tree={tree} onOpenFile={onOpenFile} refresh={refreshTree} />}
      </div>
    </aside>
  );
}

/** 打包下载 URL（工作区相对路径；空 = 整个工作区） */
function archiveUrl(sid: string, path: string): string {
  return withToken(`/api/sessions/${encodeURIComponent(sid)}/archive?path=${encodeURIComponent(path)}`);
}

/** 单文件原始字节下载 URL（大文件/二进制无法预览时的下载出路） */
function fileUrl(sid: string, path: string): string {
  return withToken(`/api/sessions/${encodeURIComponent(sid)}/file?path=${encodeURIComponent(path)}&raw=1`);
}

/** 联动筛选行：交付物（产物 tab）与产出物（工作区 tab）共用同一条件同步
 *  过滤。条件维度=产物归属：主控（无 agent 归属）/ 各子任务（turn→subtask
 *  join）；chips 带计数，再点当前条件=回「全部」。单归属会话（无可筛条件
 *  且未挂筛选）不渲染。 */
function ScopeFilterRow() {
  const subtasks = useStore(s => s.subtasks);
  const turns = useStore(s => s.turns);
  const artifacts = useStore(s => s.artifacts);
  const scope = useStore(s => s.artifactScope);
  const setScope = useStore(s => s.setArtifactScope);
  const subOf = new Map(turns.map(t => [t.id, t.subtask_id ?? null]));
  const counts = new Map<number, number>();
  let globalN = 0;
  for (const a of artifacts) {
    if (!a.agent_id) { globalN++; continue; }
    if (a.turn_id == null) continue;
    const sid = subOf.get(a.turn_id);
    if (sid != null) counts.set(sid, (counts.get(sid) ?? 0) + 1);
  }
  const opts: { v: ArtScope; label: string; n: number; tip: string }[] = [
    ...(globalN ? [{
      v: 'global' as const, label: '主控', n: globalN,
      tip: '主控问答/用户导出的产物（无 agent 归属）——产物与工作区同步过滤',
    }] : []),
    ...subtasks
      .filter(st => counts.get(st.id))
      .map(st => ({
        v: st.id as number, label: st.title, n: counts.get(st.id)!,
        tip: `子任务「${st.title}」：产物清单与工作区文件同步过滤`,
      })),
  ];
  if (!opts.length && scope == null) return null;
  return (
    <div className="scope-filter">
      <span className="scope-filter-label">联动筛选</span>
      <button className={`chip ${scope == null ? 'on' : 'off'}`}
        title="不过滤：全部产物与全部工作区文件"
        onClick={() => setScope(null)}>全部 {artifacts.length}</button>
      {opts.map(o => (
        <button key={String(o.v)} className={`chip ${scope === o.v ? 'on' : 'off'}`}
          title={o.tip}
          onClick={() => setScope(scope === o.v ? null : o.v)}>
          {o.label} {o.n}
        </button>
      ))}
    </div>
  );
}

function ArtifactsTab({ onOpenFile }: { onOpenFile: (path: string) => void }) {
  const all = useStore(s => s.artifacts);
  const scope = useStore(s => s.artifactScope);
  const currentSid = useStore(s => s.currentSid);
  const live = useStore(s => s.live);
  const subtasks = useStore(s => s.subtasks);
  const turns = useStore(s => s.turns);
  // 联动筛选：交付物清单先过 scope 再进原分组派生（分组逻辑零改动）
  const artifacts = scopeArtifacts(all, turns, scope);
  if (all.length === 0) return <div className="panel-empty">还没有产物<br />agent 的交付物会出现在 workspace/artifacts/</div>;
  if (artifacts.length === 0) return (
    <div className="panel-empty">该筛选条件下暂无产物<br />
      <button className="link"
        onClick={() => useStore.getState().setArtifactScope(null)}>
        查看全部（{all.length}）
      </button>
    </div>
  );
  // 分组（rev10）：产物按「子任务 > agent」两级（turn→subtask join 派生）；
  // 落不进子任务的（旧数据未 backfill / 主控问答产物）按既有「agent 分组 +
  // 全局」呈现。运行中子任务组挂「写入中」徽标（mtime ≥ turn 开始）
  const runningTurn = live?.status === 'running' ? live.turnId : null;
  const startedAt = live?.startedAt ?? 0;
  const turnSub = new Map(turns.map(t => [t.id, t.subtask_id ?? null]));
  const subOf = (a: (typeof artifacts)[number]) =>
    a.turn_id != null ? turnSub.get(a.turn_id) ?? null : null;
  const attributed = artifacts.filter(a => a.agent_id);
  const globals = artifacts.filter(a => !a.agent_id);
  // 一级：子任务（有成员产物的才建组）
  const subGroups: { key: string; title: string; arts: typeof artifacts; writing: boolean }[] = [];
  for (const a of attributed) {
    const subId = subOf(a);
    if (subId == null) continue;
    const gk = `s:${subId}`;
    let g = subGroups.find(x => x.key === gk);
    if (!g) {
      const st = subtasks.find(x => x.id === subId);
      g = { key: gk, title: st?.title ?? `子任务 #${subId}`, arts: [],
            writing: false };
      subGroups.push(g);
    }
    g.arts.push(a);
  }
  for (const g of subGroups) {
    g.writing = g.arts.some(a => a.turn_id === runningTurn);
  }
  // 二级：子任务组内的 agent 聚合 + 落不进子任务的 agent 旧分组
  const groups: { key: string; title: string; arts: typeof artifacts; writing: boolean }[] = [];
  for (const a of attributed) {
    if (subOf(a) != null) continue;
    const gk = `${a.turn_id}:${a.agent_id}`;
    let g = groups.find(x => x.key === gk);
    if (!g) {
      g = { key: gk, title: `${a.agent_name ?? a.agent_id} 的输出`, arts: [],
            writing: runningTurn === a.turn_id };
      groups.push(g);
    }
    g.arts.push(a);
  }
  // 写入中判定：运行中 turn 的组里 mtime 新于 turn 开始的卡片
  const isWriting = (a: (typeof artifacts)[number]) =>
    !!runningTurn && a.turn_id === runningTurn && (a.mtime ?? 0) * 1000 >= startedAt - 2000;
  const artCard = (a: (typeof artifacts)[number], writing = false) => (
    <div key={a.id} className={`artifact-card ${writing ? 'writing' : ''}`}>
      <div className="art-main">
        <span className={`kind k-${a.kind}`}>{a.kind}</span>
        <span className="art-title" title={a.path}>{a.title}</span>
        {writing
          ? <span className="writing-badge" title="本轮运行中产生/更新">写入中</span>
          : a.created_by === 'export'
            ? <span className="art-src" title="由用户在界面导出">导出</span>
            : <span className="art-src" title="任务运行中生成">任务</span>}
        <span className="art-size">{Math.max(1, Math.round(a.size / 1024))}KB</span>
      </div>
      {a.summary && (
        <div className="art-summary" title={a.path}>{a.summary}</div>
      )}
      <div className="art-actions">
        <button className="link" onClick={() => onOpenFile(a.path)}>预览</button>
        {writing
          ? <span className="art-action-off" title="写入中，等本轮结束后下载">下载</span>
          : <a href={withToken(`/api/artifacts/${a.id}/download`)} download>下载</a>}
        {!writing && <ShareBtn sid={currentSid!} path={a.path} />}
        {!writing && a.kind === 'md' && <ExportBtn sid={currentSid!} path={a.path} />}
      </div>
    </div>
  );
  return (
    <div className="artifact-list">
      <div className="pack-bar">
        <a className="link" href={archiveUrl(currentSid!, 'artifacts')} download>
          <Download size={13} /> 打包下载全部产物（zip）
        </a>
      </div>
      {globals.length > 0 && (
        <>
          <div className="art-group-head">全局 Session 产物（{globals.length}）</div>
          {globals.map(a => artCard(a))}
        </>
      )}
      {subGroups.map(g => (
        <div key={g.key} className="art-agent-group">
          <div className="art-group-head">
            子任务 · {g.title}（{g.arts.length}）
            {g.writing && <span className="writing-badge">写入中</span>}
          </div>
            {(() => {
              // 组内二级：agent 聚合
              const byAgent = new Map<string, { name: string; arts: typeof artifacts }>();
              for (const a of g.arts) {
                const k = a.agent_id ?? '_';
                let e = byAgent.get(k);
                if (!e) { e = { name: a.agent_name ?? a.agent_id ?? '主代理', arts: [] }; byAgent.set(k, e); }
                e.arts.push(a);
              }
              return [...byAgent.entries()].map(([k, e]) => (
                <div key={k}>
                  <div className="art-group-head dim">{e.name}（{e.arts.length}）</div>
                  {e.arts.map(a => artCard(a, isWriting(a)))}
                </div>
              ));
            })()}
        </div>
      ))}
      {groups.map(g => (
        <div key={g.key} className="art-agent-group">
          <div className="art-group-head dim">
            {g.title}（{g.arts.length}）
            {g.writing && <span className="writing-badge">写入中</span>}
          </div>
          {g.arts.map(a => artCard(a, isWriting(a)))}
        </div>
      ))}
    </div>
  );
}

function ExportBtn({ sid, path }: { sid: string; path: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <button className="link" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        await api(`/api/sessions/${encodeURIComponent(sid)}/export`,
          { method: 'POST', body: JSON.stringify({ source_path: path, format: 'html' }) });
        await api(`/api/sessions/${encodeURIComponent(sid)}/export`,
          { method: 'POST', body: JSON.stringify({ source_path: path, format: 'docx' }) });
        await useStore.getState().openSession(sid);
      } catch (e) {
        toast(String(e), false);
      } finally { setBusy(false); }
    }}>{busy ? '导出中…' : '导出 html/docx'}</button>
  );
}

function ShareBtn({ sid, path }: { sid: string; path: string }) {
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle');
  return (
    <button className="link" disabled={state === 'busy'} onClick={async () => {
      setState('busy');
      try {
        const r = await api<{ url: string }>(`/api/sessions/${encodeURIComponent(sid)}/share`,
          { method: 'POST', body: JSON.stringify({ path }) });
        await navigator.clipboard.writeText(r.url);
        setState('done');
        setTimeout(() => setState('idle'), 2000);
      } catch (e) {
        toast(String(e), false);
        setState('idle');
      }
    }}>{state === 'busy' ? '生成中…' : state === 'done' ? '已复制链接' : '分享'}</button>
  );
}

interface TreeNode {
  path: string; dir: boolean; size: number | null;
  children?: TreeNode[];
}

function buildTree(items: { path: string; dir: boolean; size: number | null }[]): TreeNode[] {
  const root: TreeNode = { path: '', dir: true, size: null, children: [] };
  for (const it of items) {
    const parts = it.path.split('/');
    let cur = root;
    for (let i = 0; i < parts.length; i++) {
      const last = i === parts.length - 1;
      const p = parts.slice(0, i + 1).join('/');
      let next = cur.children!.find(c => c.path === p);
      if (!next) {
        next = { path: p, dir: last ? it.dir : true, size: last ? it.size : null, children: [] };
        cur.children!.push(next);
      }
      cur = next;
    }
  }
  const sortRec = (n: TreeNode) => {
    n.children!.sort((a, b) => (a.dir === b.dir ? a.path.localeCompare(b.path) : a.dir ? -1 : 1));
    n.children!.forEach(sortRec);
  };
  sortRec(root);
  return root.children!;
}

/** 联动筛选树剪枝：仅保留可见文件节点及其祖先目录（scope 挂上时，
 *  产出物视图=该条件产物在工作区里的落点，其余文件让路） */
function pruneToPaths(nodes: TreeNode[], visible: Set<string>): TreeNode[] {
  const out: TreeNode[] = [];
  for (const n of nodes) {
    if (n.dir) {
      const kids = pruneToPaths(n.children ?? [], visible);
      if (kids.length) out.push({ ...n, children: kids });
    } else if (visible.has(n.path)) {
      out.push(n);
    }
  }
  return out;
}

/** 在线可编辑的文本后缀（与后端 TEXT_EXTS 对齐的常用子集） */
const EDITABLE_EXTS = new Set(['md', 'txt', 'json', 'csv', 'tsv', 'py', 'js', 'ts', 'tsx',
  'jsx', 'css', 'html', 'htm', 'xml', 'yaml', 'yml', 'sh', 'log', 'ini', 'toml',
  'env', 'conf', 'sql', 'svg', 'lock', 'example']);

function FilesTab({ tree, onOpenFile, refresh }: {
  tree: { path: string; dir: boolean; size: number | null }[];
  onOpenFile: (path: string) => void;
  refresh: () => void;
}) {
  const sid = useStore(s2 => s2.currentSid);
  // 联动筛选：产出物（工作区树）与交付物（产物清单）同条件同步过滤——
  // scope 挂上时树剪枝到该条件产物的路径（含祖先目录）
  const turns = useStore(s => s.turns);
  const artifacts = useStore(s => s.artifacts);
  const scope = useStore(s => s.artifactScope);
  const full = buildTree(tree);
  const nodes = scope == null ? full
    : pruneToPaths(full, new Set(scopeArtifacts(artifacts, turns, scope).map(a => a.path)));
  // —— 文件管理态：新建行内输入 / 重命名行内输入 / 编辑抽屉 / 上传中 ——
  const [creating, setCreating] = useState('');
  const [renaming, setRenaming] = useState<{ path: string; val: string } | null>(null);
  const [edit, setEdit] = useState<{ path: string; text: string; dirty: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const upRef = useRef<HTMLInputElement>(null);

  const S = encodeURIComponent(sid ?? '');

  const createFile = async () => {
    const p = creating.trim();
    if (!p || !sid) return;
    setBusy(true);
    try {
      const r = await api<{ ok: boolean }>(`/api/sessions/${S}/file`, {
        method: 'POST', body: JSON.stringify({ path: p }) });
      if ((r as { ok?: boolean }).ok) { toast(`已新建 ${p}`); setCreating(''); refresh(); }
    } catch (e) { toast(`新建失败：${(e as Error).message}`, true); }
    finally { setBusy(false); }
  };

  const doRename = async () => {
    if (!renaming || !sid) return;
    const nv = renaming.val.trim();
    if (!nv || nv === renaming.path) { setRenaming(null); return; }
    setBusy(true);
    try {
      await api(`/api/sessions/${S}/file`, {
        method: 'PATCH', body: JSON.stringify({ path: renaming.path, new_path: nv }) });
      toast(`已重命名为 ${nv}`); setRenaming(null); refresh();
    } catch (e) { toast(`重命名失败：${(e as Error).message}`, true); }
    finally { setBusy(false); }
  };

  const openEditor = async (path: string) => {
    if (!sid) return;
    try {
      const d = await api<{ cat: string; text?: string }>(
        `/api/sessions/${S}/file?path=${encodeURIComponent(path)}`);
      if (d.cat === 'text' && typeof d.text === 'string') {
        setEdit({ path, text: d.text, dirty: false });
      } else { toast('该文件不是可编辑文本（可下载）', true); }
    } catch (e) { toast(`读取失败：${(e as Error).message}`, true); }
  };

  const saveEdit = async () => {
    if (!edit || !sid) return;
    setBusy(true);
    try {
      await api(`/api/sessions/${S}/file`, {
        method: 'PUT', body: JSON.stringify({ path: edit.path, content: edit.text }) });
      toast(`已保存 ${edit.path}`); setEdit(null); refresh();
    } catch (e) { toast(`保存失败：${(e as Error).message}`, true); }
    finally { setBusy(false); }
  };

  const removeNode = async (path: string, isDir: boolean) => {
    if (!sid) return;
    if (!await askConfirm({ title: `删除${isDir ? '目录' : '文件'}「${path}」？`
      + (isDir ? '（仅空目录可删）' : ''), danger: true })) return;
    setBusy(true);
    try {
      await api(`/api/sessions/${S}/file?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
      toast(`已删除 ${path}`); refresh();
    } catch (e) { toast(`删除失败：${(e as Error).message}`, true); }
    finally { setBusy(false); }
  };

  const doUpload = async (fl: FileList | null) => {
    if (!fl?.length || !sid) return;
    setBusy(true);
    try {
      for (const f of Array.from(fl)) {
        const fd = new FormData();
        fd.append('file', f);
        const r = await api<{ path: string }>(`/api/sessions/${S}/upload`,
          { method: 'POST', body: fd });
        toast(`${f.name} → ${r.path}`);
      }
      refresh();
    } catch (e) { toast(`上传失败：${(e as Error).message}`, true); }
    finally { setBusy(false); if (upRef.current) upRef.current.value = ''; }
  };

  const acts = {
    onEdit: openEditor,
    startRename: (p: string) => setRenaming({ path: p, val: p }),
    onRenameChange: (v: string) => setRenaming(r => (r ? { ...r, val: v } : r)),
    onRenameCommit: () => void doRename(),
    onRenameCancel: () => setRenaming(null),
    onDelete: removeNode, renaming,
  };

  return (
    <div className="files-tab">
      {sid && (
        <div className="pack-bar ws-toolbar">
          <button className="btn xs ghost" disabled={busy}
            onClick={() => upRef.current?.click()}>上传</button>
          <button className="btn xs ghost" disabled={busy}
            onClick={() => setCreating(creating ? '' : '新建文件.md')}>＋新建</button>
          <a className="link" href={archiveUrl(sid, '')} download>
            <Download size={13} /> 打包（zip）
          </a>
          <input ref={upRef} type="file" multiple hidden
            onChange={e => void doUpload(e.target.files)} />
        </div>
      )}
      {creating !== '' && (
        <div className="ws-inline-new">
          <input value={creating} onChange={e => setCreating(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void createFile();
                              if (e.key === 'Escape') setCreating(''); }}
            placeholder="文件名（可含子目录，如 notes/a.md）" autoFocus />
          <button className="btn xs primary" disabled={busy} onClick={() => void createFile()}>建</button>
        </div>
      )}
      <div className="file-tree">
        {nodes.map(n => <FileNode key={n.path} node={n} depth={0} onOpen={onOpenFile} {...acts} />)}
      </div>
      {nodes.length === 0 && scope != null && (
        <div className="panel-empty">该筛选条件下工作区暂无文件</div>
      )}
      {edit && (
        <div className="ws-editor">
          <div className="ws-editor-head">
            <span className="mono">{edit.path}{edit.dirty ? ' •' : ''}</span>
            <span>
              <button className="btn xs primary" disabled={busy || !edit.dirty}
                onClick={() => void saveEdit()}>保存</button>
              <button className="btn xs ghost" onClick={() => setEdit(null)}>收起</button>
            </span>
          </div>
          <textarea className="ws-editor-body" spellCheck={false} value={edit.text}
            onChange={e => setEdit({ ...edit, text: e.target.value, dirty: true })} />
        </div>
      )}
      <div className="files-hint muted">点击预览；hover 行内编辑/改名/删除；上传落 inputs/</div>
    </div>
  );
}

/** 工作区 tab：三子 tab（文件/记忆/历史）——记忆=本工作区项目记忆域，
 * 历史=文件快照（可回滚）。子 tab 本地态，切会话不重置（无跨会话一致性风险）。 */
function WorkspaceTab({ tree, onOpenFile, refresh }: {
  tree: { path: string; dir: boolean; size: number | null }[];
  onOpenFile: (path: string) => void;
  refresh: () => void;
}) {
  const [sub, setSub] = useState<'files' | 'memory' | 'history'>('files');
  return (
    <div className="ws-wrap">
      <div className="ws-subtabs">
        {(['files', 'memory', 'history'] as const).map(s => (
          <button key={s} className={`btn xs ${sub === s ? 'primary' : 'ghost'}`}
            onClick={() => setSub(s)}>
            {s === 'files' ? '文件' : s === 'memory' ? '记忆' : '历史'}
          </button>
        ))}
      </div>
      {sub === 'files' && <FilesTab tree={tree} onOpenFile={onOpenFile} refresh={refresh} />}
      {sub === 'memory' && <WorkspaceMemory />}
      {sub === 'history' && <SnapshotList />}
    </div>
  );
}

interface MemEntry { id: string; summary: string; origin_session: string; created_at: string; draft?: boolean }

/** 工作区记忆（项目记忆域）：列表/新建/删除/看正文——写操作走
 * /api/memory/* 同域（后端会话属主收口）。 */
function WorkspaceMemory() {
  const sid = useStore(s => s.currentSid);
  const [domain, setDomain] = useState('');
  const [entries, setEntries] = useState<MemEntry[]>([]);
  const [open, setOpen] = useState<MemEntry | null>(null);
  const [body, setBody] = useState('');
  const [creating, setCreating] = useState(false);
  const [summary, setSummary] = useState('');
  const [content, setContent] = useState('');
  const [err, setErr] = useState('');

  const reload = async () => {
    if (!sid) return;
    try {
      const d = await api<{ domain: string; entries: MemEntry[] }>(
        `/api/sessions/${encodeURIComponent(sid)}/memory`);
      setDomain(d.domain);
      setEntries([...d.entries].reverse());   // 新在前
    } catch { setEntries([]); }
  };
  useEffect(() => { setOpen(null); void reload(); }, [sid]);

  const show = async (e: MemEntry) => {
    if (open?.id !== e.id) {
      setOpen(e); setBody('加载中…');
      try {
        const d = await api<{ text: string }>(
          `/api/memory/file?domain=${domain}&id=${e.id}`);
        setBody(d.text.replace(/^---\n[\s\S]*?\n---\n/, '').trim());
      } catch { setBody('（加载失败）'); }
    } else setOpen(null);
  };

  if (!sid) return <div className="panel-empty">未选中会话</div>;
  return (
    <div className="ws-memory">
      <div className="admin-toolbar">
        <button className="btn sm" onClick={() => setCreating(v => !v)}>＋ 新建</button>
        <span className="muted" style={{ fontSize: 'var(--fs-xs)' }}>
          本工作区记忆（仅本会话/项目的新会话注入）
        </span>
      </div>
      {creating && (
        <div className="new-skill-form">
          <input placeholder="摘要（≤20 字）" value={summary}
            onChange={e => setSummary(e.target.value)}  autoComplete="off"/>
          <textarea className="mono" rows={4} placeholder="记忆正文" value={content}
            onChange={e => setContent(e.target.value)}  autoComplete="off"/>
          <div className="modal-foot">
            <button className="btn ghost sm" onClick={() => { setCreating(false); setSummary(''); setContent(''); }}>取消</button>
            <button className="btn primary sm" disabled={!summary.trim() || !content.trim()}
              onClick={() => void (async () => {
                try {
                  await api('/api/memory/file', { method: 'POST', body: JSON.stringify({
                    domain, summary: summary.trim(), content }) });
                  setCreating(false); setSummary(''); setContent(''); void reload();
                } catch (e2) { setErr(String(e2)); }
              })()}>创建</button>
          </div>
          {err && <div className="admin-err">{err}</div>}
        </div>
      )}
      <div className="admin-list">
        {entries.map(e => (
          <div key={e.id} className={`hub-card slim${open?.id === e.id ? ' on' : ''}`}>
            <b onClick={() => void show(e)} role="button">{e.summary}{e.draft
              ? <span className="chip" title="候选教训——保存转正">待确认</span> : null}</b>
            {open?.id === e.id && (
              <pre className="mono" style={{
                fontSize: 'var(--fs-sm)', whiteSpace: 'pre-wrap',
                background: 'var(--bg2)', padding: 8, borderRadius: 6 }}>{body}</pre>
            )}
            <div className="sk-foot">
              <span className="sk-time">{e.created_at?.slice(0, 16)}</span>
              <button className="link danger-link" onClick={() => void (async () => {
                if (!await askConfirm({ title: `删除「${e.summary}」？历史保留可恢复。`, danger: true })) return;
                try {
                  await api(`/api/memory/entry?domain=${domain}&id=${e.id}`, { method: 'DELETE' });
                  toast('已删除（历史在，可恢复）');
                  if (open?.id === e.id) setOpen(null);
                  void reload();
                } catch (er) { toast(`删除失败：${String(er)}`, false); }
              })()}>删除</button>
            </div>
          </div>
        ))}
        {!entries.length && (
          <div className="panel-empty">（暂无记忆——本工作区会话沉淀的
            项目事实/教训会出现在这里；首次写入自动建域）</div>)}
      </div>
    </div>
  );
}

interface Snap { point: string; at: string; files?: number }

/** 工作区历史：文件快照清单 + 回滚（POST /sessions/{sid}/rollback）。 */
function SnapshotList() {
  const sid = useStore(s => s.currentSid);
  const [snaps, setSnaps] = useState<Snap[]>([]);
  const [busy, setBusy] = useState('');
  const reload = async () => {
    if (!sid) return;
    try {
      const d = await api<{ snapshots: Snap[] }>(
        `/api/sessions/${encodeURIComponent(sid)}/snapshots`);
      setSnaps(d.snapshots ?? []);
    } catch { setSnaps([]); }
  };
  useEffect(() => { void reload(); }, [sid]);
  if (!sid) return <div className="panel-empty">未选中会话</div>;
  return (
    <div className="ws-snaps">
      <div className="files-hint muted">每次文件改动的快照点；回滚会恢复当时的工作区文件</div>
      {snaps.map(sp => (
        <div key={sp.point} className="hub-card slim">
          <div className="mono" style={{ fontSize: 'var(--fs-sm)' }}>{sp.point}</div>
          <div className="sk-foot">
            <span className="sk-time">{sp.at} {sp.files ? `· ${sp.files} 文件` : ''}</span>
            <button className="link danger-link" disabled={busy === sp.point}
              onClick={() => void (async () => {
                if (!await askConfirm({
                  title: `回滚到快照 ${sp.point}？此后改动将被撤销。`, danger: true })) return;
                setBusy(sp.point);
                try {
                  await api(`/api/sessions/${encodeURIComponent(sid)}/rollback`, {
                    method: 'POST', body: JSON.stringify({ point: sp.point }) });
                  toast(`已回滚到 ${sp.point}`);
                } catch (e) { toast(`回滚失败：${String(e)}`, false); }
                finally { setBusy(''); }
              })()}>回滚</button>
          </div>
        </div>
      ))}
      {!snaps.length && <div className="panel-empty">（暂无快照——文件改动后自动生成）</div>}
    </div>
  );
}

interface FileActs {
  onEdit: (p: string) => void;
  startRename: (p: string) => void;
  onRenameChange: (v: string) => void;
  onRenameCommit: () => void;
  onRenameCancel: () => void;
  onDelete: (p: string, isDir: boolean) => void;
  renaming: { path: string; val: string } | null;
}

function FileNode({ node, depth, onOpen, onEdit, startRename, onRenameChange,
  onRenameCommit, onRenameCancel, onDelete, renaming }: {
  node: TreeNode; depth: number; onOpen: (p: string) => void;
} & FileActs) {
  const [open, setOpen] = useState(depth < 1);
  if (node.dir) {
    return (
      <div>
        <div className="fn dir" style={{ paddingLeft: depth * 12 }} onClick={() => setOpen(!open)}>
          <span className="fn-chevron">{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</span>
          <Folder size={13} /> {node.path.split('/').pop()}
          <span className="fn-acts" onClick={e => e.stopPropagation()}>
            <button className="fn-act" title="重命名/移动"
              onClick={() => startRename(node.path)}>✎</button>
            <button className="fn-act" title="删除（仅空目录）"
              onClick={() => onDelete(node.path, true)}>✕</button>
            <a className="fn-act fn-pack" title="打包下载此目录（zip）"
              href={archiveUrl(useStore.getState().currentSid ?? '', node.path)} download>
              <Download size={12} />
            </a>
          </span>
        </div>
        {open && node.children!.map(c => <FileNode key={c.path} node={c} depth={depth + 1} onOpen={onOpen}
          onEdit={onEdit} startRename={startRename} onRenameChange={onRenameChange}
          onRenameCommit={onRenameCommit} onRenameCancel={onRenameCancel}
          onDelete={onDelete} renaming={renaming} />)}
      </div>
    );
  }
  // 重命名行内态：整行变输入框——Enter 提交 / Esc・失焦取消（保守不误改）
  if (renaming?.path === node.path) {
    return (
      <div className="fn file renaming" style={{ paddingLeft: depth * 12 + 18 }}>
        <FileDoc size={13} />
        <input className="fn-rename-input" value={renaming.val} autoFocus
          onChange={e => onRenameChange(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') onRenameCommit();
                            if (e.key === 'Escape') onRenameCancel(); }}
          onBlur={() => onRenameCancel()} />
      </div>
    );
  }
  const ext = node.path.split('.').pop()?.toLowerCase() ?? '';
  const editable = EDITABLE_EXTS.has(ext);
  return (
    <div className="fn file" style={{ paddingLeft: depth * 12 + 18 }} onClick={() => onOpen(node.path)}>
      <FileDoc size={13} /> {node.path.split('/').pop()} <span className="fn-size">{node.size ? `${Math.round(node.size / 1024)}K` : ''}</span>
      <span className="fn-acts" onClick={e => e.stopPropagation()}>
        {editable && <button className="fn-act" title="在线编辑"
          onClick={() => onEdit(node.path)}>✎</button>}
        <button className="fn-act" title="重命名/移动"
          onClick={() => startRename(node.path)}>⇄</button>
        <button className="fn-act" title="删除"
          onClick={() => onDelete(node.path, false)}>✕</button>
        <a className="fn-act fn-pack" title="下载此文件"
          href={fileUrl(useStore.getState().currentSid ?? '', node.path)} download>
          <Download size={12} />
        </a>
      </span>
    </div>
  );
}
