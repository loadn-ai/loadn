// 管理中心（统一管理页）：Skills / 工具（MCP / 内建开关）/ 设置（含外观主题）/
// 定时 / 成本分析——原侧栏三个入口（暗色/成本/管理）合并于此。
// v0.6.12 起各 tab 拆至 components/admin/*（本文件只留页壳与 tab 切换）。
import { useState } from 'react';

import CostTab from './CostPanel';
import SchedulesTab from './SchedulePanel';
import WebhooksTab from './admin/WebhooksTab';
import MemoryTab from './admin/MemoryTab';
import ActivityTab from './admin/ActivityTab';
import SecurityTab from './SecurityPanel';
import ResourcesTab from './ResourcesPanel';
import EgressPanel from './admin/EgressPanel';
import SkillsTab from './admin/SkillsTab';
import SettingsTab from './admin/SettingsTab';
import ToolsTab from './admin/ToolsTab';
import TasksTab from './admin/TasksTab';
import type { AdminTab } from './admin/shared';

export type { AdminTab };

export default function AdminPanel({ onClose, initialTab, filterSid, onClearFilter }:
  { onClose: () => void; initialTab?: AdminTab;
    filterSid?: string; onClearFilter?: () => void }) {
  const [tab, setTab] = useState<AdminTab>(initialTab ?? 'skills');

  return (
    <div className="admin-page">
      <div className="admin-head">
        <h2>管理中心</h2>
        <button className="btn ghost" onClick={onClose}>← 返回</button>
      </div>
      <div className="admin-tabs">
        <button className={`tab ${tab === 'skills' ? 'on' : ''}`} onClick={() => setTab('skills')}>Skills</button>
        <button className={`tab ${tab === 'tools' ? 'on' : ''}`} onClick={() => setTab('tools')}>工具</button>
        <button className={`tab ${tab === 'settings' ? 'on' : ''}`} onClick={() => setTab('settings')}>设置</button>
        <button className={`tab ${tab === 'tasks' ? 'on' : ''}`} onClick={() => setTab('tasks')}>任务</button>
        <button className={`tab ${tab === 'schedules' ? 'on' : ''}`} onClick={() => setTab('schedules')}>定时</button>
        <button className={`tab ${tab === 'webhooks' ? 'on' : ''}`} onClick={() => setTab('webhooks')}>Webhooks</button>
        <button className={`tab ${tab === 'memory' ? 'on' : ''}`} onClick={() => setTab('memory')}>记忆</button>
        <button className={`tab ${tab === 'activity' ? 'on' : ''}`} onClick={() => setTab('activity')}>台账</button>
        <button className={`tab ${tab === 'cost' ? 'on' : ''}`} onClick={() => setTab('cost')}>成本</button>
        <button className={`tab ${tab === 'egress' ? 'on' : ''}`} onClick={() => setTab('egress')}>流量</button>
        <button className={`tab ${tab === 'security' ? 'on' : ''}`} onClick={() => setTab('security')}>安全</button>
        <button className={`tab ${tab === 'resources' ? 'on' : ''}`} onClick={() => setTab('resources')}>资源</button>
      </div>
      {tab === 'skills' ? <SkillsTab /> : tab === 'tasks' ? <TasksTab />
        : tab === 'tools' ? <ToolsTab />
        : tab === 'schedules'
          ? <SchedulesTab filterSid={filterSid} onClearFilter={onClearFilter} />
          : tab === 'webhooks' ? <WebhooksTab />
          : tab === 'memory' ? <MemoryTab />
          : tab === 'activity' ? <ActivityTab />
          : tab === 'cost' ? <CostTab />
          : tab === 'egress' ? <EgressPanel />
          : tab === 'security' ? <SecurityTab onClose={onClose} />
          : tab === 'resources' ? <ResourcesTab /> : <SettingsTab />}
    </div>
  );
}
