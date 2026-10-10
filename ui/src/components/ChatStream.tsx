import { memo, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useStore } from '../stores/sessions';
import type { StreamItem, ToolEvent, ToolItem } from '../stores/sessions';
import { api, fmtTime } from '../api/client';
import {
  FileImage, Cloud, Tool, CheckSquare, Check, ChevronDown, ChevronRight,
  Terminal, Search, Globe, BookOpen, Pencil, PenLine, Plus, RotateCw, Star,
  Trash, Copy, Users,
} from './icons';
import type { ComponentType } from 'react';
import { useAutoScroll } from './SessionView';
import { DispatchCard } from './SubtaskTab';
import { askConfirm } from '../stores/confirm';
import { toast } from '../stores/toasts';

/** 历史消息尾窗：大会话百余条带完整过程块的消息全量渲染，打开要卡好几秒
 *  （2.7MB DOM 一次进页面）；默认只铺尾部，更早的按需「加载更早」展开 */
const HISTORY_TAIL = 40;

export default function ChatStream({ onOpenFile }: { onOpenFile?: (path: string) => void }) {
  const { messages, live, turns, retractTurn } = useStore();
  const ref = useAutoScroll(messages.length + (live?.items.length ?? 0));
  const [histLimit, setHistLimit] = useState(HISTORY_TAIL);
  const hiddenMsgs = Math.max(0, messages.length - histLimit);
  const shown = hiddenMsgs ? messages.slice(-histLimit) : messages;
  // 排队消息（turn 仍 queued）不插在聊天历史中间——聚到 live 之后、
  // 输入框上方的排队区，视觉上是「等当前任务跑完依次执行」的队列
  const queued = messages.filter(m =>
    !!m.turn_id && turns.some(t => t.id === m.turn_id && t.status === 'queued'));
  const queuedIds = new Set(queued.map(m => m.id));
  const hist = shown.filter(m => !queuedIds.has(m.id));

  return (
    <div className="chat-stream" ref={ref}>
      {hiddenMsgs > 0 && (
        <button className="load-earlier" onClick={() => setHistLimit(l => l + 100)}>
          ⋯ 加载更早消息（还有 {hiddenMsgs} 条）
        </button>
      )}
      {hist.map(m =>
        m.role === 'user'
          ? <UserMsg key={m.id} mid={m.id} text={m.content} blocks={m.blocks_json} ts={m.created_at}
                     onOpenFile={onOpenFile} />
          : <AssistantMsg key={m.id} mid={m.id} text={m.content} blocks={parseBlocks(m.blocks_json)} ts={m.created_at} hits={m.memory_hits_json} turnId={m.turn_id} />)}
      {live && <LiveTurn key={`live-${live.turnId}`} live={live} />}
      {queued.length > 0 && (
        <div className="queue-zone">
          <div className="queue-head">排队中 {queued.length}</div>
          {queued.map(m =>
            <UserMsg key={m.id} mid={m.id} text={m.content} blocks={m.blocks_json} ts={m.created_at}
                     onOpenFile={onOpenFile} retractable
                     onRetract={m.turn_id ? () => void retractTurn(m.turn_id!) : undefined} />)}
        </div>
      )}
    </div>
  );
}

/** blocks_json 里的附件条目（后端 {type:"attachment",path,name,kb,is_image}） */
function parseAttachments(json: string | null | undefined) {
  if (!json) return [];
  try {
    const raw = JSON.parse(json);
    return Array.isArray(raw) ? raw.filter(b => b?.type === 'attachment') : [];
  } catch { return []; }
}

/** blocks_json → 过程流水（穿插时间线：thinking/tool/text 按真实顺序；
 * 旧格式无 text 块 = 纯过程面板 + 合并正文，由调用方分形态渲染）。
 *  agent 归属字段透传（Task 卡/子N· 转发块，AgentTab/派发卡消费）。 */
