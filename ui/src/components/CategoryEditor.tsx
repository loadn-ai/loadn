// 分类编辑器：名字 + 图标网格（新建/改名共用）。内联面板形态（rail 新建、
// 分类菜单改名都弹这个），确认回调把 {name, icon} 交给调用方落库。
import { useState } from 'react';
import CategoryIcon, { CATEGORY_ICON_KEYS, EMOJI_CHOICES, iconLabel } from './CategoryIcon';
import { Check, X } from './icons';

export default function CategoryEditor({ initialName, initialIcon, onCommit, onCancel }:
  { initialName?: string; initialIcon?: string | null;
    onCommit: (name: string, icon: string | null) => void; onCancel: () => void }) {
  const [name, setName] = useState(initialName ?? '');
  const [icon, setIcon] = useState<string | null>(initialIcon ?? null);
  return (
    <div className="cat-editor" onClick={e => e.stopPropagation()}>
      <input className="cat-editor-name" autoFocus maxLength={40} value={name}
             placeholder="空间名（如：AI 研发 / 市场调研）"
             onChange={e => setName(e.target.value)}
             onKeyDown={e => {
               if (e.key === 'Escape') onCancel();
               else if (e.key === 'Enter' && name.trim()) onCommit(name.trim(), icon);
             }} />
      <div className="cat-editor-grid">
        {CATEGORY_ICON_KEYS.map(k => (
          <button key={k} title={iconLabel(k)}
                  className={`cat-pick ${icon === k ? 'on' : ''}`}
                  onClick={() => setIcon(k === icon ? null : k)}>
            <CategoryIcon icon={k} size={15} />
          </button>
        ))}
        {EMOJI_CHOICES.map(e => (
          <button key={e} title="emoji"
                  className={`cat-pick ${icon === e ? 'on' : ''}`}
                  onClick={() => setIcon(e === icon ? null : e)}>
            <CategoryIcon icon={e} size={15} />
          </button>
        ))}
      </div>
      <div className="cat-editor-ops">
        <button className="btn sm" disabled={!name.trim()}
                onClick={() => onCommit(name.trim(), icon)}><Check size={13} /> 确定</button>
        <button className="btn ghost sm" onClick={onCancel}><X size={13} /> 取消</button>
      </div>
    </div>
  );
}
