import { useEffect, useState } from 'react';
import { useStore } from '../stores/sessions';
import type { ArtifactInfo, TurnInfo } from '../stores/sessions';
import { api, withToken } from '../api/client';
import PropertiesPanel from './PropertiesPanel';
import { Folder, FileDoc, ChevronDown, ChevronRight, Download } from './icons';
import { toast } from '../stores/toasts';

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
        {tab === 'files' && <FilesTab tree={tree} onOpenFile={onOpenFile} />}
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

function FilesTab({ tree, onOpenFile }: {
  tree: { path: string; dir: boolean; size: number | null }[];
  onOpenFile: (path: string) => void;
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
  return (
    <div className="files-tab">
      {sid && (
        <div className="pack-bar">
          <a className="link" href={archiveUrl(sid, '')} download>
            <Download size={13} /> 打包下载整个工作区（zip）
          </a>
        </div>
      )}
      <div className="file-tree">
        {nodes.map(n => <FileNode key={n.path} node={n} depth={0} onOpen={onOpenFile} />)}
      </div>
      {nodes.length === 0 && scope != null && (
        <div className="panel-empty">该筛选条件下工作区暂无文件</div>
      )}
      <div className="files-hint muted">点击文件在主区新 tab 预览（md / html）</div>
    </div>
  );
}

function FileNode({ node, depth, onOpen }: { node: TreeNode; depth: number; onOpen: (p: string) => void; }) {
  const [open, setOpen] = useState(depth < 1);
  if (node.dir) {
    return (
      <div>
        <div className="fn dir" style={{ paddingLeft: depth * 12 }} onClick={() => setOpen(!open)}>
          <span className="fn-chevron">{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</span>
          <Folder size={13} /> {node.path.split('/').pop()}
          <a className="fn-pack" title="打包下载此目录（zip）"
             href={archiveUrl(useStore.getState().currentSid ?? '', node.path)} download
             onClick={e => e.stopPropagation()}>
            <Download size={12} />
          </a>
        </div>
        {open && node.children!.map(c => <FileNode key={c.path} node={c} depth={depth + 1} onOpen={onOpen} />)}
      </div>
    );
  }
  return (
    <div className="fn file" style={{ paddingLeft: depth * 12 + 18 }} onClick={() => onOpen(node.path)}>
      <FileDoc size={13} /> {node.path.split('/').pop()} <span className="fn-size">{node.size ? `${Math.round(node.size / 1024)}K` : ''}</span>
      <a className="fn-pack" title="下载此文件"
         href={fileUrl(useStore.getState().currentSid ?? '', node.path)} download
         onClick={e => e.stopPropagation()}>
        <Download size={12} />
      </a>
    </div>
  );
}