export function parseBlocks(json: string | null | undefined): StreamItem[] {
  if (!json) return [];
  let raw: any[];
  try { raw = JSON.parse(json); } catch { return []; }
  if (!Array.isArray(raw)) return [];
  return raw.map(b => b?.type === 'thinking'
    ? { kind: 'thinking' as const, text: String(b.text ?? '') }
    : b?.type === 'text'
    ? { kind: 'text' as const, text: String(b.text ?? '') }
    : {
        kind: 'tool' as const,
        id: b?.id ?? null,
        name: String(b?.name ?? '?'),
        brief: String(b?.brief ?? ''),
        input: b?.input,
        result: b?.result,
        is_error: b?.is_error,
        agent_id: b?.agent_id ?? null,
        agent_name: b?.agent_name ?? null,
        agent_n: b?.agent_n ?? null,
        agent_role: b?.agent_role ?? null,
        subagent_type: b?.subagent_type ?? null,
      } as ToolItem);
}

/** Task 卡升级为拟人派发卡；其余走普通工具卡（含 子N· 归属徽标） */
function ToolLine({ b, done, turnId }: {
  b: ToolEvent; done?: boolean; turnId: number | null;
}) {
  if (b.name === 'Task') {
    return <DispatchCard tool={b} turnId={turnId} />;
  }
  return <ToolCard tool={b} done={done} />;
}

/** 气泡操作条：复制 / 编辑 / 删除（hover 浮现）。文本为空时全隐藏。 */
function BubbleActions({ text, mid, editing, onEdit, className }:
  { text: string; mid?: number; editing?: boolean;
    onEdit?: () => void; className?: string }) {
  const { deleteMessage } = useStore();
  const [copied, setCopied] = useState(false);
  if (!text) return null;
  const copy = () => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  };
  return (
    <div className={`bubble-actions ${className ?? ''}`}>
      <button title={copied ? '已复制' : '复制'} onClick={copy}>
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
      {mid != null && onEdit && (
        <button title="编辑" onClick={onEdit} disabled={editing}>
          <Pencil size={13} />
        </button>
      )}
      {mid != null && (
        <button title="删除" className="danger"
                onClick={async () => {
                  if (await askConfirm({ title: '删除这条气泡？（只删展示，不影响任务记录）', danger: true })) {
                    void deleteMessage(mid).catch(e => toast(`删除失败：${e instanceof Error ? e.message : e}`, false));
                  }
                }}>
          <Trash size={13} />
        </button>
      )}
    </div>
  );
}

/** 气泡内编辑态：textarea + 保存/取消 */
function EditBox({ initial, onSave, onCancel }:
  { initial: string; onSave: (text: string) => void; onCancel: () => void }) {
  const [val, setVal] = useState(initial);
  return (
    <div className="bubble-edit">
      <textarea value={val} onChange={e => setVal(e.target.value)} rows={Math.min(10, Math.max(2, val.split('\n').length + 1))} autoFocus />
      <div className="bubble-edit-ops">
        <button className="btn sm" onClick={() => val.trim() && onSave(val.trim())}>保存</button>
        <button className="btn ghost sm" onClick={onCancel}>取消</button>
      </div>
    </div>
  );
}

function UserMsg({ text, ts, blocks, onOpenFile, retractable, onRetract, mid }:
  { text: string; ts: string; blocks?: string | null; mid?: number;
    onOpenFile?: (path: string) => void;
    retractable?: boolean; onRetract?: () => void }) {
  const atts = parseAttachments(blocks);
  const { editMessage } = useStore();
  const [editing, setEditing] = useState(false);
  const save = async (t: string) => {
    setEditing(false);
    if (t === text) return;
    try { await editMessage(mid!, t); }
    catch (e) { toast(`保存失败：${e instanceof Error ? e.message : e}`, false); }
  };
  return (
    <div className="msg user">
      <div className="msg-user-row">
        {retractable && (
          <button className="retract-btn" title="撤回（还在排队，尚未执行）"
                  onClick={async () => { if (await askConfirm({ title: '撤回这条排队中的消息？', danger: false })) onRetract?.(); }}>×</button>
        )}
        {editing
          ? <EditBox initial={text} onSave={v => void save(v)} onCancel={() => setEditing(false)} />
          : <>
              {text && <div className="bubble">{text}</div>}
              {!retractable && (
                <BubbleActions text={text} mid={mid}
                               editing={editing} onEdit={() => setEditing(true)} />
              )}
            </>}
      </div>
      {atts.length > 0 && (
        <div className="attach-chips">
          {atts.map((a, i) => <AttachChip key={i} a={a} onOpenFile={onOpenFile} />)}
        </div>
      )}
      <div className="msg-meta">{retractable ? `${fmtTime(ts)} · 排队中` : fmtTime(ts)}</div>
    </div>
  );
}

