import { useEffect, useState } from 'react';
import type { ComponentType, ReactNode } from 'react';
import { useStore } from '../stores/sessions';
import type { ProjectInfo, CategoryInfo, MoveDest } from '../stores/sessions';
import { fmtTokens } from '../api/client';
import PopupMenu from './Menu';
import type { MenuEntry } from './Menu';
import {
  Flask, Code, Chat, Bot, Plus, Star, Pencil, Undo, Trash,
  Folder, ChevronDown, ChevronRight, Box, MoreVertical, Pin, Archive, Tag, Clock,
  ArrowRight, Settings,
} from './icons';
import CategoryIcon from './CategoryIcon';

const PROFILE_ICON: Record<string, ComponentType<{ size?: number }>> = {
  researcher: Flask, coder: Code, assistant: Chat,
};

/** 「最近」分区默认展开条数（「更多」每次再加一页） */
const PAGE = 20;

/** 分区桶：置顶 > 收藏 > 自定义分类 > 最近（归档走 status 单列） */
type RowBucket = 'pinned' | 'starred' | 'recent' | number;
const bucketOf = (x: { pinned?: number; starred?: number; category_id?: number | null }): RowBucket =>
  x.pinned ? 'pinned' : x.starred ? 'starred' : (x.category_id ?? 'recent');

type TopRow =
  | { kind: 'session'; s: { id: string; updated_at: string;
      pinned?: number; starred?: number; category_id?: number | null;
      active_turn?: { status: string } | null } }
  | { kind: 'project'; p: ProjectInfo };
const rowBucket = (r: TopRow): RowBucket => r.kind === 'session' ? bucketOf(r.s) : bucketOf(r.p);
const rowTs = (r: TopRow) => r.kind === 'session'
  ? new Date(r.s.updated_at).getTime()
  : new Date(r.p.updated_at ?? 0).getTime();
/** 排序键：在跑任务恒置顶（含跑着子任务的项目组——活跃优先于最近完成，
 *  长跑任务期间 updated_at 不动也不被后来完成的旧任务压下去） */
const mkRowOrder = (kidsRunning: Set<string>) => (a: TopRow, b: TopRow) => {
  const run = (r: TopRow) => r.kind === 'session'
    ? r.s.active_turn?.status === 'running'
    : kidsRunning.has(r.p.id);
  return Number(run(b)) - Number(run(a)) || rowTs(b) - rowTs(a);
};

/** 「移动到」二级面板：置顶/最近/收藏/自定义分类（含内联新建），当前分区打勾 */
function moveEntries(cur: RowBucket, onMove: (d: MoveDest) => void,
                     categories: CategoryInfo[],
                     createCategory: (n: string, icon?: string) => Promise<CategoryInfo | null>): MenuEntry[] {
  const item = (key: string, label: string,
                icon: ComponentType<{ size?: number }>, dest: MoveDest): MenuEntry => ({
    kind: 'item', key, label, icon,
    check: cur === (typeof dest === 'object' ? dest.cat : dest),
    onClick: () => onMove(dest),
  });
  return [
    item('pinned', '置顶', Pin, 'pinned'),
    item('starred', '收藏', Star, 'starred'),
    item('recent', '最近（默认）', Clock, 'recent'),
    ...(categories.length ? [
      { kind: 'divider', key: 'sep-cat' } as MenuEntry,
      ...categories.map((c): MenuEntry => ({
        kind: 'item', key: `cat-${c.id}`, label: c.name, icon: Tag,
        check: cur === c.id, onClick: () => onMove({ cat: c.id }),
      })),
    ] : []),
    { kind: 'input', key: 'new-cat', placeholder: '新建分类并移入…',
      onSubmit: v => { void (async () => {
        const cat = await createCategory(v.slice(0, 40));
        if (cat) onMove({ cat: cat.id });
      })(); } },
  ];
}

