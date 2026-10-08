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
import AgentTab from './AgentTab';
import { AgentAvatar, } from './AgentTab';
import { AgentChips, DISPATCH_TEMPLATE } from './AgentChips';
import { Menu, Chat, FileDoc, Plus } from './icons';

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
  const agents = useStore(s => s.agents);
  const live = useStore(s => s.live);
  const requestCompose = useStore(s => s.requestCompose);
  const [tabs, setTabs] = useState<MainTab[]>([]);
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid));
  // agent tab 指向已被清理的 key（极旧 turn 等）→ 回主控。
  // （hook 必须在早退 return 之前——否则条件 hook 违反规则）
  useEffect(() => {
    const a = useStore.getState().mainTab;
    if (a.startsWith('a:') && !useStore.getState().agents.some(x => `a:${x.key}` === a)) {
      setActive('chat');
    }
  }, [agents, active]);
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
  const activeAgent = active.startsWith('a:')
    ? agents.find(a => a.key === active.slice(2)) : undefined;

  const agentRunning = (turnId: number) =>
    live?.turnId === turnId && live.status === 'running';

  return (
    <div className="session-view">
      <ApprovalBanner />
      <SkillSuggestCard />
      <header className="session-head">
        <div className="head-left">
          <button className="menu-btn" title="任务列表" onClick={onMenu}><Menu size={18} /></button>
          <div className="crumbs" title={`${s.project_title ?? '工作台'} / ${s.title}`}>
            <span className="crumb-space">{s.project_title ?? '工作台'}</span>
            <span className="crumb-sep">/</span>
            <span className="crumb-session">{s.title}</span>
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
          <div className="main-tabs">
            <div className={`mtab ${active === 'chat' ? 'on' : ''}`}
                 title="主控对话" onClick={() => setActive('chat')}>
              <span className="mtab-name"><Chat size={13} /> 主控与规划</span>
            </div>
            {agents.map(a => (
              <div key={a.key} className={`mtab agent ${active === `a:${a.key}` ? 'on' : ''}`}
                   title={`${a.name}${a.role ? ` · 负责：${a.role}` : ''}（子任务）`}
                   onClick={() => setActive(`a:${a.key}`)}>
                <span className="mtab-name">
                  <AgentAvatar name={a.name} size={16} />
                  <span className="mtab-agent-name">{a.name}</span>
                </span>
                {a.status === 'running' && agentRunning(a.turnId)
                  && <span className="mtab-dot pulse" />}
                {a.status === 'error' && <span className="mtab-dot err" />}
              </div>
            ))}
            {tabs.map(t => (
              <div key={t.key} className={`mtab ${active === t.key ? 'on' : ''}`}
                title={t.path ?? '对话'}
                onClick={() => setActive(t.key)}>
                <span className="mtab-name">
                  <FileDoc size={13} /> {t.path!.split('/').pop()}</span>
                <span className="mtab-x" onClick={e => { e.stopPropagation(); closeTab(t.key); }}>×</span>
              </div>
            ))}
            <button className="mtab add" title="新建子任务（往输入框注入派发模板，由主代理派发）"
                    onClick={() => { setActive('chat'); requestCompose(DISPATCH_TEMPLATE); }}>
              <Plus size={12} /> 新建子任务
            </button>
          </div>
          {active === 'chat' || !activeFile
            ? <>
                {activeAgent
                  ? <div className="agent-tab-scroll"><AgentTab agent={activeAgent} onOpenFile={openFile} /></div>
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
