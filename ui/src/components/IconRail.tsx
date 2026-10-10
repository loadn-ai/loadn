// 左侧 icon rail（60px 空间条）：品牌 + 内置空间（最近/收藏）+ 自定义分类
// （CategoryIcon 渲染，悬停 tooltip）+ 新建分类 + 归档；底部设置与用户。
// 空间选择驱动第二栏（Sidebar）的单桶渲染。
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useStore } from '../stores/sessions';
import type { CategoryInfo } from '../stores/sessions';
import CategoryIcon, { BUILTIN_ICONS } from './CategoryIcon';
import CategoryEditor from './CategoryEditor';
import PopupMenu from './Menu';
import type { MenuEntry } from './Menu';
import { Settings, Plus, Pencil, Trash, Archive, Star, Clock, Pin } from './icons';
import { api } from '../api/client';
import { askConfirm } from '../stores/confirm';

export default function IconRail({ onAdmin, adminActive }: {
  onAdmin: (tab?: 'cost') => void; adminActive?: boolean;
}) {
  const activeSpace = useStore(s => s.activeSpace);
  const setActiveSpace = useStore(s => s.setActiveSpace);
  const categories = useStore(s => s.categories);
  // 渲染纪律：selector 必须返回稳定引用（sessions.filter 每次新数组 =
  // useSyncExternalStore 无限重渲染，React #185）——选原数组，组件内过滤
  const sessions = useStore(s => s.sessions);
  const runningSessions = sessions.filter(
    x => x.status === 'active' && x.active_turn?.status === 'running');
  const [editor, setEditor] = useState<null | { mode: 'new' } | {
    mode: 'edit'; cat: CategoryInfo }>(null);
  const [menu, setMenu] = useState<{ cat: CategoryInfo; el: HTMLElement } | null>(null);

  const catRunning = (cid: number) => runningSessions.some(
    s => s.category_id === cid && (!s.project_id));

  const commitNew = async (name: string, icon: string | null) => {
    setEditor(null);
    const cat = await useStore.getState().createCategory(name, icon ?? undefined);
    if (cat) setActiveSpace(`cat:${cat.id}`);
  };
  const commitEdit = async (name: string, icon: string | null) => {
    if (!editor || editor.mode !== 'edit') return;
    const { cat } = editor;
    setEditor(null);
    await useStore.getState().updateCategory(cat.id, name, icon);
  };

  const catMenuItems = (cat: CategoryInfo): MenuEntry[] => [
    { key: 'edit', label: '编辑（改名/图标）', icon: Pencil,
      onClick: () => setEditor({ mode: 'edit', cat }) },
    { key: 'del', label: '删除空间（成员回「最近」）', icon: Trash, danger: true,
      onClick: async () => {
        if (activeSpace === `cat:${cat.id}`) setActiveSpace('recent');
        if (await askConfirm({ title: `删除分类「${cat.name}」？分类内的任务/项目回到「最近」，本身不受影响。`, danger: true }))
          void useStore.getState().deleteCategory(cat.id);
      } },
  ];

  return (
    <div className="icon-rail">
      <div className="rail-brand" title="loadn">
        <img src="/icons/apple-touch-icon.png" alt="loadn" />
      </div>
      <div className="rail-spaces">
        <RailBtn iconKey="recent" title="最近" Icon={BUILTIN_ICONS.recent ?? Clock}
          active={activeSpace === 'recent'} dot={runningSessions.some(
            s => !s.pinned && !s.starred && !s.category_id && !s.project_id)}
          onClick={() => setActiveSpace('recent')} />
        <RailBtn iconKey="pinned" title="置顶关注" Icon={Pin}
          active={activeSpace === 'pinned'}
          onClick={() => setActiveSpace('pinned')} />
        <RailBtn iconKey="starred" title="收藏" Icon={BUILTIN_ICONS.starred ?? Star}
          active={activeSpace === 'starred'}
          onClick={() => setActiveSpace('starred')} />
        <div className="rail-sep" />
        {categories.map(cat => (
          <RailBtn key={cat.id} iconKey={`cat-${cat.id}`} title={cat.name}
            icon={<CategoryIcon icon={cat.icon} size={16} />}
            active={activeSpace === `cat:${cat.id}`} dot={catRunning(cat.id)}
            onClick={() => setActiveSpace(`cat:${cat.id}`)}
            onContextMenu={e => {
              e.preventDefault();
              setMenu({ cat, el: e.currentTarget as HTMLElement });
            }} />
        ))}
        <button className={`rail-btn add ${editor?.mode === 'new' ? 'on' : ''}`}
                title="新建分类空间"
                onClick={() => setEditor(editor?.mode === 'new' ? null : { mode: 'new' })}>
          <Plus size={15} />
          <span className="rail-tip">新建分类空间</span>
        </button>
        <div className="rail-sep" />
        <RailBtn iconKey="archive" title="归档" Icon={BUILTIN_ICONS.archive ?? Archive}
          active={activeSpace === 'archive'}
          onClick={() => setActiveSpace('archive')} />
      </div>
      <div className="rail-foot">
        <button className={`rail-btn ${adminActive ? 'on' : ''}`} title="管理中心"
                onClick={() => onAdmin()}>
          <Settings size={16} />
          <span className="rail-tip">管理中心</span>
        </button>
        <UserBadge />
      </div>
      {editor?.mode === 'new' && (
        <div className="rail-editor">
          <CategoryEditor onCommit={(n, i) => void commitNew(n, i)} onCancel={() => setEditor(null)} />
        </div>
      )}
      {editor?.mode === 'edit' && (
        <div className="rail-editor">
          <CategoryEditor initialName={editor.cat.name} initialIcon={editor.cat.icon}
            onCommit={(n, i) => void commitEdit(n, i)} onCancel={() => setEditor(null)} />
        </div>
      )}
      {menu && (
        <PopupMenu anchor={menu.el} items={catMenuItems(menu.cat)}
                   onClose={() => setMenu(null)} />
      )}
    </div>
  );
}

function RailBtn({ iconKey, title, Icon, icon, active, dot, onClick, onContextMenu }: {
  iconKey: string; title: string;
  Icon?: typeof Clock; icon?: ReactNode;
  active: boolean; dot?: boolean;
  onClick: () => void; onContextMenu?: (e: React.MouseEvent) => void;
}) {
  return (
    <button key={iconKey} className={`rail-btn space ${active ? 'on' : ''}`}
            title={title} onClick={onClick} onContextMenu={onContextMenu}>
      {Icon ? <Icon size={16} /> : icon}
      {dot && <span className="rail-dot" />}
      <span className="rail-tip">{title}</span>
    </button>
  );
}

/** 用户徽标（自 Sidebar 挪入 rail 底部） */
function UserBadge() {
  const [me, setMe] = useState<{ username: string; role: string } | null>(null);
  useEffect(() => {
    void api<{ logged_in: boolean; user: { username: string; role: string } | null }>(
      '/api/auth/status')
      .then(d => setMe(d.logged_in ? d.user : null))
      .catch(() => setMe(null));
  }, []);
  if (!me) return null;
  const initial = me.username.slice(0, 1).toUpperCase();
  return (
    <button className="rail-user" title={`${me.username}${me.role === 'admin' ? ' · 管理员' : ''}（点击登出）`}
            onClick={async () => {
              if (!await askConfirm({ title: `退出登录 ${me.username}？`, danger: false })) return;
              await api('/api/auth/logout', { method: 'POST' });
              location.reload();
            }}>
      {initial}
    </button>
  );
}
