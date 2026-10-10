import { useEffect, useRef, useState } from 'react';
import type { ClipboardEvent, ComponentType, DragEvent, KeyboardEvent } from 'react';
import { useStore } from '../stores/sessions';
import type { Attachment } from '../stores/sessions';
import { getDraft, setDraft } from '../stores/drafts';
import {
  Paperclip, Sparkles, Flask, Code, Chat, Bot, ChevronDown, Check,
  ArrowUp, StopSquare, FileDoc, Spinner, Terminal,
} from './icons';

const PROFILE_CN: Record<string, string> = {
  researcher: '研究', coder: '编程', assistant: '助手',
};
const PROFILE_DESC: Record<string, string> = {
  auto: '按消息内容自动匹配角色',
  researcher: '深度调研、检索与成文',
  coder: '写代码、跑任务、改工件',
  assistant: '日常问答与轻量杂务',
};
const PROFILE_ICON: Record<string, ComponentType<{ size?: number }>> = {
  auto: Sparkles, researcher: Flask, coder: Code, assistant: Chat,
};

interface Pending {
  key: string; file: File; preview: string;
  status: 'uploading' | 'done' | 'error';
  remote?: Attachment; err?: string;
}

let keySeq = 0;

/** 停止按钮目标 turn：live 优先（真在跑的），否则 sessions 行的 active_turn
 *  （running/queued——刷新后 live 重建前的空窗）。selector 返回原始值，
 *  delta 风暴下引用稳定不引发重渲染 */
function stopTargetTid(s: ReturnType<typeof useStore.getState>): number | null {
  if (s.live?.turnId != null) return s.live.turnId;
  const at = s.sessions.find(x => x.id === s.currentSid)?.active_turn;
  return at && (at.status === 'running' || at.status === 'queued') ? at.id : null;
}

/** claude CLI 风格输入框：一个圆角容器，chips 框内顶部、无边框输入区、
 *  底栏 [📎][模式] … [提示][发送] —— 附件与模式都是输入框的一部分，不突兀。 */