/** 历史消息里的附件 chip：小图走文件接口取 base64，点击开预览 tab */
function AttachChip({ a, onOpenFile }: {
  a: { path: string; name?: string; kb?: number | null; is_image?: boolean };
  onOpenFile?: (path: string) => void;
}) {
  const sid = useStore(s => s.currentSid);
  const [b64, setB64] = useState<string | null>(null);
  const smallImg = !!a.is_image && (a.kb ?? 9999) < 2048;
  useEffect(() => {
    if (!smallImg || !sid) return;
    void api(`/api/sessions/${encodeURIComponent(sid)}/file?path=${encodeURIComponent(a.path)}`)
      .then(d => { if (d?.b64) setB64(`data:${d.mime};base64,${d.b64}`); })
      .catch(() => { /* 缩略图失败不致命 */ });
  }, [smallImg, sid, a.path]);
  return (
    <button className="attach-chip done" title={`${a.path}（点击预览）`}
            onClick={() => onOpenFile?.(a.path)}>
      {b64 ? <img className="attach-thumb" src={b64} alt="" />
           : <span className="attach-ico"><FileImage size={17} /></span>}
      <span className="attach-info">
        <span className="attach-name">{a.name ?? a.path.split('/').pop()}</span>
        {a.kb != null && <span className="attach-st">{a.kb}KB</span>}
      </span>
    </button>
  );
}

function AssistantMsg({ text, blocks, ts, mid, hits, turnId }:
  { text: string; blocks: StreamItem[]; ts: string; mid?: number;
    hits?: string | null; turnId?: number | null }) {
  const [open, setOpen] = useState(blocks.length <= 6);
  const { editMessage } = useStore();
  const [editing, setEditing] = useState(false);
  const shown = text && text !== '（无文本输出）' ? text : '';
  // 穿插回放：blocks 带正文段（新格式）→ 按真实顺序铺时间线（与 live 同构）；
  // 旧格式（无 text 块）→ 过程面板 + 合并正文，原样保留
  const interleaved = blocks.some(b => b.kind === 'text');
  const save = async (t: string) => {
    setEditing(false);
    if (t === shown) return;
    try { await editMessage(mid!, t); }
    catch (e) { toast(`保存失败：${e instanceof Error ? e.message : e}`, false); }
  };
  const parsedHits: { id: string; domain: string }[] = (() => {
    try { const h = JSON.parse(hits || '[]'); return Array.isArray(h) ? h : []; }
    catch { return []; }
  })();
  return (
    <div className="msg assistant">
      {parsedHits.length > 0 && <MemoryChips mid={mid} hits={parsedHits} />}
      {blocks.length > 0 && (interleaved ? (
        <div className="replay">
          {blocks.map((b, i) => {
            if (b.kind === 'thinking') return <ThinkingCard key={i} text={b.text} />;
            if (b.kind === 'tool') return <ToolLine key={i} b={b} done turnId={turnId ?? null} />;
            return <MdText key={i} text={b.text} />;
          })}
        </div>
      ) : (
        <div className="tools">
          <div className="tools-head" onClick={() => setOpen(!open)}>
            {blocks.some(b => b.kind === 'thinking') ? <Cloud size={13} /> : <Tool size={13} />}
            <span>{blocks.length} 步过程</span>
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </div>
          {open && blocks.map((b, i) => {
            if (b.kind === 'thinking') return <ThinkingCard key={i} text={b.text} />;
            if (b.kind === 'tool') return <ToolLine key={i} b={b} done turnId={turnId ?? null} />;
            return null;
          })}
        </div>
      ))}
      {editing
        ? <EditBox initial={shown} onSave={v => void save(v)} onCancel={() => setEditing(false)} />
        : <>
            {shown && !interleaved && (
              <div className="md-row">
                <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{shown}</ReactMarkdown></div>
                <BubbleActions text={shown} mid={mid}
                               editing={editing} onEdit={() => setEditing(true)} className="vertical" />
              </div>
            )}
          </>}
      {interleaved && !editing && shown && (
        <BubbleActions text={shown} mid={mid} onEdit={() => setEditing(true)} />
      )}
      <div className="msg-meta">{fmtTime(ts)}</div>
    </div>
  );
}