/** 空间元信息：activeSpace → 标题/副题/图标 */
function spaceMeta(activeSpace: string, categories: CategoryInfo[]): {
  title: string; sub: string; icon?: ReactNode;
} {
  if (activeSpace === 'pinned') return { title: '置顶关注', sub: '钉住的重要任务' };
  if (activeSpace === 'starred') return { title: '收藏', sub: '打过星标的任务与项目' };
  if (activeSpace === 'archive') return { title: '归档', sub: '已收起的任务（可恢复）' };
  const cat = categories.find(c => `cat:${c.id}` === activeSpace);
  if (cat) return { title: cat.name, sub: '自定义空间', icon: <CategoryIcon icon={cat.icon} size={13} /> };
  return { title: '最近', sub: '全部进行中的任务与项目' };
}

export default function Sidebar({ onNew, onAdmin, onNav, adminActive }: {
  onNew: () => void; onAdmin: (tab?: 'cost') => void;
  onNav?: () => void; adminActive?: boolean;
}) {
  // 字段级订阅（整店订阅会在 live delta 风暴下全列表重渲染）
  const sessions = useStore(s => s.sessions);
  const currentSid = useStore(s => s.currentSid);
  const openSession = useStore(s => s.openSession);
  const projects = useStore(s => s.projects);
  const categories = useStore(s => s.categories);
  const activeSpace = useStore(s => s.activeSpace);
  const [limit, setLimit] = useState(PAGE);
  const [q, setQ] = useState('');
  const [newProj, setNewProj] = useState(false);
  const [projTitle, setProjTitle] = useState('');

  useEffect(() => {
    void useStore.getState().loadProjects();
    void useStore.getState().loadCategories();
  }, []);

  // ---- 分区归桶 ----
  const activeSessions = sessions.filter(s => s.status === 'active');
  const archivedSessions = sessions.filter(s => s.status === 'archived');
  // 顶层任务行：独立会话 + 被置顶/收藏/分类「抽出」的项目子任务（项目内只留默认桶的）
  const topSessions = activeSessions.filter(s => !s.project_id || bucketOf(s) !== 'recent');
  const activeProjects = projects.filter(p => p.status === 'active');
  const archivedProjects = projects.filter(p => p.status === 'archived');
  const byProject = new Map<string, typeof activeSessions>();
  for (const s of activeSessions) {
    if (!s.project_id || bucketOf(s) !== 'recent') continue;
    if (!byProject.has(s.project_id)) byProject.set(s.project_id, []);
    byProject.get(s.project_id)!.push(s);
  }
  const kidsRunning = new Set(
    activeSessions
      .filter(s => s.project_id && s.active_turn?.status === 'running')
      .map(s => s.project_id!));
  const topRows: TopRow[] = [
    ...topSessions.map(s => ({ kind: 'session', s }) as TopRow),
    ...activeProjects.map(p => ({ kind: 'project', p }) as TopRow),
  ].sort(mkRowOrder(kidsRunning));
  const rowsIn = (rows: TopRow[], b: RowBucket) => rows.filter(r => rowBucket(r) === b);

  // 归档：独立任务平铺；归档项目成组（子任务收进组里不重复平铺）
  const archivedByProject = new Map<string, typeof archivedSessions>();
  for (const s of archivedSessions) {
    if (!s.project_id) continue;
    if (!archivedByProject.has(s.project_id)) archivedByProject.set(s.project_id, []);
    archivedByProject.get(s.project_id)!.push(s);
  }
  const archivedTop: TopRow[] = [
    ...archivedSessions
      .filter(s => !s.project_id || !archivedProjects.some(p => p.id === s.project_id))
      .map(s => ({ kind: 'session', s }) as TopRow),
    ...archivedProjects.map(p => ({ kind: 'project', p }) as TopRow),
  ].sort((a, b) => rowTs(b) - rowTs(a));

  // ---- 当前空间的行（单桶渲染） ----
  const cat = categories.find(c => `cat:${c.id}` === activeSpace);
  const spaceRows: TopRow[] = activeSpace === 'archive' ? archivedTop
    : activeSpace === 'pinned' ? rowsIn(topRows, 'pinned')
    : activeSpace === 'starred' ? rowsIn(topRows, 'starred')
    : cat ? rowsIn(topRows, cat.id)
    : rowsIn(topRows, 'recent');
  const shownRows = activeSpace === 'recent'
    ? spaceRows.slice(0, limit) : spaceRows;
  const meta = spaceMeta(activeSpace, categories);

  // 真实口径：z.ai API 按量价（旧数据无该键时回落 CLI 口径）；total_all 含 cache read
  const allCost = sessions.reduce(
    (a, s) => a + (s.usage?.cost_api_usd ?? s.usage?.cost_usd ?? 0), 0);
  const allTokens = sessions.reduce(
    (a, s) => a + (s.usage?.total_all ?? s.usage?.total ?? 0), 0);

  // 搜索：标题子串（不分大小写），跨全部分区匹配，不受分页限制
  const query = q.trim().toLowerCase();
  const matches = query
    ? sessions.filter(s =>
        s.title.toLowerCase().includes(query)
        || (s.project_title ?? '').toLowerCase().includes(query))
    : [];
  const searching = query.length > 0;
  const overlay = !!adminActive;

  // 点会话即离开管理页（清 hash 回到聊天视图）；移动端顺带收抽屉
  const openAndLeaveAdmin = (sid: string) => {
    onNav?.();
    if (overlay) location.hash = '';
    void openSession(sid);
  };

  // 分类内直接新建：任务零选择建完即进（title 自动），项目走内联输入
  const addTaskInSpace = async () => {
    try {
      const s = await useStore.getState().createSession(
        cat ? { category_id: cat.id } : {});
      if (overlay) location.hash = '';
      void useStore.getState().openSession(s.id);
    } catch (e) {
      alert(`新建任务失败：${e instanceof Error ? e.message : e}`);
    }
  };
  const addProjectInSpace = async (title: string) => {
    try {
      await useStore.getState().createProject(title.slice(0, 80), cat?.id);
    } catch (e) {
      alert(`新建项目失败：${e instanceof Error ? e.message : e}`);
    }
  };

  const renderRow = (r: TopRow, archived = false) => r.kind === 'session'
    ? <SessionRow key={r.s.id} sid={r.s.id} archived={archived}
        onClick={() => openAndLeaveAdmin(r.s.id)} current={r.s.id === currentSid && !overlay} />
    : <ProjectGroup key={r.p.id} project={r.p}
        kids={(archived ? archivedByProject.get(r.p.id) : byProject.get(r.p.id)) ?? []}
        collapsed={archivedByProject.has(r.p.id) && archived ? true : undefined}
        onToggle={() => {}}
        onOpen={openAndLeaveAdmin} currentSid={currentSid} overlay={overlay} archived={archived} />;

  return (
    <aside className="sidebar">
      <div className="space-head">
        <div className="space-title-row">
          <span className="space-ico">{meta.icon ?? <Clock size={13} />}</span>
          <div className="space-names">
            <span className="space-kicker">当前空间</span>
            <span className="space-name">{meta.title}</span>
          </div>
        </div>
        {activeSpace !== 'archive' && (
          <div className="space-actions">
            <button className="btn primary space-new" onClick={() => (cat ? void addTaskInSpace() : onNew())}>
              <Plus size={14} /> 新任务
            </button>
            <button className="btn ghost sm" onClick={() => setNewProj(v => !v)}>
              <Folder size={13} /> {newProj ? '收起' : '新建项目'}
            </button>
          </div>
        )}
        {newProj && (
          <input className="new-project-input" value={projTitle} autoFocus maxLength={80}
                 placeholder="项目名（回车创建，共享工作区的任务容器）"
                 onChange={e => setProjTitle(e.target.value)}
                 onKeyDown={async e => {
                   if (e.key === 'Escape') { setNewProj(false); setProjTitle(''); }
                   else if (e.key === 'Enter') {
                     const t = projTitle.trim();
                     if (!t) return;
                     setNewProj(false); setProjTitle('');
                     await addProjectInSpace(t);
                   }
                 }} />
        )}
      </div>
      <div className="sidebar-search">
        <input value={q} placeholder="搜索任务…" maxLength={80}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Escape') setQ(''); }} />
        {q && <button className="search-clear" title="清空"
          onClick={() => setQ('')}>×</button>}
      </div>
      <div className="sidebar-list">
        {searching ? (
          <>
            {matches.map(s => (
              <div key={s.id} className="search-hit">
                {s.project_title && <span className="hit-project">{s.project_title} / </span>}
                <SessionRow sid={s.id} archived={s.status === 'archived'}
                  onClick={() => openAndLeaveAdmin(s.id)} current={s.id === currentSid && !overlay} />
              </div>
            ))}
            {matches.length === 0 && <div className="sidebar-empty">没有匹配的任务</div>}
          </>
        ) : (
          <>
            {shownRows.map(r => renderRow(r, activeSpace === 'archive'))}
            {activeSpace === 'recent' && shownRows.length < spaceRows.length && (
              <button className="more-btn" onClick={() => setLimit(l => l + PAGE)}>
                更多（还有 {spaceRows.length - shownRows.length} 个）
              </button>
            )}
            {activeSpace === 'recent' && shownRows.length >= spaceRows.length && limit > PAGE && (
              <button className="more-btn" onClick={() => setLimit(PAGE)}>收起</button>
            )}
            {!searching && spaceRows.length === 0 && (
              <div className="sidebar-empty">
                {activeSpace === 'archive' ? '没有归档任务' : '这个空间还没有任务'}
              </div>
            )}
          </>
        )}
      </div>
      <div className="sidebar-foot">
        <button className="sidebar-usage" title="真实成本（z.ai API 按量价）· 点击到管理中心的成本分析"
          onClick={() => onAdmin('cost')}>
          累计 <b>{fmtTokens(allTokens)}</b> tokens · <b>${allCost.toFixed(2)}</b>
        </button>
      </div>
    </aside>
  );
}

