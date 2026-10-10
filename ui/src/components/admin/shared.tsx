// 管理中心各 tab 共享的类型与小件（AdminPanel 拆分沉淀，v0.6.12）
// AC-2.3：'egress' 流量 tab 并入安全 tab 出口卡（AdminTab 收窄，旧深链 App.tsx 落 security）
export type AdminTab = 'skills' | 'tools' | 'settings' | 'schedules' | 'webhooks' | 'memory' | 'activity' | 'cost' | 'security' | 'resources' | 'tasks';

export interface SkillItem {
  name: string; description: string; mtime: string; disabled: boolean;
  source?: { via: string; repo?: string; subpath?: string; installed_at?: string };
  pinned?: boolean;      // 七轮：供应链锁状态（外部 skill 受 rug-pull 防护）
  lock_ok?: boolean;     // false=哈希不匹配——引擎已拒索引（页面仍显示）
}
export interface McpServer { name: string; spec: Record<string, any>; sessions_overriding: number; session_only?: boolean }
export interface ProfileTools { name: string; description: string; disallowed_tools: string[] }


export function withQ(url: string): string {
  const t = new URLSearchParams(location.search).get('token');
  return t ? url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t) : url;
}
