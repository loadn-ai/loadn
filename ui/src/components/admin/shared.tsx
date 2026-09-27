// 管理中心各 tab 共享的类型与小件（AdminPanel 拆分沉淀，v0.6.12）
export type AdminTab = 'skills' | 'tools' | 'settings' | 'schedules' | 'cost' | 'egress' | 'security' | 'resources';

export interface SkillItem {
  name: string; description: string; mtime: string; disabled: boolean;
  source?: { via: string; repo?: string; subpath?: string; installed_at?: string };
}
export interface McpServer { name: string; spec: Record<string, any>; sessions_overriding: number; session_only?: boolean }
export interface ProfileTools { name: string; description: string; disallowed_tools: string[] }


export function withQ(url: string): string {
  const t = new URLSearchParams(location.search).get('token');
  return t ? url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t) : url;
}
