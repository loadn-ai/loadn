import { useEffect, useRef, useState } from 'react';
import { useStore } from '../stores/sessions';
import { fmtTokens, fmtTime } from '../api/client';
import ChatStream from './ChatStream';
import Composer from './Composer';
import RightPanel from './RightPanel';
import FilePreview from './FilePreview';
import { Menu, Chat, FileDoc } from './icons';

interface MainTab { key: string; path?: string }   // key 'chat' 或 `f:<path>`

export default function SessionView({ onMenu }: { onMenu: () => void }) {
  // selector 订阅：delta 风暴下 currentSid 引用不变 → 不连带重渲染 ChatStream/
  // RightPanel/Composer 整个子树（它们各自字段级订阅）
  const currentSid = useStore(s => s.currentSid);
  // 桌面默认开右面板；窄屏它是遮盖式抽屉，默认收起（点「工作区」滑出）
  const [showPanel, setShowPanel] = useState(() => window.innerWidth > 900);
  const [tabs, setTabs] = useState<MainTab[]>([{ key: 'chat' }]);
  const [active, setActive] = useState('chat');
  const s = useStore(st => st.sessions.find(x => x.id === st.currentSid));
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
    if (active === key && next.length) setActive(next[Math.min(Math.max(0, i - 1), next.length - 1)].key);
  };

  const activeFile = tabs.find(t => t.key === active)?.path;

  return (
    <div className="session-view">
      <header className="session-head">
        <div className="head-left">
          <button className="menu-btn" title="任务列表" onClick={onMenu}><Menu size={18} /></button>
          <h2>{s.title}</h2>
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
          <button className="btn ghost" onClick={() => setShowPanel(v => !v)}>
            {showPanel ? '隐藏面板' : '工作区'}
          </button>
        </div>
      </header>
      <div className="session-body">
        <div className="chat-col">
          {tabs.length > 1 && (
            <div className="main-tabs">
              {tabs.map(t => (
                <div key={t.key} className={`mtab ${active === t.key ? 'on' : ''}`}
                  title={t.path ?? '对话'}
                  onClick={() => setActive(t.key)}>
                  <span className="mtab-name">
                    {t.key === 'chat'
                      ? <><Chat size={13} /> 对话</>
                      : <><FileDoc size={13} /> {t.path!.split('/').pop()}</>}
                  </span>
                  {t.key !== 'chat' &&
                    <span className="mtab-x" onClick={e => { e.stopPropagation(); closeTab(t.key); }}>×</span>}
                </div>
              ))}
            </div>
          )}
          {active === 'chat' || !activeFile
            ? <><ChatStream onOpenFile={openFile} /><Composer /></>
            : <FilePreview key={activeFile} sid={currentSid} path={activeFile} />}
        </div>
        {showPanel && <div className="scrim panel" onClick={() => setShowPanel(false)} />}
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
