// 预览内联编辑器（任务1）：textarea 透明叠层语法高亮（零依赖 tokenizer，
// 见 lib/highlight.ts）+ 保存走 PUT /sessions/{sid}/file（UTF-8 ≤2MB 契约）。
// 编辑能力覆盖文本类（含 md）：与预览同一 tab 切换，保存后 nonce 刷新回预览。
import { useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { langOf, highlight } from '../lib/highlight';
import { Save, X } from './icons';
import { toast } from '../stores/toasts';

export default function FileEditor({ sid, path, initial, onSaved, onCancel }: {
  sid: string; path: string; initial: string;
  onSaved: () => void; onCancel: () => void;
}) {
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const hlRef = useRef<HTMLPreElement>(null);
  const name = path.split('/').pop() ?? path;
  const dot = name.lastIndexOf('.');
  const lang = useMemo(
    () => langOf(dot >= 0 ? name.slice(dot + 1) : ''), [name, dot]);
  const toks = useMemo(() => highlight(text, lang), [text, lang]);
  const dirty = text !== initial;

  const save = async () => {
    setBusy(true);
    try {
      await api(`/api/sessions/${encodeURIComponent(sid)}/file`,
        { method: 'PUT', body: JSON.stringify({ path, content: text }) });
      toast('已保存', true);
      onSaved();
    } catch (e) {
      toast(`保存失败：${String(e)}`, false);
      setBusy(false);
    }
  };

  const syncScroll = () => {
    if (hlRef.current && taRef.current) {
      hlRef.current.scrollTop = taRef.current.scrollTop;
      hlRef.current.scrollLeft = taRef.current.scrollLeft;
    }
  };

  return (
    <div className="fe-root">
      <div className="fe-bar">
        <span className="fe-lang">{lang}</span>
        {dirty && <span className="fe-dirty">● 未保存</span>}
        <span className="fe-sp" />
        <button className="btn primary sm" disabled={busy || !dirty} onClick={() => void save()}>
          <Save size={13} /> {busy ? '保存中…' : '保存'}
        </button>
        <button className="btn ghost sm" disabled={busy}
                onClick={dirty ? onCancel : onCancel} title="放弃修改回到预览">
          <X size={13} /> 关闭
        </button>
      </div>
      <div className="fe-wrap">
        <pre className="fe-hl" ref={hlRef} aria-hidden="true">
          {toks.map((t, i) => <span key={i} className={t.c}>{t.t}</span>)}
          {'\n'}
        </pre>
        <textarea className="fe-ta" ref={taRef} value={text} spellCheck={false}
                  onScroll={syncScroll}
                  onChange={e => setText(e.target.value)}
                  onKeyDown={e => {
                    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                      e.preventDefault();
                      if (dirty && !busy) void save();
                    }
                  }} />
      </div>
    </div>
  );
}
