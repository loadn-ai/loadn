// 悬浮目录（markdown TOC 风）：替代顶部 tab 栏——子任务多时 tab 横向溢出
// 难看。目录列：主控 / 子任务（轮数+运行点）/ 打开的文件（可关）/
// ＋新建子任务。右侧悬浮、可折叠（宽屏默认展开，记忆在 localStorage）。
import { useEffect, useState } from 'react';
import { useStore } from '../stores/sessions';
import { DISPATCH_TEMPLATE } from './AgentChips';
import { Chat, FileDoc, Menu, Plus, X } from './icons';

interface FileTab { key: string; path?: string }

export default function SubtaskToc({ fileTabs, onCloseFile }: {
  fileTabs: FileTab[]; onCloseFile: (key: string) => void;
}) {
  const subtasks = useStore(s => s.subtasks);
  const turns = useStore(s => s.turns);
  const active = useStore(s => s.mainTab);
  const setActive = useStore(s => s.setMainTab);
  const requestCompose = useStore(s => s.requestCompose);

  const [open, setOpen] = useState(() => {
    const saved = localStorage.getItem('wd_toc_open');
    if (saved === '0' || saved === '1') return saved === '1';
    return typeof window !== 'undefined' && window.innerWidth > 900;
  });
  useEffect(() => {
    localStorage.setItem('wd_toc_open', open ? '1' : '0');
  }, [open]);

  const countOf = (id: number) =>
    turns.filter(t => t.subtask_id === id).length;
  const runningOf = (id: number) =>
    turns.some(t => t.subtask_id === id
      && (t.status === 'running' || t.status === 'queued'));
  const runningAny = turns.some(t =>
    t.status === 'running' || t.status === 'queued');

  const item = (key: string, label: React.ReactNode, title: string,
                opts?: { dot?: 'pulse' | 'err'; onClose?: () => void;
                         bold?: boolean }) => (
    <div key={key}
         className={`toc-item ${active === key ? 'on' : ''} ${opts?.bold ? 'bold' : ''}`}
         title={title} onClick={() => setActive(key)}>
      <span className="toc-label">{label}</span>
      {opts?.dot && <span className={`toc-dot ${opts.dot}`} />}
      {opts?.onClose && <span className="toc-x" title="关闭"
                               onClick={e => { e.stopPropagation(); opts.onClose!(); }}>
        <X size={10} /></span>}
    </div>
  );

  return (
    <div className={`subtask-toc ${open ? 'open' : ''}`}>
      <button className="toc-fab" title={open ? '收起目录' : '展开任务目录'}
              onClick={() => setOpen(o => !o)}>
        <Menu size={15} />
        {subtasks.length > 0 && <span className="toc-fab-n">{subtasks.length}</span>}
      </button>
      {open && (
        <div className="toc-panel">
          <div className="toc-head">
            <span>任务目录</span>
            <span className="muted" style={{ fontSize: 'var(--fs-xs)' }}>
              {subtasks.length} 个子任务
            </span>
          </div>
          <div className="toc-body">
            {item('chat', <><Chat size={12} /> 主控（全部合集）</>,
                  '所有内容的合集——对话/agent/产物都在这',
                  { dot: runningAny ? 'pulse' : undefined, bold: true })}
            {subtasks.map(st => item(
              `s:${st.id}`,
              <><span className="toc-sub-name">{st.title}</span>
                {countOf(st.id) > 0 && <span className="toc-n">{countOf(st.id)}</span>}</>,
              `${st.title}（${countOf(st.id)} 轮对话）`,
              { dot: runningOf(st.id) ? 'pulse' : undefined }))}
            {fileTabs.map(t => item(
              t.key,
              <><FileDoc size={12} /> {t.path!.split('/').pop()}</>,
              t.path!, { onClose: () => onCloseFile(t.key) }))}
            <div className="toc-item add"
                 title="新建子任务（往输入框注入派发模板，由主代理派发）"
                 onClick={() => { setActive('chat'); requestCompose(DISPATCH_TEMPLATE); }}>
              <span className="toc-label"><Plus size={12} /> 新建子任务</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
