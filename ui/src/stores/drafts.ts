/** 每会话输入草稿：主区切文件预览 tab 会卸载 Composer、切会话会串台——
 *  文字统一存这里（localStorage 'wd_drafts' 持久化，空草稿不落盘，debounce 写回）。
 *  模块级可变状态、不进 zustand：击零键不触发任何重渲染。 */
const drafts: Record<string, string> = (() => {
  try { return JSON.parse(localStorage.getItem('wd_drafts') ?? '{}'); } catch { return {}; }
})();

let timer: ReturnType<typeof setTimeout> | undefined;
function save() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    const o: Record<string, string> = {};
    for (const [k, v] of Object.entries(drafts)) if (v) o[k] = v;
    try { localStorage.setItem('wd_drafts', JSON.stringify(o)); } catch { /* 隐私模式/满：只丢持久化 */ }
  }, 400);
}

export function getDraft(sid: string): string {
  return drafts[sid] ?? '';
}

export function setDraft(sid: string, v: string) {
  drafts[sid] = v;
  save();
}

/** 会话被彻底删除时清掉残留草稿（归档/恢复不清） */
export function clearDraft(sid: string) {
  delete drafts[sid];
  save();
}