export default function Composer() {
  // 字段级订阅；live 只取两个原始值（busy/stopTid），delta 风暴下引用恒定不重渲染
  const steerOrSend = useStore(s => s.steerOrSend);
  const uploadFile = useStore(s => s.uploadFile);
  const currentSid = useStore(s => s.currentSid);
  const profiles = useStore(s => s.profiles);
  const sessions = useStore(s => s.sessions);
  const openSession = useStore(s => s.openSession);
  const engines = useStore(s => s.engines);
  const defaultEngine = useStore(s => s.defaultEngine);
  const busy = useStore(s => !!s.live);
  const [text, setText] = useState(() => (currentSid ? getDraft(currentSid) : ''));
  // 切会话换出各自的草稿（切文件 tab 是卸载重挂载，走上面 useState 初值，同源）
  useEffect(() => { setText(currentSid ? getDraft(currentSid) : ''); }, [currentSid]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [dragging, setDragging] = useState(false);
  // 运行中发送方式：插话（实时注入当前任务）或排队（当前任务结束后执行）。
  // 记住选择——临时想法切一下，常用姿势不用每次点
  const [queueMode, setQueueMode] = useState<'steer' | 'queue'>(
    () => localStorage.getItem('wd_send_mode') === 'queue' ? 'queue' : 'steer');
  const pickSendMode = (v: 'steer' | 'queue') => {
    setQueueMode(v);
    localStorage.setItem('wd_send_mode', v);
  };
  const fileInput = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const sess = sessions.find(x => x.id === currentSid);
  const mode = sess?.profile_auto ? 'auto' : (sess?.profile ?? 'auto');

  useEffect(() => () => { for (const p of pending) URL.revokeObjectURL(p.preview); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 输入区随内容长高（claude CLI 同款手感）
  useEffect(() => {
    const ta = taRef.current;
    if (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 240) + 'px'; }
  }, [text]);

  // 模板注入（「新建子任务/招募」按钮 → store.requestCompose）：seq 递增，
  // 同文本重复请求也能触发；追加到现有草稿尾部并聚焦
  useEffect(() => {
    let lastSeq = 0;
    return useStore.subscribe((s, prev) => {
      const req = s.composeReq;
      if (req && req.seq !== lastSeq && req.seq !== prev.composeReq?.seq) {
        lastSeq = req.seq;
        const cur = useStore.getState().currentSid;
        const base = cur ? getDraft(cur) : '';
        const merged = base ? `${base}\n${req.text}` : req.text;
        setText(merged);
        if (cur) setDraft(cur, merged);
        requestAnimationFrame(() => taRef.current?.focus());
      }
    });
  }, []);

  function addFiles(files: FileList | File[]) {
    const items: Pending[] = [];
    for (const f of Array.from(files)) {
      items.push({ key: `f${keySeq++}`, file: f, preview: URL.createObjectURL(f),
                   status: 'uploading' });
    }
    if (!items.length) return;
    setPending(ps => [...ps, ...items]);
    for (const it of items) {
      uploadFile(it.file)
        .then(remote => setPending(ps => ps.map(p =>
          p.key === it.key ? { ...p, status: 'done', remote } : p)))
        .catch(e => setPending(ps => ps.map(p =>
          p.key === it.key ? { ...p, status: 'error', err: String(e?.message ?? e) } : p)));
    }
  }

  function remove(key: string) {
    setPending(ps => {
      const p = ps.find(x => x.key === key);
      if (p) URL.revokeObjectURL(p.preview);
      return ps.filter(x => x.key !== key);
    });
  }

  async function pickMode(v: string) {
    if (!currentSid || v === mode) return;
    await fetch(`/api/sessions/${encodeURIComponent(currentSid)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: v }),
    });
    await openSession(currentSid);
  }

  // 内核切换：会话级覆盖，下一 turn 生效（引擎 id 迁移由后端对齐处理）
  const engineSel = sess?.engine_override ?? '';
  async function pickEngine(v: string) {
    if (!currentSid || v === engineSel) return;
    await fetch(`/api/sessions/${encodeURIComponent(currentSid)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine: v || null }),
    });
    await openSession(currentSid);
  }

  const modeItems = [
    { value: 'auto', label: '自动', desc: PROFILE_DESC.auto },
    ...profiles.map(p => ({ value: p.name, label: PROFILE_CN[p.name] ?? p.name, desc: PROFILE_DESC[p.name] })),
  ];

  function changeText(v: string) {
    setText(v);
    if (currentSid) setDraft(currentSid, v);
  }

  function submit() {
    if (!currentSid) return;
    const uploading = pending.some(p => p.status === 'uploading');
    const atts = pending.filter(p => p.status === 'done').map(p => p.remote!);
    const t = text.trim();
    if ((!t && !atts.length) || uploading) return;
    changeText('');
    for (const p of pending) URL.revokeObjectURL(p.preview);
    setPending([]);
    // 失败路径：store 已撤乐观消息并回填草稿，这里把文本同步回本地输入框。
    // steerOrSend：运行中的 loadn turn 且发送方式为「插话」时走插话通道
    // （下一轮 LLM 调用前实时注入）；「排队」或无运行任务/带附件 → 正常排队
    void steerOrSend(t || '（见附件）', 'foreground', atts,
                     { queue: queueMode === 'queue' })
      .catch(() => { if (currentSid && getDraft(currentSid)) setText(getDraft(currentSid)); });
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    if (e.clipboardData?.files?.length) {
      e.preventDefault();
      addFiles(e.clipboardData.files);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
  }

  const uploading = pending.some(p => p.status === 'uploading');
  const canSend = !!currentSid && (text.trim().length > 0 || pending.some(p => p.status === 'done')) && !uploading;
  // 停止按钮贴着发送：composer 永远在屏内（移动端头部按钮会被挤没、运行条会滚出视口）
  const stopTid = useStore(stopTargetTid);
  // 停止在途：POST 已受理但引擎未确认终态——按钮禁用转圈（防重复触发，
  // 终态确认/失败/30s 超时后恢复，见 stores/sessions.stopTurn）
  const stopBusy = useStore(s => {
    const t = stopTargetTid(s);
    return t != null && s.stoppingTids[t] != null;
  });

  return (
    <div className="composer-wrap">
      <div className={`cbox ${dragging ? 'dragging' : ''}`}
           onDragOver={e => { e.preventDefault(); setDragging(true); }}
           onDragLeave={e => { if (e.currentTarget === e.target) setDragging(false); }}
           onDrop={onDrop}>
        {pending.length > 0 && (
          <div className="attach-chips">
            {pending.map(p => (
              <div key={p.key} className={`attach-chip ${p.status}`}
                   title={p.status === 'error' ? p.err : `${p.file.name} ${Math.round(p.file.size / 1024)}KB`}>
                {p.file.type.startsWith('image/')
                  ? <img className="attach-thumb" src={p.preview} alt="" />
                  : <span className="attach-ico"><FileDoc size={17} /></span>}
                <span className="attach-info">
                  <span className="attach-name">{p.file.name}</span>
                  <span className="attach-st">
                    {p.status === 'uploading' ? <><Spinner /> 上传中…</>
                      : p.status === 'error' ? '上传失败'
                      : `${p.remote?.kb}KB`}
                  </span>
                </span>
                <button className="attach-x" title="移除" onClick={() => remove(p.key)}>×</button>
              </div>
            ))}
          </div>
        )}
        <textarea ref={taRef} className="cinput"
          value={text}
          onChange={e => changeText(e.target.value)}
          onKeyDown={onKey}
          onPaste={onPaste}
          placeholder={busy
            ? (queueMode === 'steer' ? '运行中，发送即插话' : '运行中，发送将排队')
            : '描述任务…（Enter 发送 · Shift+Enter 换行，可粘贴或拖入附件）'}
          rows={1}
        />
        <div className="cbar">
          <input ref={fileInput} type="file" multiple hidden
                 onChange={e => { addFiles(e.target.files ?? []); e.target.value = ''; }} />
          <button className="cbtn" title="上传附件" onClick={() => fileInput.current?.click()}>
            <Paperclip size={16} />
          </button>
          <ModeMenu mode={mode} items={modeItems} onPick={v => void pickMode(v)} />
          <EngineMenu sel={engineSel} engines={engines} defaultEngine={defaultEngine}
                     onPick={v => void pickEngine(v)} />
          {busy && !uploading && (
            <SendModeMenu value={queueMode} onPick={pickSendMode} />
          )}
          {uploading && <span className="cbar-hint">附件上传中…</span>}
          {stopTid != null && (
            <button className="stop-btn"
                    title={stopBusy ? '停止请求已发出，等待引擎确认…' : '停止当前任务'}
                    disabled={stopBusy}
                    onClick={() => { if (!stopBusy) void useStore.getState().stopTurn(stopTid); }}>
              {stopBusy ? <Spinner size={12} /> : <StopSquare size={12} />}
            </button>
          )}
          <button className="send" disabled={!canSend}
                  title={uploading ? '附件上传中' : '发送（Enter）'} onClick={submit}>
            <ArrowUp size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

/** 模式选择：自定义下拉（原生 select 弹出层不吃主题样式，替代之）。
 *  触发器 = 幽灵胶囊（图标 + 当前模式 + 箭头），向上弹出菜单，点外/Escape 关闭。 */
function ModeMenu({ mode, items, onPick }: {
  mode: string;
  items: { value: string; label: string; desc?: string }[];
  onPick: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const Icon = PROFILE_ICON[mode] ?? Bot;
  return (
    <div className="mode-menu" ref={root}>
      <button className={`mode-trigger ${open ? 'open' : ''}`}
              aria-haspopup="menu" aria-expanded={open}
              title="模式（下一轮生效；自动 = 按消息内容选角色）"
              onClick={() => setOpen(o => !o)}>
        <Icon size={16} />
        <span className="mode-trigger-label">{items.find(i => i.value === mode)?.label ?? mode}</span>
        <ChevronDown size={12} className="mode-caret" />
      </button>
      {open && (
        <div className="mode-pop" role="menu">
          {items.map(i => {
            const Ico = PROFILE_ICON[i.value] ?? Bot;
            return (
              <button key={i.value} role="menuitem" className={`mode-item ${i.value === mode ? 'sel' : ''}`}
                      onClick={() => { setOpen(false); onPick(i.value); }}>
                <span className="mode-item-ico"><Ico size={15} /></span>
                <span className="mode-item-text">
                  <span className="mode-item-label">{i.label}</span>
                  {i.desc && <span className="mode-item-desc">{i.desc}</span>}
                </span>
                <Check size={14} className="mode-ck" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** 内核选择：与 ModeMenu 同交互（幽灵胶囊 + 向上弹出）。当前生效内核 =
 *  会话级覆盖（engine_override）或全局默认；选「跟随默认」清空覆盖。 */
function EngineMenu({ sel, engines, defaultEngine, onPick }: {
  sel: string;
  engines: Record<string, { ok?: boolean; version?: string }>;
  defaultEngine: string;
  onPick: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const items = [
    { value: '', label: `跟随默认（${defaultEngine}）`,
      desc: '按配置的 profile / 全局默认引擎执行' },
    ...Object.keys(engines).map(name => ({
      value: name, label: name,
      desc: engines[name]?.ok
        ? (engines[name]?.version ?? '').slice(0, 60)
        : '（探测不可用）',
    })),
  ];
  return (
    <div className="mode-menu" ref={root}>
      <button className={`mode-trigger ${open ? 'open' : ''}`}
              aria-haspopup="menu" aria-expanded={open}
              title="执行内核（会话级切换，下一轮生效；各内核会话独立，切回自动续接）"
              onClick={() => setOpen(o => !o)}>
        <Terminal size={15} />
        <span className="mode-trigger-label">{sel || defaultEngine}</span>
        <ChevronDown size={12} className="mode-caret" />
      </button>
      {open && (
        <div className="mode-pop" role="menu">
          {items.map(i => (
            <button key={i.value} role="menuitem"
                    className={`mode-item ${i.value === sel ? 'sel' : ''}`}
                    onClick={() => { setOpen(false); onPick(i.value); }}>
              <span className="mode-item-ico">
                {i.value ? <Terminal size={15} /> : <Bot size={15} />}
              </span>
              <span className="mode-item-text">
                <span className="mode-item-label">{i.label}</span>
                {i.desc && <span className="mode-item-desc">{i.desc}</span>}
              </span>
              <Check size={14} className="mode-ck" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}


/** 运行中发送方式：插话（实时注入）/ 排队（当前任务后执行）。
 *  与 ModeMenu 同交互（幽灵胶囊 + 向上弹出），省底栏宽度；选择记忆在调用方。 */
function SendModeMenu({ value, onPick }: {
  value: 'steer' | 'queue'; onPick: (v: 'steer' | 'queue') => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const items = [
    { value: 'steer' as const, label: '插话' },
    { value: 'queue' as const, label: '排队' },
  ];
  return (
    <div className="mode-menu" ref={root}>
      <button type="button" className={`mode-trigger ${open ? 'open' : ''}`}
              aria-haspopup="menu" aria-expanded={open}
              title="运行中发送方式"
              onClick={() => setOpen(o => !o)}>
        {value === 'steer' ? '插话' : '排队'}
        <ChevronDown size={12} className="mode-caret" />
      </button>
      {open && (
        <div className="mode-pop" role="menu">
          {items.map(i => (
            <button key={i.value} type="button" role="menuitem"
                    className={`mode-item ${value === i.value ? 'sel' : ''}`}
                    onClick={() => { setOpen(false); onPick(i.value); }}>
              <span className="mode-item-text">
                <span className="mode-item-label">{i.label}</span>
              </span>
              <Check size={14} className="mode-ck" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
