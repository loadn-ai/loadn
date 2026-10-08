// 子任务标签页视图：单个子代理的执行流（live 过滤 + 历史回放过滤）+
// 派发卡（prompt/结果）+ 该 agent 的产物。数据全部派生自既有面
// （live items / blocks_json / artifacts 归属列），零新端点。
import { memo, useState } from 'react';
import { useStore } from '../stores/sessions';
import type { ArtifactInfo, ToolEvent, ToolItem } from '../stores/sessions';
import { SUB_TAG_RE, type AgentInfo } from '../stores/agents';
import { ToolCard } from './ChatStream';
import { Download, FileDoc } from './icons';
import { withToken } from '../api/client';

/** 按名着色的头像圈（name 首字）——chips/派发卡/tab 共用一套观感 */
const AVATAR_HUES = [258, 210, 160, 20, 330, 120, 30, 280];
export function agentHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return AVATAR_HUES[h % AVATAR_HUES.length];
}

export function AgentAvatar({ name, size = 24 }: { name: string; size?: number }) {
  const hue = agentHue(name);
  return (
    <span className="agent-avatar" style={{
      width: size, height: size, fontSize: size * 0.42,
      background: `hsl(${hue} 62% 88%)`, color: `hsl(${hue} 55% 34%)`,
    }}>{name.slice(0, 1)}</span>
  );
}

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
              {agent.status === 'running' ? '运行中…' : agent.status === 'error' ? '未正常结束' : '已完成'}
            </span>
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
          <div className="art-group-head">该子代理的产物（{agentArts.length}）</div>
          {agentArts.map(a => <AgentArtCard key={a.id} a={a} onOpenFile={onOpenFile} />)}
        </div>
      )}
    </div>
  );
}

/** 派发卡：Task 工具卡的拟人化渲染（prompt 摘要 + 结束结果） */
export const DispatchCard = memo(function DispatchCard({ agent, tool, turnId, defaultOpen = false }: {
  agent?: Pick<AgentInfo, 'name' | 'role' | 'type' | 'status'>;
  tool: { id?: string | null; name?: string | null; brief?: string;
    input?: Record<string, unknown>; result?: string; is_error?: boolean;
    agent_id?: string | null; agent_name?: string | null;
    agent_role?: string | null; subagent_type?: string | null };
  turnId?: number | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const setMainTab = useStore(s => s.setMainTab);
  const name = tool.agent_name ?? agent?.name ?? '子代理';
  const role = tool.agent_role ?? agent?.role ?? '';
  const type = tool.subagent_type ?? agent?.type ?? '';
  const prompt = String(tool.input?.prompt ?? tool.brief ?? '');
  const done = tool.result !== undefined || tool.is_error !== undefined;
  const err = !!tool.is_error;
  const status = err ? 'error' : done ? 'done' : 'running';
  return (
    <div className={`dispatch-card ${err ? 'err' : ''}`}>
      <div className="dispatch-row" onClick={() => setOpen(!open)}>
        <AgentAvatar name={name} size={26} />
        <div className="dispatch-mid">
          <span className="dispatch-name">{name}</span>
          {role && <span className="dispatch-role">负责：{role}</span>}
        </div>
        {type && <span className="agent-type">{type}</span>}
        <span className={`agent-status ${status}`}>
          {err ? '✗ 未正常结束' : done ? '✓ 已完成' : '运行中…'}
        </span>
        {tool.agent_id && tool.agent_id.startsWith('sub_')
          && <button className="link dispatch-open" title="查看子任务标签页"
                     onClick={e => { e.stopPropagation();
                       setMainTab(`a:${turnId ?? 0}:${tool.agent_id}`); }}>查看 →</button>}
      </div>
      {!open && prompt && <div className="dispatch-prompt">{prompt.slice(0, 120)}</div>}
      {open && (
        <div className="dispatch-detail">
          {prompt && <div className="tool-result-tag">派发指令</div>}
          {prompt && <pre className="tool-result">{prompt}</pre>}
          {tool.result !== undefined && (
            <>
              <div className={`tool-result-tag ${err ? 'danger' : ''}`}>{err ? '失败结果' : '交付结果'}</div>
              <pre className="tool-result">{tool.result || '（空）'}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
});

function AgentArtCard({ a, onOpenFile }: {
  a: ArtifactInfo; onOpenFile?: (path: string) => void;
}) {
  return (
    <div className="artifact-card">
      <div className="art-main">
        <span className={`kind k-${a.kind}`}>
          {a.kind === 'image' ? <FileDoc size={11} /> : a.kind}</span>
        <span className="art-title" title={a.path}>{a.title}</span>
        <span className="art-size">{Math.max(1, Math.round(a.size / 1024))}KB</span>
      </div>
      {a.summary && <div className="art-summary">{a.summary}</div>}
      <div className="art-actions">
        <button className="link" onClick={() => onOpenFile?.(a.path)}>预览</button>
        <a href={withToken(`/api/artifacts/${a.id}/download`)} download>
          <Download size={11} /> 下载
        </a>
      </div>
    </div>
  );
}
