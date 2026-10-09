// agent 钻取视图（按需打开：点 chips/子任务里的 agent chip 进入，不自动
// 生成 tab——常驻 tab 是子任务，agent 是视角筛选拆面）。数据全派生：
// live items / blocks_json 按 agent 归属过滤 + 该 agent 的产物。
import { useStore } from '../stores/sessions';
import type { ToolEvent, ToolItem } from '../stores/sessions';
import { SUB_TAG_RE, type AgentInfo } from '../stores/agents';
import { ToolCard } from './ChatStream';
import { AgentArtCard, AgentAvatar, DispatchCard } from './SubtaskTab';

/** 块/流水项是否属于该 agent（新字段优先，旧数据回退 子N· 前缀） */
function belongsTo(tool: { id?: string | null; name?: string | null;
  agent_id?: string | null }, a: AgentInfo): boolean {
  if (tool.agent_id) return tool.agent_id === a.agentId;
  const m = SUB_TAG_RE.exec(tool.name ?? '');
  return !!m && Number(m[1]) === a.n;
}

interface HistTool { id: string | null; name: string; brief: string;
  input?: Record<string, unknown>; result?: string; is_error?: boolean;
  agent_id?: string | null }

export default function AgentTab({ agent, onOpenFile }: {
  agent: AgentInfo; onOpenFile?: (path: string) => void;
}) {
  const live = useStore(s => s.live);
  const messages = useStore(s => s.messages);
  const artifacts = useStore(s => s.artifacts);
  const subtaskId = useStore(s =>
    s.turns.find(t => t.id === agent.turnId)?.subtask_id ?? null);
  const setMainTab = useStore(s => s.setMainTab);

  // live：本 turn 正在跑的流水（回放是单一来源——重进时历史块走 messages 路）
  const liveItems = live && live.turnId === agent.turnId
    ? live.items.filter((it): it is ToolItem =>
        it.kind === 'tool' && belongsTo(it, agent)) : [];
  // 历史：本 turn 落库的 assistant 消息 blocks
  const histBlocks: HistTool[] = [];
  let dispatch: HistTool | null = null;
  for (const m of messages) {
    if (m.role !== 'assistant' || m.turn_id !== agent.turnId || !m.blocks_json) continue;
    let blocks: any[];
    try { blocks = JSON.parse(m.blocks_json); } catch { continue; }
    for (const b of blocks) {
      if (!b || b.type !== 'tool') continue;
      const t: HistTool = { id: b.id ?? null, name: String(b.name ?? '?'),
        brief: String(b.brief ?? ''), input: b.input, result: b.result,
        is_error: b.is_error, agent_id: b.agent_id };
      if (t.name === 'Task' && (t.agent_id ?? (t.id ?? '')).includes(agent.agentId)) {
        dispatch = t;      // 派发卡（含结束结果）
        continue;
      }
      if (belongsTo(t, agent)) histBlocks.push(t);
    }
  }
  const agentArts = artifacts.filter(
    a => a.agent_id === agent.agentId && a.turn_id === agent.turnId);
  const running = agent.status === 'running';

  return (
    <div className="agent-tab">
      <div className="agent-head">
        <AgentAvatar name={agent.name} size={34} />
        <div className="agent-head-info">
          <div className="agent-head-name">
            {agent.name}
            <span className={`agent-status ${agent.status}`}>
              {running ? '运行中…' : agent.status === 'error' ? '未正常结束' : '已完成'}
            </span>
            {subtaskId != null
              && <button className="link dispatch-open"
                         title="回到该 turn 所属的子任务标签页"
                         onClick={() => setMainTab(`s:${subtaskId}`)}>
                   所属子任务 →
                 </button>}
          </div>
          <div className="agent-head-sub">
            {agent.role && <span className="agent-role">负责：{agent.role}</span>}
            {agent.type && <span className="agent-type">{agent.type}</span>}
            {agent.lastTool && <span className="muted">最近：{agent.lastTool}</span>}
          </div>
        </div>
      </div>

      {dispatch && <DispatchCard agent={agent} tool={dispatch} turnId={agent.turnId} defaultOpen={!!dispatch.result} />}

      <div className="agent-stream">
        {liveItems.length > 0 && (
          <div className="agent-live">
            {liveItems.map((it, i) => <ToolCard key={`lv${i}`} tool={it} />)}
          </div>
        )}
        {histBlocks.length > 0 && histBlocks.map((t, i) => (
          <ToolCard key={`hb${i}`} tool={t as ToolEvent}
                    done={t.result !== undefined || t.is_error !== undefined} />
        ))}
        {liveItems.length === 0 && histBlocks.length === 0 && (
          <div className="panel-empty">{running ? '子代理已派出，过程事件尚未到达…' : '这个子代理没有留下过程记录'}</div>
        )}
      </div>

      {agentArts.length > 0 && (
        <div className="agent-arts">
          <div className="art-group-head">该 agent 的产物（{agentArts.length}）</div>
          {agentArts.map(a => <AgentArtCard key={a.id} a={a} onOpenFile={onOpenFile} />)}
        </div>
      )}
    </div>
  );
}
