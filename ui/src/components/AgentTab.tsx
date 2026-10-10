// agent 视角视图（按人名聚合）：一个名字 = 一个 agent 身份；引擎跨轮重名
// （每轮计数器从头取名），该名下的所有派出实例在此按「派遣」分组呈现——
// 每组=一张派发卡+该次的过程流水。产物按 agent_name 归并。
import { useStore } from '../stores/sessions';
import type { ArtifactInfo, ToolEvent, ToolItem } from '../stores/sessions';
import { SUB_TAG_RE, type AgentInfo } from '../stores/agents';
import { ToolCard } from './ChatStream';
import { AgentArtCard, AgentAvatar, DispatchCard } from './SubtaskTab';

/** 块/流水项是否属于该派出实例（新字段优先，旧数据回退 子N· 前缀） */
function belongsTo(tool: { id?: string | null; name?: string | null;
  agent_id?: string | null }, a: AgentInfo): boolean {
  if (tool.agent_id) return tool.agent_id === a.agentId;
  const m = SUB_TAG_RE.exec(tool.name ?? '');
  return !!m && Number(m[1]) === a.n;
}

interface HistTool { id: string | null; name: string; brief: string;
  input?: Record<string, unknown>; result?: string; is_error?: boolean;
  agent_id?: string | null }

/** 一次派出的完整轨迹（该 (turn, agent) 的 Task 卡 + 工具流水） */
interface Dispatch {
  agent: AgentInfo;
  card: HistTool | null;
  tools: HistTool[];
  liveTools: ToolItem[];
}

export default function AgentTab({ name, dispatches, onOpenFile }: {
  name: string; dispatches: AgentInfo[];
  onOpenFile?: (path: string) => void;
}) {
  const live = useStore(s => s.live);
  const messages = useStore(s => s.messages);
  const artifacts = useStore(s => s.artifacts);
  const turns = useStore(s => s.turns);
  const setMainTab = useStore(s => s.setMainTab);
  const setAgentFilter = useStore(s => s.setAgentFilter);

  const groups: Dispatch[] = [];
  for (const agent of dispatches) {
    const g: Dispatch = { agent, card: null, tools: [], liveTools: [] };
    for (const m of messages) {
      if (m.role !== 'assistant' || m.turn_id !== agent.turnId
          || !m.blocks_json) continue;
      let blocks: any[];
      try { blocks = JSON.parse(m.blocks_json); } catch { continue; }
      for (const b of blocks) {
        if (!b || b.type !== 'tool') continue;
        const t: HistTool = { id: b.id ?? null, name: String(b.name ?? '?'),
          brief: String(b.brief ?? ''), input: b.input, result: b.result,
          is_error: b.is_error, agent_id: b.agent_id };
        if (t.name === 'Task'
            && (t.agent_id ?? (t.id ?? '')).includes(agent.agentId)) {
          g.card = t;        // 该次派发的 Task 卡（含结束结果）
          continue;
        }
        if (belongsTo(t, agent)) g.tools.push(t);
      }
    }
    if (live && live.turnId === agent.turnId) {
      g.liveTools = live.items.filter((it): it is ToolItem =>
        it.kind === 'tool' && belongsTo(it, agent));
    }
    groups.push(g);
  }
  const running = dispatches.some(a => a.status === 'running');
  const allArts: ArtifactInfo[] = artifacts.filter(a => a.agent_name === name);
  const subIds = [...new Set(
    dispatches.map(a => turns.find(t => t.id === a.turnId)?.subtask_id)
      .filter((x): x is number => x != null))];

  return (
    <div className="agent-tab">
      <div className="agent-head">
        <AgentAvatar name={name} size={34} />
        <div className="agent-head-info">
          <div className="agent-head-name">
            {name}
            <span className={`agent-status ${running ? 'running' : 'done'}`}>
              {running ? '运行中…' : '已完成'}
            </span>
            <span className="muted" style={{ fontSize: 'var(--fs-sm)' }}>
              {dispatches.length} 次派出
            </span>
            {subIds.length === 1
              && <button className="link dispatch-open"
                         title="清除视角筛选并跳到所属的子任务标签页"
                         onClick={() => { setAgentFilter(null);
                                          setMainTab(`s:${subIds[0]}`); }}>
                   所属子任务 →
                 </button>}
          </div>
          <div className="agent-head-sub">
            {[...new Set(dispatches.map(a => a.role).filter(Boolean))]
              .slice(0, 4).map(r => <span key={r} className="agent-role">负责：{r}</span>)}
          </div>
        </div>
      </div>

      <div className="agent-stream">
        {groups.map(g => (
          <div key={g.agent.key} className="agent-dispatch-group">
            <div className="agent-dispatch-head">
              派遣 #{g.agent.turnId}:{g.agent.agentId}
              <span className={`agent-status ${g.agent.status}`}>
                {g.agent.status === 'running' ? '运行中…'
                  : g.agent.status === 'error' ? '未正常结束' : '已完成'}
              </span>
            </div>
            {g.card && <DispatchCard tool={g.card} turnId={g.agent.turnId} />}
            {g.liveTools.map((it, i) =>
              <ToolCard key={`lv${i}`} tool={it} />)}
            {g.tools.map((t, i) => (
              <ToolCard key={`hb${i}`} tool={t as ToolEvent}
                        done={t.result !== undefined || t.is_error !== undefined} />
            ))}
          </div>
        ))}
        {groups.length === 0 && (
          <div className="panel-empty">没有该 agent 的派出记录</div>
        )}
      </div>

      {allArts.length > 0 && (
        <div className="agent-arts">
          <div className="art-group-head">{name} 的产物（{allArts.length}）</div>
          {allArts.map(a => <AgentArtCard key={a.id} a={a} onOpenFile={onOpenFile} />)}
        </div>
      )}
    </div>
  );
}
