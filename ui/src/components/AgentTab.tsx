// agent 视角视图（按人名聚合）：一个名字 = 一个 agent 身份；引擎跨轮重名
// （每轮计数器从头取名），该名下的所有派出实例在此按「派遣」分组呈现——
// 每组=一张派发卡+该次的过程流水。产物按 agent_name 归并。
import { useStore } from '../stores/sessions';
import type { ArtifactInfo, TextItem, ThinkItem, ToolEvent, ToolItem } from '../stores/sessions';
import { SUB_TAG_RE, type AgentInfo } from '../stores/agents';
import { SubTextCard, ThinkingCard, ToolCard } from './ChatStream';
import { AgentArtCard, AgentAvatar, DispatchCard } from './SubtaskTab';

/** 块/流水项是否属于该派出实例（新字段优先，旧数据回退 子N· 前缀） */
function belongsTo(tool: { id?: string | null; name?: string | null;
  agent_id?: string | null }, a: AgentInfo): boolean {
  if (tool.agent_id) return tool.agent_id === a.agentId;
  const m = SUB_TAG_RE.exec(tool.name ?? '');
  return !!m && Number(m[1]) === a.n;
}

/** 归属判定（子代理思考聚合块/文字块：agent_id/agent_n，无 name 前缀可回退） */
function thinkBelongsTo(b: { agent_id?: string | null; agent_n?: number | null },
                        a: AgentInfo): boolean {
  if (b.agent_id) return b.agent_id === a.agentId;
  return b.agent_n != null && b.agent_n === a.n;
}

interface HistTool { id: string | null; name: string; brief: string;
  input?: Record<string, unknown>; result?: string; is_error?: boolean;
  agent_id?: string | null }

/** 派遣组时间线条目：工具 / 思考 / 子代理文字（按 blocks 真实顺序穿插） */
type HistEntry =
  | { kind: 'tool'; t: HistTool }
  | { kind: 'think'; text: string; agent_n: number | null }
  | { kind: 'text'; text: string; agent_n: number | null; agent_name: string | null };
type LiveEntry =
  | { kind: 'tool'; it: ToolItem }
  | { kind: 'think'; it: ThinkItem }
  | { kind: 'text'; it: TextItem };

/** 一次派出的完整轨迹（该 (turn, agent) 的 Task 卡 + 工具/思考流水） */
interface Dispatch {
  agent: AgentInfo;
  card: HistTool | null;
  flow: HistEntry[];
  liveFlow: LiveEntry[];
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
    const g: Dispatch = { agent, card: null, flow: [], liveFlow: [] };
    for (const m of messages) {
      if (m.role !== 'assistant' || m.turn_id !== agent.turnId
          || !m.blocks_json) continue;
      let blocks: any[];
      try { blocks = JSON.parse(m.blocks_json); } catch { continue; }
      for (const b of blocks) {
        if (!b) continue;
        if (b.type === 'thinking') {
          // 子代理思考聚合块（带归属）→ 视角页思考时间线；主控思考不带
          // agent 字段不属于任何 agent，天然排除
          if (b.agent_id != null || b.agent_n != null) {
            if (thinkBelongsTo(b, agent)) {
              g.flow.push({ kind: 'think', text: String(b.text ?? ''),
                agent_n: b.agent_n ?? null });
            }
          }
          continue;
        }
        if (b.type === 'text') {
          // 子代理文字块（带归属）→ 视角页文字卡；主控正文无 agent 字段排除
          if (b.agent_id != null || b.agent_n != null) {
            if (thinkBelongsTo(b, agent)) {
              g.flow.push({ kind: 'text', text: String(b.text ?? ''),
                agent_n: b.agent_n ?? null, agent_name: b.agent_name ?? null });
            }
          }
          continue;
        }
        if (b.type !== 'tool') continue;
        const t: HistTool = { id: b.id ?? null, name: String(b.name ?? '?'),
          brief: String(b.brief ?? ''), input: b.input, result: b.result,
          is_error: b.is_error, agent_id: b.agent_id };
        if (t.name === 'Task'
            && (t.agent_id ?? (t.id ?? '')).includes(agent.agentId)) {
          g.card = t;        // 该次派发的 Task 卡（含结束结果）
          continue;
        }
        if (belongsTo(t, agent)) g.flow.push({ kind: 'tool', t });
      }
    }
    if (live && live.turnId === agent.turnId) {
      g.liveFlow = live.items.flatMap((it): LiveEntry[] => {
        if (it.kind === 'tool' && belongsTo(it, agent)) return [{ kind: 'tool', it }];
        if (it.kind === 'thinking' && (it as ThinkItem).agent_n != null
            && thinkBelongsTo(it, agent)) return [{ kind: 'think', it }];
        if (it.kind === 'text' && it.agent_n != null
            && thinkBelongsTo(it, agent)) return [{ kind: 'text', it }];
        return [];
      });
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
            {g.liveFlow.map((e, i) => e.kind === 'tool'
              ? <ToolCard key={`lv${i}`} tool={e.it} />
              : e.kind === 'text'
              ? <SubTextCard key={`lv${i}`} text={e.it.text}
                  tag={e.it.agent_n != null ? `子${e.it.agent_n}` : undefined}
                  name={e.it.agent_name} live />
              : <ThinkingCard key={`lv${i}`} text={e.it.text} live
                  tag={e.it.agent_n != null ? `子${e.it.agent_n}` : undefined}
                  cum={e.it.cum} />)}
            {g.flow.map((e, i) => e.kind === 'tool'
              ? <ToolCard key={`hb${i}`} tool={e.t as ToolEvent}
                  done={e.t.result !== undefined || e.t.is_error !== undefined} />
              : e.kind === 'text'
              ? <SubTextCard key={`hb${i}`} text={e.text}
                  tag={e.agent_n != null ? `子${e.agent_n}` : undefined}
                  name={e.agent_name} />
              : <ThinkingCard key={`hb${i}`} text={e.text}
                  tag={e.agent_n != null ? `子${e.agent_n}` : undefined} />)}
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