/** live 过程只渲染尾部窗口：长任务（深度研究动辄数百节点）全量渲染 +
 *  每 SSE 事件整列表重渲染会打满主线程（真机直接冻结），结束后完整过程在消息历史里 */
const LIVE_TAIL = 60;

function LiveTurn({ live }: { live: NonNullable<ReturnType<typeof useStore.getState>['live']> }) {
  const { stopTurn } = useStore();
  const elapsed = Math.round((Date.now() - live.startedAt) / 1000);
  const hidden = Math.max(0, live.items.length - LIVE_TAIL);
  const shown = hidden ? live.items.slice(-LIVE_TAIL) : live.items;
  return (
    <div className="msg assistant live">
      <div className={`turn-bar ${live.status}`}>
        {live.status === 'queued' ? '排队中…' : `运行中 ${elapsed}s`}
        {live.status === 'running' && (
          <button className="btn danger sm" onClick={() => void stopTurn(live.turnId)}>停止</button>
        )}
      </div>
      {live.todos.length > 0 && <TodoList todos={live.todos} />}
      {hidden > 0 && (
        <div className="tools-head">⋯ 前面 {hidden} 条过程已折叠（完整过程任务结束后可在消息历史回看）</div>
      )}
      {shown.map((it, i) => {
        // key 用绝对索引（hidden+i）：窗口滑动时已有项 key 不变，memo 才能跳过重渲染
        if (it.kind === 'thinking') return <ThinkingCard key={hidden + i} text={it.text} live />;
        if (it.kind === 'tool') return <ToolLine key={hidden + i} b={it} turnId={live.turnId} />;
        if (it.kind === 'steer') return <SteerNote key={hidden + i} text={it.text} />;
        return <MdText key={hidden + i} text={it.text} />;
      })}
      <span className="cursor">▍</span>
    </div>
  );
}

/** live 文本（markdown）：memo 让追加事件只渲染新增项，不重排整窗 */
const MdText = memo(function MdText({ text }: { text: string }) {
  return <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
});

/** 运行中插话（steering）：accent 边条卡片，与工具/文本流区分 */
const SteerNote = memo(function SteerNote({ text }: { text: string }) {
  return (
    <div className="steer-note">
      <span className="steer-tag">插话</span>
      <span className="steer-text">{text}</span>
    </div>
  );
});

export function TodoList({ todos }: { todos: { subject: string; status: string }[] }) {
  return (
    <div className="todos">
      <div className="todos-head"><CheckSquare size={13} /> 任务清单</div>
      {todos.map((t, i) => (
        <div key={i} className={`todo ${t.status}`}>
          <span className="todo-box">
            {t.status === 'completed' ? <Check size={11} />
              : t.status === 'in_progress' ? <Star size={11} filled /> : null}
          </span>
          <span className={t.status === 'completed' ? 'done' : ''}>{t.subject}</span>
        </div>
      ))}
    </div>
  );
}

const TOOL_ICON: Record<string, ComponentType<{ size?: number }>> = {
  Bash: Terminal, WebSearch: Search, WebFetch: Globe, Read: BookOpen,
  Write: Pencil, Edit: PenLine, TaskCreate: Plus, TaskUpdate: RotateCw,
  Glob: Search, Grep: Search, Task: Users,
};

/** 工具名前缀「子N·」→ 归属徽标 + 原工具名（设计稿 子1-Bash 形态） */
const SUB_PREFIX = /^子(\d+)·/;
function splitSubTag(name: string): { tag?: string; base: string } {
  const m = SUB_PREFIX.exec(name);
  return m ? { tag: `子${m[1]}`, base: name.slice(m[0].length) } : { base: name };
}

/** 思考卡：live 默认展开（看过程），历史默认收起（留一行预览）；memo 防整列表重渲染 */
export const ThinkingCard = memo(function ThinkingCard({ text, live }: { text: string; live?: boolean }) {
  const [open, setOpen] = useState(!!live);
  return (
    <div className={`think-card ${open ? 'open' : ''}`}>
      <div className="think-head" onClick={() => setOpen(!open)}>
        <span className="think-tag"><Cloud size={12} /> 思考</span>
        {!open && <span className="think-preview">{text.slice(0, 80).replace(/\s+/g, ' ')}</span>}
        <span className="chev">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
      </div>
      {open && <div className="think-body">{text}</div>}
    </div>
  );
});

