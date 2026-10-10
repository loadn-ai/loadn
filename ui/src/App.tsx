import { useEffect, useState } from 'react';
import { useStore } from './stores/sessions';
import IconRail from './components/IconRail';
import Sidebar from './components/Sidebar';
import SessionView from './components/SessionView';
import AdminPanel from './components/AdminPanel';
import type { AdminTab } from './components/AdminPanel';
import Toasts from './components/Toasts';
import ConfirmDialog from './components/ConfirmDialog';   // BD-2：全局确认框（askConfirm 驱动）
import TokenGate from './components/TokenGate';
import { Menu, Plus, Flask, Code, FileDoc, RotateCw } from './components/icons';
import { toast } from './stores/toasts';

/** 极简 hash 路由：#/admin（#/admin/<tab> 指定标签，#/cost 为旧链兼容）→
 *  管理页；其余 → 会话/欢迎页（可刷新可收藏） */
function useHash(): [string, (h: string) => void] {
  const [hash, setHash] = useState(() => location.hash);
  useEffect(() => {
    const onHash = () => setHash(location.hash);
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  return [hash, (h: string) => { location.hash = h; }];
}

export default function App() {
  const { loadSessions, loadMeta, currentSid, createSession, openSession } = useStore();
  const theme = useStore(s => s.theme);
  const [hash, setHash] = useHash();
  const [stale, setStale] = useState(false);
  // #/cost 为旧深链兼容（等同 #/admin/cost）
  const admin = hash.startsWith('#/admin') || hash.startsWith('#/cost');
  const path = hash.split('?')[0];
  const adminTab: AdminTab | undefined =
    path.startsWith('#/admin/tasks') ? 'tasks'
    : path.startsWith('#/admin/skills') ? 'skills'
    : path.startsWith('#/admin/tools') ? 'tools'
    : path.startsWith('#/admin/settings') ? 'settings'
    : path.startsWith('#/admin/schedules') ? 'schedules'
    : path.startsWith('#/admin/memory') ? 'memory'
    : path.startsWith('#/admin/activity') ? 'activity'
    : path.startsWith('#/admin/webhooks') ? 'webhooks'
    : path.startsWith('#/admin/security') ? 'security'
    : path.startsWith('#/admin/resources') ? 'resources'
    // 'egress' 流量 tab 已并入安全 tab 出口卡（AC-2.3）——旧深链落到安全
    : path.startsWith('#/admin/egress') ? 'security'
    : (path === '#/admin/cost' || path === '#/cost') ? 'cost'
    : undefined;
  // #/admin/schedules?sid=xxx → 管理页定时 tab + 会话过滤（会话头徽章跳转目标）
  const filterSid = adminTab === 'schedules'
    ? new URLSearchParams(hash.split('?')[1] ?? '').get('sid') ?? undefined
    : undefined;
  const [navOpen, setNavOpen] = useState(false);   // 移动端侧栏抽屉（桌面常显不受影响）

  useEffect(() => {
    void loadSessions();
    void loadMeta();
    const t = setInterval(() => void loadSessions(), 5000);
    return () => clearInterval(t);
  }, []);

  // 主题同步：<html data-theme> + PWA 状态栏颜色（meta theme-color）
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', theme === 'light' ? '#f8fafc' : '#16181d');
  }, [theme]);

  // 刷新/重开恢复上次会话（PWA 点 Dock 直达 + 任务跑着时刷新不失联）
  useEffect(() => {
    const last = localStorage.getItem('loadn_sid') ?? localStorage.getItem('wd_sid');
    if (!last) return;
    void (async () => {
      const st = useStore.getState();
      if (!st.sessions.length) await st.loadSessions();
      if (useStore.getState().currentSid) return;
      if (useStore.getState().sessions.some(s => s.id === last))
        await useStore.getState().openSession(last);
    })();
  }, []);

  // 前台恢复即拉新：iOS PWA 后台冻结定时器与 SSE 重连退避，回前台不等
  // 5s 轮询周期——立即 resync（列表 + 当前会话 + SSE 死线重建），否则
  // 界面停在后台前的最后一帧（"一直运行中"假象）
  useEffect(() => {
    const wake = () => {
      if (document.visibilityState !== 'visible') return;
      void useStore.getState().resync();
    };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('focus', wake);
    return () => {
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('focus', wake);
    };
  }, []);

  // 版本巡检：正在跑的 bundle hash vs 线上 index.html 引用的 hash——
  // 部署后还开着的旧标签页继续跑旧代码（看着像"修复没生效"），30s 对比一次，
  // 变了就挂出刷新横幅
  useEffect(() => {
    const loaded = document.querySelector('script[src*="/assets/index-"]')
      ?.getAttribute('src')?.match(/index-([A-Za-z0-9_-]+)\.js/)?.[1];
    if (!loaded) return;
    const check = async () => {
      try {
        const html = await (await fetch('/', { cache: 'no-store' })).text();
        const cur = html.match(/index-([A-Za-z0-9_-]+)\.js/)?.[1];
        if (cur && cur !== loaded) setStale(true);
      } catch { /* 网络抖动忽略 */ }
    };
    void check();
    const t = setInterval(() => void check(), 30000);
    return () => clearInterval(t);
  }, []);

  // 零选择新建：一键建会话（标题/角色/skills 全自动），进去直接打字
  const quickNew = async () => {
    setNavOpen(false);
    try {
      const s = await createSession({});
      await openSession(s.id);
    } catch (e) {
      toast(String(e), false);
    }
  };

  return (
    <div className={`app ${navOpen ? 'nav-open' : ''}`}>
      <TokenGate />
      <Toasts />
      <ConfirmDialog />
      {stale && (
        <button className="stale-pill" title="平台已更新，当前页面还在跑旧版本"
          onClick={() => location.reload()}>
          <RotateCw size={13} /> 平台已更新 · 点击刷新
        </button>
      )}
      {navOpen && <div className="scrim nav" onClick={() => setNavOpen(false)} />}
      <div className="nav-col">
        <IconRail
          onAdmin={tab => {
            setNavOpen(false);
            // AC-3.2：统一语义——已在管理页（任意子 tab）点击=退出；不在=进入（tab 参数仅入口快捷）
            setHash(admin ? '' : (tab === 'cost' ? '#/admin/cost' : '#/admin'));
          }}
          adminActive={admin} />
        <Sidebar
          onNew={() => void quickNew()}
          onAdmin={tab => {
            setNavOpen(false);
            setHash(admin ? '' : (tab === 'cost' ? '#/admin/cost' : '#/admin'));
          }}
          onNav={() => setNavOpen(false)}
          adminActive={admin} />
      </div>
      <main className="main">
        {admin
          ? <AdminPanel onClose={() => { setHash(''); void loadMeta(); }}
              initialTab={adminTab} filterSid={filterSid}
              onClearFilter={() => setHash('#/admin/schedules')} />
          : currentSid
            ? <SessionView onMenu={() => setNavOpen(true)} />
            : <Welcome onNew={() => void quickNew()} onMenu={() => setNavOpen(true)} />}
      </main>
    </div>
  );
}

function Welcome({ onNew, onMenu }: { onNew: () => void; onMenu: () => void }) {
  return (
    <div className="welcome">
      <button className="menu-btn welcome-menu" title="任务列表" onClick={onMenu}><Menu size={18} /></button>
      <div className="welcome-card">
        <img className="welcome-logo" src="/icons/apple-touch-icon.png" alt="loadn" />
        <h1>loadn</h1>
        <p>多 agent 任务平台 · 每个对话一个独立工作区 · 底层 Claude Code</p>
        <button className="btn primary" onClick={onNew}><Plus size={15} /> 新建任务</button>
        <div className="welcome-hints">
          <span><Flask size={14} /> 深度研究（多轮搜索 + 引用审计）</span>
          <span><Code size={14} /> 编程分析（独立工作区写码执行）</span>
          <span><FileDoc size={14} /> 产物导出（md / html / docx）</span>
        </div>
      </div>
    </div>
  );
}
