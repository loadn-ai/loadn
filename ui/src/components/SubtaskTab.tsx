// 子任务标签页视图（rev10 概念模型：tab=子任务，agent 走 chips/派发卡）。
// 数据全派生自既有面：成员=turns.filter(subtask_id===X)；消息/agent/产物
// 经 turn join 过滤——零新端点。主控 tab 是所有内容的合集，这里是筛选视图。
import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useStore } from '../stores/sessions';
import type { ArtifactInfo, SubtaskInfo } from '../stores/sessions';
import { ToolCard, parseBlocks } from './ChatStream';
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

export default function SubtaskTab({ subtask, onOpenFile }: {
  subtask: SubtaskInfo; onOpenFile?: (path: string) => void;
}) {
  const turns = useStore(s => s.turns);
  const messages = useStore(s => s.messages);
  const artifacts = useStore(s => s.artifacts);
  const agents = useStore(s => s.agents);
  const live = useStore(s => s.live);

  const memberIds = new Set(
    turns.filter(t => t.subtask_id === subtask.id).map(t => t.id));
  const running = [...memberIds].some(id => {
    if (live && live.turnId === id && live.status === 'running') return true;
    const t = turns.find(x => x.id === id);
    return t?.status === 'running' || t?.status === 'queued';
  });
  const memberTurns = turns.filter(t => memberIds.has(t.id));
  const msgs = messages.filter(m => m.turn_id != null && memberIds.has(m.turn_id));
  const groupAgents = agents.filter(a => memberIds.has(a.turnId));
  const arts = artifacts.filter(a => a.turn_id != null && memberIds.has(a.turn_id));
  const liveHere = live && memberIds.has(live.turnId) ? live : null;

  return (
    <div className="agent-tab">
      <div className="agent-head">
        <div className="agent-head-info">
          <div className="agent-head-name">
            {subtask.title}
            <span className={`agent-status ${running ? 'running' : 'done'}`}>
              {running ? '运行中…' : '已收束'}
            </span>
            <span className="muted" style={{ fontSize: 11.5 }}>
              {memberTurns.length} 轮对话
            </span>
          </div>
          {groupAgents.length > 0 && (
            <div className="agent-head-sub">
              <span className="muted">调用过的 agent：</span>
              {groupAgents.map(a => (
                <span key={a.key} className="chip-agent static"
                      title={a.role ? `负责：${a.role}` : a.name}>
                  <AgentAvatar name={a.name} size={18} />
                  <span className="chip-name">{a.name}</span>
                  <span className={`chip-dot ${a.status}`} />
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {liveHere && (
        <div className={`turn-bar ${liveHere.status}`}>
          {liveHere.status === 'queued' ? '排队中…' : '运行中'}
        </div>
      )}

      <div className="agent-stream">
        {msgs.map(m => m.role === 'user'
          ? <div key={m.id} className="msg user"><div className="bubble">{m.content}</div></div>
          : <ReplayBlocks key={m.id} blocksJson={m.blocks_json} turnId={m.turn_id} />)}
        {liveHere && liveHere.items.map((it, i) => {
          if (it.kind === 'thinking')
            return <ThinkingLine key={i} text={it.text} />;
          if (it.kind === 'tool')
            return it.name === 'Task'
              ? <DispatchCard key={i} tool={it} turnId={liveHere.turnId} />
              : <ToolCard key={i} tool={it} />;
          if (it.kind === 'steer') return null;
          return <div key={i} className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{it.text}</ReactMarkdown></div>;
        })}
        {msgs.length === 0 && !liveHere && (
          <div className="panel-empty">这个子任务还没有落库内容</div>
        )}
      </div>

      {arts.length > 0 && (
        <div className="agent-arts">
          <div className="art-group-head">该子任务的产物（{arts.length}）</div>
          {arts.map(a => <AgentArtCard key={a.id} a={a} onOpenFile={onOpenFile} />)}
        </div>
      )}
    </div>
  );
}

/** 历史 assistant 消息的块时间线（Task→派发卡；tool→工具卡；text→md） */
function ReplayBlocks({ blocksJson, turnId }: {
  blocksJson?: string | null; turnId: number | null;
}) {
  const blocks = parseBlocks(blocksJson);
  if (!blocks.length) return null;
  return (
    <div className="replay subtask-replay">
      {blocks.map((b, i) => {
        if (b.kind === 'thinking') return <ThinkingLine key={i} text={b.text} />;
        if (b.kind === 'tool') {
          return b.name === 'Task'
            ? <DispatchCard key={i} tool={b} turnId={turnId} />
            : <ToolCard key={i} tool={b} done />;
        }
        return <div key={i} className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{b.text}</ReactMarkdown></div>;
      })}
    </div>
  );
}

/** 思考行（子任务视图内轻量折叠） */
function ThinkingLine({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`think-card ${open ? 'open' : ''}`}>
      <div className="think-head" onClick={() => setOpen(!open)}>
        <span className="think-preview">{text.slice(0, 60).replace(/\s+/g, ' ')}</span>
        <span className="chev">{open ? '−' : '+'}</span>
      </div>
      {open && <div className="think-body">{text}</div>}
    </div>
  );
}

/** 派发卡：Task 工具卡的拟人化渲染（prompt 摘要 + 结束结果）。
 *  「查看 →」跳该 turn 所属子任务 tab（无标签则不显示——agent 不再独占 tab）。 */
export const DispatchCard = memo(function DispatchCard({ agent, tool, turnId, defaultOpen = false }: {
  agent?: { name: string; role?: string; type?: string; status?: string };
  tool: { id?: string | null; name?: string | null; brief?: string;
    input?: Record<string, unknown>; result?: string; is_error?: boolean;
    agent_id?: string | null; agent_name?: string | null;
    agent_role?: string | null; subagent_type?: string | null };
  turnId?: number | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const subtaskId = useStore(s =>
    turnId != null ? (s.turns.find(t => t.id === turnId)?.subtask_id ?? null) : null);
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
        {subtaskId != null
          && <button className="link dispatch-open" title="查看子任务标签页"
                     onClick={e => { e.stopPropagation();
                       setMainTab(`s:${subtaskId}`); }}>查看 →</button>}
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