export const ToolCard = memo(function ToolCard({ tool, done }: { tool: ToolEvent; done?: boolean }) {
  const [open, setOpen] = useState(false);
  const pending = !done && tool.is_error === undefined;
  const state = tool.is_error ? 'err' : pending ? 'pending' : 'ok';
  const input = tool.input ?? {};
  const inputKeys = Object.keys(input);
  const { tag, base } = splitSubTag(tool.name);
  const Ico = TOOL_ICON[base] ?? TOOL_ICON[tool.name] ?? Tool;
  return (
    <div className={`tool-card ${state} ${open ? 'open' : ''} ${tag ? 'sub' : ''}`}>
      <div className="tool-row" onClick={() => setOpen(!open)}>
        <span className="tool-icon"><Ico size={13} /></span>
        {tag && <span className="tool-subtag" title="子代理归属">{tag}</span>}
        <span className="tool-name">{base}</span>
        {tool.brief && <code className="tool-brief">{tool.brief}</code>}
        <span className="tool-state">{state === 'ok' ? '✓' : state === 'err' ? '✗' : '…'}</span>
      </div>
      {open && (
        <div className="tool-detail" onClick={e => e.stopPropagation()}>
          {inputKeys.length > 0 && (
            <div className="tool-kvs">
              {inputKeys.map(k => (
                <div key={k} className="tool-kv">
                  <span className="tool-k">{k}</span>
                  <pre className="tool-v">{String(input[k])}</pre>
                </div>
              ))}
            </div>
          )}
          {tool.result !== undefined && (
            <div className="tool-result-wrap">
              <div className="tool-result-tag">{tool.is_error ? '出错输出' : '输出'}</div>
              <pre className="tool-result">{tool.result || '（空）'}</pre>
            </div>
          )}
          {pending && tool.result === undefined && <div className="tool-result-tag muted">执行中…</div>}
        </div>
      )}
    </div>
  );
});


/** P8 来源 chips：本 turn 注入的记忆清单（域图标+id8）；点击弹层看全文/状态 */
function MemoryChips({ mid, hits }: { mid?: number; hits: { id: string; domain: string }[] }) {
  const [open, setOpen] = useState(false);
  const [src, setSrc] = useState<{ hits: { id: string; domain: string; reason?: string;
    status?: string; content?: string | null; origin_session?: string | null }[] } | null>(null);
  const sid = useStore(s => s.currentSid);
  const icon = (d: string) => (d === 'user' ? '🧠' : '📁');
  async function load() {
    setOpen(!open);
    if (!open && mid && sid && !src) {
      try { setSrc(await api(`/api/sessions/${sid}/messages/${mid}/sources`)); }
      catch { setSrc({ hits: [] }); }
    }
  }
  return (
    <div className="memory-chips" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 4 }}>
      {hits.map(h => (
        <button key={h.id} className="link" onClick={() => void load()}
          title={`记忆来源 ${h.domain}/${h.id}（点击看详情）`}
          style={{ fontSize: 'var(--fs-sm)', opacity: 0.8 }}>
          {icon(h.domain)} {h.id}
        </button>
      ))}
      {open && src && (
        <div className="hooks-aside panel" style={{ width: '100%', fontSize: 'var(--fs-md)', padding: 8 }}>
          {src.hits.map((h, i) => (
            <div key={i} style={{ borderTop: i ? '1px dashed var(--border)' : undefined, padding: '4px 0' }}>
              <b>{icon(h.domain)} {h.id}</b>
              <span style={{ opacity: 0.7 }}>
                {' '}· {h.status === 'present' ? '存在' : h.status === 'modified' ? '已修改' : '已删除'}
                {' '}· {(h.reason || '')}
                {h.origin_session ? ` · 源自 ${h.origin_session.slice(0, 18)}` : ''}
              </span>
              {h.content && <div className="muted" style={{ whiteSpace: 'pre-wrap' }}>
                {h.content.slice(0, 300)}</div>}
            </div>
          ))}
          <div style={{ marginTop: 4 }}>
            <button className="link" onClick={() => { location.hash = '#/admin/memory'; }}>
              去记忆页编辑 →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