/** 项目组行：折叠箭头 + 标题 + 计数 + 「…」菜单（子任务/改名/移动/归档）。
 *  子任务行缩进渲染在组内；归档项目在归档分区成组展示。 */
function ProjectGroup({ project, kids, collapsed: collapsedProp, onToggle, onOpen, currentSid, overlay, archived = false }: {
  project: ProjectInfo; kids: any[];
  collapsed?: boolean; onToggle: () => void;
  onOpen: (sid: string) => void; currentSid: string | null; overlay: boolean;
  archived?: boolean;
}) {
  const categories = useStore(st => st.categories);
  const renameProject = useStore(s => s.renameProject);
  const archiveProject = useStore(s => s.archiveProject);
  const restoreProject = useStore(s => s.restoreProject);
  const purgeProject = useStore(s => s.purgeProject);
  const createSubtask = useStore(s => s.createSubtask);
  const moveProject = useStore(s => s.moveProject);
  const createCategory = useStore(s => s.createCategory);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [menuEl, setMenuEl] = useState<HTMLElement | null>(null);
  const [collapsedLocal, setCollapsedLocal] = useState(false);
  const collapsed = collapsedProp ?? collapsedLocal;
  const toggle = () => {
    if (collapsedProp === undefined) setCollapsedLocal(v => !v);
    else onToggle();
  };
  const commit = (v: string) => {
    const t = v.trim();
    if (t && t !== project.title) void renameProject(project.id, t.slice(0, 80));
    setEditing(false);
  };
  const addSub = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const s = await createSubtask(project.id);
      if (overlay) location.hash = '';
      void useStore.getState().openSession(s.id);
    } catch (e) {
      alert(`新建子任务失败：${e instanceof Error ? e.message : e}`);
    } finally { setBusy(false); }
  };
  const purge = async () => {
    if (!confirm(`彻底删除项目「${project.title}」？\n全部子任务与工作区文件一并删除，不可恢复。`)) return;
    try { await purgeProject(project.id); }
    catch (e) { alert(`删除失败：${e instanceof Error ? e.message : e}`); }
  };
  const items: MenuEntry[] = archived ? [
    { key: 'restore', label: '恢复项目（子任务一并恢复）', icon: Undo,
      onClick: () => void restoreProject(project.id) },
    { key: 'purge', label: '彻底删除项目（含子任务）', icon: Trash, danger: true,
      onClick: () => void purge() },
  ] : [
    { key: 'move', label: '移动到…', icon: ArrowRight, subTitle: '移动到',
      sub: moveEntries(bucketOf(project), d => void moveProject(project.id, d),
                       categories, createCategory) },
    { key: 'sub', label: '新建子任务（共享工作区）', icon: Plus, onClick: () => void addSub() },
    { key: 'rename', label: '改名', icon: Pencil, onClick: () => setEditing(true) },
    { kind: 'divider', key: 'sep-arch' } as MenuEntry,
    { key: 'archive', label: '归档项目（子任务一并归档）', icon: Archive,
      onClick: () => void archiveProject(project.id) },
    { key: 'purge', label: '删除项目（含全部子任务与工作区）', icon: Trash, danger: true,
      onClick: () => void purge() },
  ];
  return (
    <div className={`project-group ${collapsed ? 'collapsed' : ''}`}>
      <div className={`project-row ${menuEl ? 'menu-open' : ''}`} title="项目：同工作区多任务容器（双击改名）"
           onClick={toggle}
           onDoubleClick={e => { e.stopPropagation(); if (!archived) setEditing(true); }}>
        <span className="proj-chevron">{collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}</span>
        <Folder size={13} />
        {editing
          ? <input className="row-edit" defaultValue={project.title} autoFocus maxLength={80}
              onClick={e => e.stopPropagation()}
              onKeyDown={e => {
                if (e.key === 'Enter') commit((e.target as HTMLInputElement).value);
                else if (e.key === 'Escape') setEditing(false);
              }}
              onBlur={e => commit(e.target.value)} />
          : <span className="row-title">{project.title}</span>}
        {!editing && !archived && project.pinned ? <Pin size={11} className="row-flag pin" /> : null}
        {!editing && !archived && project.starred ? <Star size={11} filled className="row-flag star" /> : null}
        {!editing && <span className="proj-count">{kids.length}</span>}
        {!editing && (
          <span className="row-ops">
            <button className="row-op" title="更多操作"
                    onClick={e => { e.stopPropagation(); setMenuEl(e.currentTarget); }}>
              <MoreVertical size={14} />
            </button>
          </span>
        )}
      </div>
      {!collapsed && kids.map(k => (
        <div className="proj-kid" key={k.id}>
          <SessionRow sid={k.id} onClick={() => onOpen(k.id)} current={k.id === currentSid && !overlay} />
        </div>
      ))}
      {!collapsed && !archived && kids.length === 0 && (
        <button className="proj-empty-add" onClick={() => void addSub()}>
          <Plus size={12} /> 还没有子任务，点这里开一个
        </button>
      )}
      {menuEl && <PopupMenu anchor={menuEl} items={items} onClose={() => setMenuEl(null)} />}
    </div>
  );
}

