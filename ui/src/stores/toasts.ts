// 全局 Toast（AC-5.5）：操作成功/失败反馈浮层——替代散落各面板的常驻
// admin-msg（挤占工具栏空间且不自动消失）。操作型反馈走 toast（2.6s 自清），
// 加载错误仍走面板内 admin-err/admin-msg err（需常驻+可重试）。
import { create } from 'zustand';

export interface Toast { id: number; text: string; ok: boolean }

interface ToastState {
  toasts: Toast[];
  push: (text: string, ok?: boolean) => void;
  dismiss: (id: number) => void;
}

let _nextId = 1;

export const useToasts = create<ToastState>(set => ({
  toasts: [],
  push: (text, ok = true) => {
    const id = _nextId++;
    set(s => ({ toasts: [...s.toasts.slice(-4), { id, text, ok }] }));   // 最多 5 条
    setTimeout(() => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })),
      2600);
  },
  dismiss: id => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })),
}));

/** 非组件调用快捷面（组件内用 useToasts().push） */
export const toast = (text: string, ok = true) => useToasts.getState().push(text, ok);
