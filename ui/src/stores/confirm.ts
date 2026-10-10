// 全局确认对话框（BD-2）：Promise 化 confirm——替代浏览器原生 confirm()
// （原生弹窗样式脱离主题体系、阻塞式、移动端观感差，视觉方案 G 组遗留项）。
// 用法：`if (await askConfirm({ title, body, danger: true })) { ... }`
import { create } from 'zustand';

export interface ConfirmOpts {
  title: string;
  body?: string;
  okText?: string;
  danger?: boolean;   // 危险操作红按钮（删除/禁用/熔断类）
}

interface ConfirmState {
  open: boolean;
  opts: ConfirmOpts | null;
  resolve: ((ok: boolean) => void) | null;
  ask: (opts: ConfirmOpts) => Promise<boolean>;
  settle: (ok: boolean) => void;
}

export const useConfirm = create<ConfirmState>((set, get) => ({
  open: false, opts: null, resolve: null,
  ask: opts => new Promise<boolean>(resolve => set({ open: true, opts, resolve })),
  settle: ok => {
    get().resolve?.(ok);
    set({ open: false, opts: null, resolve: null });   // resolve 置空→重复 settle 幂等
  },
}));

/** 非组件快捷面（组件外/事件 handler 直调） */
export const askConfirm = (opts: ConfirmOpts) => useConfirm.getState().ask(opts);