function SessionRow({ sid, onClick, current, archived = false }: {
  sid: string; onClick: () => void; current: boolean; archived?: boolean;
}) {
  const s = useStore(st => st.sessions.find(x => x.id === sid))!;
  const categories = useStore(st => st.categories);
  const archiveSession = useStore(s => s.archiveSession);
  const restoreSession = useStore(s => s.restoreSession);
  const purgeSession = useStore(s => s.purgeSession);
  const renameSession = useStore(s => s.renameSession);
  const promoteSession = useStore(s => s.promoteSession);
  const moveSession = useStore(s => s.moveSession);
  const openProps = useStore(s => s.openProps);
  const createCategory = useStore(s => s.createCategory);
  const [editing, setEditing] = useState(false);
  const [menuEl, setMenuEl] = useState<HTMLElement | null>(null);
  const running = s.active_turn?.status === 'running' || s.active_turn?.status === 'queued';

  const commit = (v: string) => {
    const t = v.trim();
    if (t && t !== s.title) void renameSession(sid, t.slice(0, 80));
    setEditing(false);
  };

  const purge = async () => {
    if (!confirm(`彻底删除「${s.title}」？\n工作区文件将一并删除，不可恢复。`)) return;
    try {
      await purgeSession(sid);
    } catch (e) {
      alert(`删除失败：${e instanceof Error ? e.message : e}`);
    }
  };

  const items: MenuEntry[] = archived ? [
    { key: 'props', label: '属性', icon: Settings,
      onClick: () => void openProps(s.id) },
    { key: 'restore', label: '恢复为进行中', icon: Undo,
      onClick: () => void restoreSession(s.id) },
    { key: 'purge', label: '彻底删除（含工作区）', icon: Trash, danger: true,
      onClick: () => void purge() },
  ] : [
    { key: 'props', label: '属性（参数/安全/挂接）', icon: Settings,
      onClick: () => void openProps(s.id) },
    { key: 'move', label: '移动到…', icon: ArrowRight, subTitle: '移动到',
      sub: moveEntries(bucketOf(s), d => void moveSession(s.id, d), categories, createCategory) },
    { key: 'rename', label: '改名', icon: Pencil, onClick: () => setEditing(true) },
    ...(!s.project_id ? [{
      key: 'promote', label: '升级为项目（共享工作区容器）', icon: Box,
      onClick: () => void promoteSession(s.id),
    }] : []),
    { kind: 'divider', key: 'sep-arch' } as MenuEntry,
    { key: 'archive', label: '归档', icon: Archive,
      onClick: () => void archiveSession(s.id) },
    { key: 'purge', label: '删除（含工作区，不可恢复）', icon: Trash, danger: true,
      onClick: () => void purge() },
  ];

  const ProfIcon = PROFILE_ICON[s.profile] ?? Bot;
  return (
    <div className={`session-row ${current ? 'current' : ''} ${editing ? 'editing' : ''} ${menuEl ? 'menu-open' : ''}`}
      title={s.project_title ? `${s.project_title} / ${s.title}（双击改名）` : `${s.title}（双击改名）`}
      onClick={editing ? undefined : onClick}
      onDoubleClick={e => { e.stopPropagation(); if (!archived) setEditing(true); }}>
      <span className="row-icon"><ProfIcon size={14} /></span>
      {editing
        ? <input className="row-edit" defaultValue={s.title} autoFocus maxLength={80}
            onClick={e => e.stopPropagation()}
            onKeyDown={e => {
              if (e.key === 'Enter') commit((e.target as HTMLInputElement).value);
              else if (e.key === 'Escape') setEditing(false);
            }}
            onBlur={e => commit(e.target.value)} />
        : <span className="row-title">{s.title}</span>}
      {!editing && !archived && s.pinned ? <Pin size={11} className="row-flag pin" /> : null}
      {!editing && !archived && s.starred ? <Star size={11} filled className="row-flag star" /> : null}
      {running && !editing && <span className="row-status run" title={s.active_turn!.status} />}
      {!editing && (
        <span className="row-ops">
          <button className="row-op" title="更多操作"
            onClick={e => { e.stopPropagation(); setMenuEl(e.currentTarget); }}>
            <MoreVertical size={14} />
          </button>
        </span>
      )}
      {menuEl && <PopupMenu anchor={menuEl} items={items} onClose={() => setMenuEl(null)} />}
    </div>
  );
}
