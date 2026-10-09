import { useEffect, useRef, useState } from 'react';
import ApprovalBanner from './ApprovalBanner';
import SkillSuggestCard from './SkillSuggestCard';
import CompactTimeline from './CompactTimeline';
import { useStore } from '../stores/sessions';
import { fmtTokens, fmtTime } from '../api/client';
import ChatStream from './ChatStream';
import Composer from './Composer';
import RightPanel from './RightPanel';
import FilePreview from './FilePreview';
import SubtaskTab from './SubtaskTab';
import AgentTab from './AgentTab';
import { AgentAvatar } from './SubtaskTab';
import { Menu, X } from './icons';
import { AgentChips } from './AgentChips';
import SubtaskToc from './SubtaskToc';

interface MainTab { key: string; path?: string }   // key 'chat' 或 `f:<path>`

export default function SessionView({ onMenu }: { onMenu: () => void }) {
  // selector 订阅：delta 风暴下 currentSid 引用不变 → 不连带重渲染 ChatStream/
  // RightPanel/Composer 整个子树（它们各自字段级订阅）
  const currentSid = useStore(s => s.currentSid);
  // 面板开合在 store（桌面默认开；窄屏遮盖式抽屉默认收起）——侧边栏
  // 「属性」入口要能从外部强开（移动端抽屉同路滑出）
  const showPanel = useStore(s => s.panelOpen);
  const setPanelOpen = useStore(s => s.setPanelOpen);
  // 主区 tab 活动态在 store（chips/派发卡要能从外部跳过来）；文件 tab 开合
  // 仍是本地态（agent tab 派生自 agents，不落本地）
  const active = useStore(s => s.mainTab);
  const setActive = useStore(s => s.setMainTab);
  const subtasks = useStore(s => s.subtasks);
  const agents = useStore(s => s.agents);
  const agentFilter = useStore(s => s.agentFilter);
  const setAgentFilter = useStore(s => s.setAgentFilter);
  const [tabs, setTabs] = useState<MainTab[]>([]);
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid));
  // 子任务 tab 指向已不存在的 id（会话切换残留等）→ 回主控。
  // （hook 必须在早退 return 之前——否则条件 hook 违反规则）
  useEffect(() => {
    const a = useStore.getState().mainTab;
    const st = useStore.getState();
    if (a.startsWith('s:') && !st.subtasks.some(x => `s:${x.id}` === a)) {
      setActive('chat');
    }
  }, [subtasks, active]);
  if (!currentSid || !s) return null;

  const wake = s.next_wake;
  const wakeText = wake ? (() => {
    try {
      const d = new Date(wake.due_at);
      const now = Date.now();
      const diffMin = Math.round((d.getTime() - now) / 60000);
      const hm = d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      return diffMin >= 0 && diffMin < 24 * 60 ? `${diffMin} 分钟后唤醒` : hm;
    } catch { return wake.due_at; }
  })() : '';

  // 主区开文件预览 tab（同文件去重复用）
  const openFile = (path: string) => {
    const key = `f:${path}`;
    setTabs(ts => ts.some(t => t.key === key) ? ts : [...ts, { key, path }]);
    setActive(key);
  };
  const closeTab = (key: string) => {
    const i = tabs.findIndex(t => t.key === key);
    const next = tabs.filter(t => t.key !== key);
    setTabs(next);
    if (active === key) {
      setActive(next.length
        ? next[Math.min(Math.max(0, i - 1), next.length - 1)].key : 'chat');
    }
  };

  const activeFile = tabs.find(t => t.key === active)?.path;
  const activeSubtask = active.startsWith('s:')
    ? subtasks.find(x => x.id === Number(active.slice(2))) : undefined;
  // agent 视角筛选（按人名聚合，非 tab）：挂上时内容区切该名字的聚合视图
  const filterDispatches = agentFilter
    ? agents.filter(x => x.name === agentFilter) : [];
  // 面包屑尾段：当前视图名（tab 栏已移除，头部承载位置感）
  const viewName = filterDispatches.length > 0
    ? `视角：${agentFilter}`
    : activeSubtask?.title
    ?? (activeFile ? activeFile.split('/').pop() : '');

  return (
    <div className="session-view">
      <ApprovalBanner />
      <SkillSuggestCard />
      <header className="session-head">
        <div className="head-left">
          <button className="menu-btn" title="任务列表" onClick={onMenu}><Menu size={18} /></button>
          <div className="crumbs"
               title={`${s.project_title ?? '工作台'} / ${s.title}${viewName ? ` / ${viewName}` : ''}`}>
            <span className="crumb-space">{s.project_title ?? '工作台'}</span>
            <span className="crumb-sep">/</span>
            <span className="crumb-session">{s.title}</span>
            {viewName && <>
              <span className="crumb-sep">/</span>
              <span className="crumb-view">{viewName}</span>
            </>}
          </div>
          {wake && (
            <button className="badge" title={`定时唤醒：${wake.label ?? '（无标签）'} · ${wake.due_at}${wake.cron ? ` · ${wake.cron}` : wake.every_s ? ` · 递归 ${Math.round(wake.every_s / 60)}min ×${wake.max_fires}` : ' · 单次'} · 点击管理`}
                    onClick={() => { location.hash = `#/admin/schedules?sid=${s.id}`; }}>
              {wakeText}
            </button>
          )}
        </div>
        <div className="head-right">
          <span className="usage" title="累计 tokens / 成本">
            {fmtTokens(s.usage?.total)} tok · ${(s.usage?.cost_usd ?? 0).toFixed(2)}
          </span>
          <span className={`conn ${useStore.getState().connected ? 'ok' : ''}`} title="SSE 连接状态" />
          <button className="btn ghost" onClick={() => setPanelOpen(!showPanel)}>
            {showPanel ? '隐藏面板' : '工作区'}
          </button>
        </div>
      </header>
      <div className="session-body">
        <div className="chat-col">
          <CompactTimeline />
          <SubtaskToc fileTabs={tabs} onCloseFile={closeTab} />
          {agentFilter && filterDispatches.length > 0 && (
            <div className="agent-filter-bar">
              <span className="muted">视角筛选</span>
              <span className="chip-agent static">
                <AgentAvatar name={agentFilter} size={18} />
                <span className="chip-name">{agentFilter}</span>
                <span className={`chip-dot ${filterDispatches.some(a => a.status === 'running')
                  ? 'running' : 'done'}`} />
              </span>
              <span className="muted" style={{ flex: 1 }}>
                只看该 agent 的过程与产物
              </span>
              <button className="btn ghost sm" onClick={() => setAgentFilter(null)}>
                <X size={12} /> 清除视角
              </button>
            </div>
          )}
          {active === 'chat' || !activeFile
            ? <>
                {filterDispatches.length > 0
                  ? <div className="agent-tab-scroll"><AgentTab name={agentFilter!} dispatches={filterDispatches} onOpenFile={openFile} /></div>
                  : activeSubtask
                    ? <div className="agent-tab-scroll"><SubtaskTab subtask={activeSubtask} onOpenFile={openFile} /></div>
                    : <ChatStream onOpenFile={openFile} />}
                <AgentChips />
                <Composer />
              </>
            : <FilePreview key={activeFile} sid={currentSid} path={activeFile} />}
        </div>
        {showPanel && <div className="scrim panel" onClick={() => setPanelOpen(false)} />}
        {showPanel && <RightPanel onOpenFile={openFile} />}
      </div>
    </div>
  );
}

export function useAutoScroll(dep: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [dep]);
  return ref;
}

export { fmtTime };
