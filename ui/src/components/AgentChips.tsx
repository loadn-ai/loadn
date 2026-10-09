// Composer 上方的 agent chips 条（设计稿「当前 Task 调用的 Agent」）：
// 当前 live turn 的子代理头像 chips（状态点 + 点击跳子任务标签页）+
// 「＋ 招募」按钮（往输入框注入派发模板——由主代理决定怎么拆）。
import { memo } from 'react';
import { useStore } from '../stores/sessions';
import { AgentAvatar } from './SubtaskTab';
import { Plus } from './icons';

/** 「新建子任务/招募」共用模板（tab 栏按钮与 chips 尾部 +） */
export const DISPATCH_TEMPLATE =
  '请派一个子代理完成以下子任务（用 Task 工具，给出 description 与 subagent_type）：\n' +
  '- 用途（general=通用 / explore=只读调研 / plan=架构设计）：\n' +
  '- 子任务描述（自包含：目标/边界/交付物路径）：\n';

function AgentChipsBase() {
  const agents = useStore(s => s.agents);
  const liveTurnId = useStore(s => s.live?.turnId ?? null);
  const openAgentView = useStore(s => s.openAgentView);
  const requestCompose = useStore(s => s.requestCompose);
  // chips 只显示当前（或最近）turn 的 agent——全 session 的在 tab 栏
  const focusTurn = liveTurnId
    ?? (agents.length ? agents[agents.length - 1].turnId : null);
  const shown = agents.filter(a => a.turnId === focusTurn);
  if (!shown.length) return null;
  return (
    <div className="agent-chips">
      <span className="agent-chips-label">当前 Task 调用的 Agent：</span>
      {shown.map(a => (
        <button key={a.key} className={`chip-agent ${a.status}`}
                title={`${a.name}${a.role ? ` · 负责：${a.role}` : ''}（点击看该 agent 视角）`}
                onClick={() => openAgentView(a.key)}>
          <AgentAvatar name={a.name} size={20} />
          <span className="chip-name">{a.name}</span>
          <span className={`chip-dot ${a.status}`} />
        </button>
      ))}
      <button className="chip-add" title="招募新子代理（往输入框注入派发模板）"
              onClick={() => requestCompose(DISPATCH_TEMPLATE)}>
        <Plus size={11} />
      </button>
    </div>
  );
}

export const AgentChips = memo(AgentChipsBase);
