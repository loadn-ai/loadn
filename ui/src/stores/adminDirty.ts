// 管理页脏状态登记（AC-5.2）：子面板上报「有未保存改动」，AdminPanel 切 tab
// 前统一拦截确认——此前设置页改了一半点别的 tab 直接 unmount 静默丢弃。
// 约定：source 为面板标识（settings/skill-editor/…）；面板 unmount 或保存
// 成功后自行清位；AdminPanel 切 tab 成功后整体 reset（旧面板已卸载）。
import { create } from 'zustand';

interface AdminDirtyState {
  dirty: Record<string, boolean>;
  setDirty: (source: string, v: boolean) => void;
  reset: () => void;
}

export const useAdminDirty = create<AdminDirtyState>(set => ({
  dirty: {},
  setDirty: (source, v) => set(s => {
    if (!!s.dirty[source] === v) return s;   // 无变化不触发订阅渲染
    const dirty = { ...s.dirty };
    if (v) dirty[source] = true;
    else delete dirty[source];
    return { dirty };
  }),
  reset: () => set({ dirty: {} }),
}));

export const anyAdminDirty = () =>
  Object.keys(useAdminDirty.getState().dirty).length > 0;
