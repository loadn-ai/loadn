/** DW-12：员工 @ 提及——浮层数据（mention-list 拉取缓存）+ 选择上报。
 *  排序真源在服务端（选择次数 × 时间衰减加权），前端只缓存平铺列表。 */
import { api } from './client';

export interface MentionEmp {
  name: string;
  dept: string;
  title: string;
  duty: string;
  score: number;
}

let cache: { at: number; list: MentionEmp[] } | null = null;
const TTL_MS = 5 * 60 * 1000;   // 5 分钟：会话中途 org CRUD 也能较快感知
let inflight: Promise<MentionEmp[]> | null = null;

/** 浮层员工列表（加权预排序）。失败静默回空数组——@ 功能降级不打断输入。 */
export async function fetchMentionList(force = false): Promise<MentionEmp[]> {
  if (!force && cache && Date.now() - cache.at < TTL_MS) return cache.list;
  if (inflight) return inflight;
  inflight = api<{ employees: MentionEmp[] }>('/api/org/mention-list')
    .then(d => {
      cache = { at: Date.now(), list: d.employees ?? [] };
      return cache.list;
    })
    .catch(() => cache?.list ?? [])
    .finally(() => { inflight = null; });
  return inflight;
}

/** 选择上报（fire-and-forget：失败不提示，下次选择仍会累计到已有分）。 */
export function reportMention(name: string): void {
  void api('/api/org/mention', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  }).catch(() => { /* 静默：统计丢一次无碍 */ });
}
