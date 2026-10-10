// 管理中心（统一管理页）：Skills / 工具（MCP / 内建开关）/ 设置（含外观主题）/
// 定时 / 成本分析——原侧栏三个入口（暗色/成本/管理）合并于此。
// v0.6.12 起各 tab 拆至 components/admin/*（本文件只留页壳与 tab 切换）。
// AC-1.2 角色裁剪：cookie 普通用户只见有属主隔离的面（任务/定时/Webhooks）；
// 其余 tab 平台级（凭证/设置/审计/成本无 owner 维度）——隐藏 + 深链兜底占位卡。
// token 单用户模式（未 cookie 登录）无角色概念，全量开放不变。
import { Fragment, useEffect, useState } from 'react';

import CostTab from './CostPanel';
import SchedulesTab from './SchedulePanel';
import WebhooksTab from './admin/WebhooksTab';
import MemoryTab from './admin/MemoryTab';
import ActivityTab from './admin/ActivityTab';
import SecurityTab from './SecurityPanel';
import ResourcesTab from './ResourcesPanel';
import SkillsTab from './admin/SkillsTab';
import SettingsTab from './admin/SettingsTab';
import ToolsTab from './admin/ToolsTab';
import TasksTab from './admin/TasksTab';
import { api } from '../api/client';
import type { AdminTab } from './admin/shared';

export type { AdminTab };

/** adminOnly = 平台级数据面（无属主隔离，普通用户不可见）。
 *  tasks/schedules/webhooks 后端均有 owner 过滤（多用户批2），普通用户保留。
 *  AC-3.3：按使用域分组（能力/自动化/观测/治理/平台）重排 + 命名统一中文——
 *  11 个平级 tab 的扫读成本从「逐个排除」降为「选组再选」。 */
const TABS: { id: AdminTab; label: string; group: string; adminOnly?: boolean }[] = [
  { id: 'skills', label: '技能', group: '能力', adminOnly: true },
  { id: 'tools', label: '工具', group: '能力', adminOnly: true },
  { id: 'memory', label: '记忆', group: '能力', adminOnly: true },
  { id: 'tasks', label: '任务', group: '自动化' },
  { id: 'schedules', label: '定时', group: '自动化' },
  { id: 'webhooks', label: 'Webhook', group: '自动化' },
  { id: 'activity', label: '台账', group: '观测', adminOnly: true },
  { id: 'cost', label: '成本', group: '观测', adminOnly: true },
  { id: 'security', label: '安全', group: '治理', adminOnly: true },
  { id: 'settings', label: '设置', group: '平台', adminOnly: true },
  { id: 'resources', label: '资源', group: '平台', adminOnly: true },
];

function AdminDenied() {
  return (
    <div className="admin-body">
      <div className="setting-card">
        <h4>需要管理员权限</h4>
        <div className="muted" style={{ fontSize: 12 }}>
          此页管理平台级配置（凭证 / 设置 / 审计 / 全局资源），仅管理员可见。
          你当前以普通用户身份登录——可用功能见上方标签。
        </div>
      </div>
    </div>
  );
}

export default function AdminPanel({ onClose, initialTab, filterSid, onClearFilter }:
  { onClose: () => void; initialTab?: AdminTab;
    filterSid?: string; onClearFilter?: () => void }) {
  const [tab, setTabRaw] = useState<AdminTab>(initialTab ?? 'skills');
  // AC-3.1：tab↔hash 双向同步——切 tab 写地址栏（可收藏/分享/回退），
  // 浏览器回退/前进或站内 hash 跳转驱动 tab（替代 App.tsx 的 key remount）
  const setTab = (t: AdminTab) => {
    setTabRaw(t);
    const q = location.hash.split('?')[1];   // 保留 schedules?sid= 类查询参数
    history.replaceState(null, '', `#/admin/${t}${q ? '?' + q : ''}`);
  };
  useEffect(() => {
    const onHash = () => {
      const m = location.hash.match(/^#\/admin\/(\w+)/);
      if (m) {
        const t = TABS.find(x => x.id === m[1]);
        if (t) setTabRaw(t.id);
      }
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  // null = 未登录（token 单用户模式）或状态未返回 → 不裁剪（全量开放）
  const [userRole, setUserRole] = useState<string | null>(null);
  useEffect(() => {
    void api<{ logged_in: boolean; user: { role: string } | null }>('/api/auth/status')
      .then(d => setUserRole(d.logged_in && d.user ? d.user.role : null))
      .catch(() => setUserRole(null));
  }, []);
  const isPlainUser = userRole !== null && userRole !== 'admin';
  const visibleTabs = isPlainUser ? TABS.filter(t => !t.adminOnly) : TABS;
  const denied = isPlainUser && TABS.find(t => t.id === tab)?.adminOnly === true;

  return (
    <div className="admin-page">
      <div className="admin-head">
        <h2>管理中心</h2>
        <button className="btn ghost" onClick={onClose}>← 返回</button>
      </div>
      <div className="admin-tabs">
        {visibleTabs.map((t, i) => (
          <Fragment key={t.id}>
            {i > 0 && visibleTabs[i - 1].group !== t.group && (
              <span className="tab-group-sep" title={t.group}>{t.group}</span>
            )}
            <button className={`tab ${tab === t.id ? 'on' : ''}`}
              onClick={() => setTab(t.id)}>{t.label}</button>
          </Fragment>
        ))}
      </div>
      {denied ? <AdminDenied /> : tab === 'skills' ? <SkillsTab /> : tab === 'tasks' ? <TasksTab />
        : tab === 'tools' ? <ToolsTab />
        : tab === 'schedules'
          ? <SchedulesTab filterSid={filterSid} onClearFilter={onClearFilter} />
          : tab === 'webhooks' ? <WebhooksTab />
          : tab === 'memory' ? <MemoryTab />
          : tab === 'activity' ? <ActivityTab />
          : tab === 'cost' ? <CostTab />
          : tab === 'security' ? <SecurityTab />
          : tab === 'resources' ? <ResourcesTab /> : <SettingsTab />}
    </div>
  );
}
