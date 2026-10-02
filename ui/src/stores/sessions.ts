// 全局状态（zustand）：会话列表 / 当前会话 / SSE 实时流
import { create } from 'zustand';
import { api, apiUpload, connectSse } from '../api/client';
import { clearDraft, getDraft, setDraft } from './drafts';

/** 用户附件（消息 blocks_json 里的 attachment 条目） */
export interface Attachment {
  path: string; name: string; kb: number | null; is_image: boolean;
}

export interface SessionInfo {
  id: string; title: string; profile: string; status: string;
  project_id?: string | null; project_title?: string | null;
  starred?: number; pinned?: number; category_id?: number | null;
  session_fresh: number; claude_session_id: string | null;
  skills_json?: string; mcp_json?: string | null;
  params_json?: string | null; workspace?: string;
  cost_usd: number;
  profile_auto?: boolean;
  engine?: string | null;
  engine_override?: string | null;
  usage?: { in: number; out: number; total: number; cost_usd: number;
            cache_read?: number; cache_write?: number; total_all?: number;
            cost_api_usd?: number };
  active_turn?: TurnInfo | null;
  next_wake?: { id: number; label: string | null; due_at: string;
                every_s?: number | null; cron?: string | null;
                fires?: number; max_fires?: number } | null;
  created_at: string; updated_at: string;
}

/** 会话级参数（属性面板「模型参数」）：值类型收敛为 string|number|null */
export type ParamsMap = Record<string, string | number | null>;

/** detail 响应里属性面板要的附加字段（openSession/patch 后灌入） */
export interface SessionExtras {
  params: ParamsMap;
  params_effective: ParamsMap;
  params_profile: ParamsMap;
  skills_available: string[];
}

export interface TurnInfo {
  id: number; session_id: string; status: string; mode: string;
  duration_s?: number | null; cost_usd?: number | null;
  usage_json?: string | null; error?: string | null;
  started_at?: string | null;
}

export interface MessageInfo {
  id: number; turn_id: number | null; role: 'user' | 'assistant';
  content: string; blocks_json?: string | null; created_at: string;
}

export interface ProjectInfo {
  id: string; title: string; status: string; workspace: string;
  profile?: string | null; updated_at?: string; n_sessions?: number;
  starred?: number; pinned?: number; category_id?: number | null;
}

/** 侧栏自定义分区（任务/项目通用，与置顶/收藏/归档并列） */
export interface CategoryInfo {
  id: number; name: string;
}

/** 「移动到」目标分区：三标记位互斥（由 move* 负责清位），归档走 status */
export type MoveDest = 'pinned' | 'recent' | 'starred' | 'archive' | { cat: number };

export interface ArtifactInfo {
  id: number; path: string; kind: string; title: string;
  summary?: string | null;
  size: number; created_by: string;
}

export interface ToolEvent {
  id: string | null; name: string; brief: string; is_error?: boolean;
  input?: Record<string, string | number | boolean>; result?: string;
}

/** 过程流水项：思考 / 工具调用 / 文本 / 运行中插话，按真实顺序穿插渲染 */
export interface ToolItem extends ToolEvent { kind: 'tool' }
export type StreamItem =
  | { kind: 'thinking'; text: string }
  | ToolItem
  | { kind: 'text'; text: string }
  | { kind: 'steer'; text: string };

interface LiveTurn {
  turnId: number;
  status: 'queued' | 'running' | 'done' | 'error' | 'stopped' | 'interrupted';
  items: StreamItem[];
  todos: { subject: string; status: string }[];
  error?: string | null;
  startedAt: number;
  costUsd?: number | null;
}

export interface ApprovalInfo {
  id: number;
  action_type: string;
  summary: string;
  agent_note?: string | null;
  status: string;
  created_at?: string;
  /** P3-7 三要素 enrich：平台解析的参数 / justification 文案 / 本 turn 改动 */
  params?: Record<string, unknown>;
  justification?: string;
  turn_changes?: { path: string; lines?: string; hash?: string }[] | null;
}

/** P3-7 压缩时间线：turn 刻度 + compact 标记（何时裁了多少 token） */
export interface TimelineMarker {
  kind: 'turn' | 'compact';
  turns?: number | null;
  tokens?: number;
  tokens_cropped?: number | null;
  summary_first?: string;
}

interface Store {
  sessions: SessionInfo[];
  projects: ProjectInfo[];
  categories: CategoryInfo[];
  profiles: { name: string; description: string; skills: string[] }[];
  skills: { name: string; description: string }[];
  engines: Record<string, { ok?: boolean; version?: string }>;
  defaultEngine: string;
  currentSid: string | null;
  messages: MessageInfo[];
  turns: TurnInfo[];
  artifacts: ArtifactInfo[];
  live: LiveTurn | null;
  approvals: ApprovalInfo[];
  timeline: TimelineMarker[];
  es: EventSource | null;
  connected: boolean;
  theme: 'dark' | 'light';
  /** 属性面板（右面板第一 tab）：detail 附加字段 + tab/面板开合 + egress 刷新信号 */
  sessionExtras: SessionExtras | null;
  rightTab: 'properties' | 'artifacts' | 'files';
  panelOpen: boolean;
  egressTick: number;

  loadSessions: () => Promise<void>;
  loadMeta: () => Promise<void>;
  loadApprovals: () => Promise<void>;
  decideApproval: (id: number, approve: boolean) => Promise<string | null>;
  loadTimeline: () => Promise<void>;
  openSession: (sid: string) => Promise<void>;
  closeSession: () => void;
  resync: () => void;
  createSession: (body: Record<string, unknown>) => Promise<SessionInfo>;
  setRightTab: (t: Store['rightTab']) => void;
  setPanelOpen: (v: boolean) => void;
  /** 通用会话 PATCH（属性面板各段共用）；成功后刷新列表行 + extras */
  patchSession: (sid: string, patch: Record<string, unknown>) => Promise<void>;
  patchParams: (sid: string, params: ParamsMap) => Promise<void>;
  patchSkills: (sid: string, skills: string[]) => Promise<void>;
  patchMcp: (sid: string, mcp: Record<string, unknown>) => Promise<void>;
  /** 侧边栏「属性」入口：当前会话直接切 tab+开面板，否则先打开会话 */
  openProps: (sid: string) => Promise<void>;
  loadProjects: () => Promise<void>;
  createProject: (title: string, categoryId?: number) => Promise<ProjectInfo>;
  createSubtask: (pid: string) => Promise<SessionInfo>;
  renameProject: (pid: string, title: string) => Promise<void>;
  /** 现有任务升级为项目容器（原任务成为首个子任务，workspace 零迁移） */
  promoteSession: (sid: string) => Promise<void>;
  archiveProject: (pid: string) => Promise<void>;
  restoreProject: (pid: string) => Promise<void>;
  purgeProject: (pid: string) => Promise<void>;
  loadCategories: () => Promise<void>;
  createCategory: (name: string) => Promise<CategoryInfo | null>;
  renameCategory: (cid: number, name: string) => Promise<void>;
  deleteCategory: (cid: number) => Promise<void>;
  /** 统一移动（任务/项目同款）：置顶/最近/收藏/分类三标记位互斥；archive 走归档 */
  moveSession: (sid: string, dest: MoveDest) => Promise<void>;
  moveProject: (pid: string, dest: MoveDest) => Promise<void>;
  sendMessage: (text: string, mode?: string, attachments?: Attachment[]) => Promise<void>;
  /** 运行中发送：默认插话（loadn turn 下一轮 LLM 调用前注入）；
   *  queue=true 或无运行中 loadn turn / 带附件时回落 sendMessage（排队为新 turn） */
  steerOrSend: (text: string, mode?: string, attachments?: Attachment[],
                opts?: { queue?: boolean }) => Promise<void>;
  uploadFile: (file: File) => Promise<Attachment>;
  stopTurn: (tid: number) => Promise<void>;
  retractTurn: (tid: number) => Promise<void>;
  deleteMessage: (mid: number) => Promise<void>;
  editMessage: (mid: number, text: string) => Promise<void>;
  renameSession: (sid: string, title: string) => Promise<void>;
  archiveSession: (sid: string) => Promise<void>;
  restoreSession: (sid: string) => Promise<void>;
  purgeSession: (sid: string) => Promise<void>;
  toggleTheme: () => void;
}

/** 主题初值：显式选择 > 系统偏好 */
function initialTheme(): 'dark' | 'light' {
  const saved = localStorage.getItem('wd_theme');
  if (saved === 'light' || saved === 'dark') return saved;
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export const useStore = create<Store>((set, get) => ({
  sessions: [], projects: [], categories: [], profiles: [], skills: [], engines: {},
  defaultEngine: 'claude',
  currentSid: null, messages: [], turns: [], artifacts: [],
  live: null,
  approvals: [], timeline: [], es: null, connected: false,
  theme: initialTheme(),
  sessionExtras: null,
  rightTab: 'artifacts',
  // 桌面默认开右面板；窄屏它是遮盖式抽屉，默认收起（原 SessionView 本地态）
  panelOpen: typeof window !== 'undefined' && window.innerWidth > 900,
  egressTick: 0,

  setRightTab(t) { set({ rightTab: t }); },
  setPanelOpen(v) { set({ panelOpen: v }); },

  async loadApprovals() {
    const sid = get().currentSid;
    if (!sid) return;
    try {
      const d = await api<{ approvals: ApprovalInfo[] }>(
        `/api/sessions/${encodeURIComponent(sid)}/approvals`);
      if (get().currentSid === sid) set({ approvals: d.approvals });
    } catch { /* 拉取失败不阻塞 */ }
  },

  async decideApproval(id: number, approve: boolean): Promise<string | null> {
    try {
      const d = await api<{ ok: boolean; code?: string; status: string; error?: string }>(
        `/api/approvals/${id}/decide`, { method: 'POST', body: JSON.stringify({ approve }) });
      await get().loadApprovals();
      return d.code ?? null;      // 批准时一次性明文码（给用户转述给 agent）
    } catch (e) {
      alert(String(e));
      return null;
    }
  },

  async loadTimeline() {
    const sid = get().currentSid;
    if (!sid) return;
    try {
      const d = await api<{ timeline: TimelineMarker[] }>(
        `/api/sessions/${encodeURIComponent(sid)}/timeline`);
      if (get().currentSid === sid) set({ timeline: d.timeline });
    } catch { /* 拉取失败不阻塞 */ }
  },

  async loadSessions() {
    const d = await api<{ sessions: SessionInfo[] }>('/api/sessions');
    set({ sessions: d.sessions });
  },

  async loadMeta() {
    const [p, s, h] = await Promise.all([
      api<{ profiles: Store['profiles'] }>('/api/profiles'),
      api<{ skills: Store['skills'] }>('/api/skills'),
      api<{ engines: Record<string, { ok?: boolean; version?: string }>; default_engine: string }>('/api/health'),
    ]);
    set({ profiles: p.profiles, skills: s.skills,
          engines: h.engines ?? {}, defaultEngine: h.default_engine ?? 'claude' });
  },

  async openSession(sid) {
    get().closeSession();
    set({ currentSid: sid, messages: [], turns: [], artifacts: [], live: null,
          sessionExtras: null, timeline: [] });
    localStorage.setItem('loadn_sid', sid);   // 刷新/重开恢复
    localStorage.removeItem('wd_sid');        // 旧键清理（迁移遗留）
    const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[]; artifacts: ArtifactInfo[] } & SessionExtras>(
      `/api/sessions/${encodeURIComponent(sid)}`);
    set({ messages: d.messages, turns: d.turns, artifacts: d.artifacts });
    fillExtras(set, sid, d);
    // 进行中 turn：凭 /live 重建元信息（turnId/status/startedAt/todos——计时与停止按钮）。
    // 过程节点（items）不播种：SSE 新连接会精准回放本 turn 尾部事件（sse.py：
    // 断线补发在无 Last-Event-ID 时即全量），两路叠加会把流水算两遍（实测
    // 627 项变 1339）。回放是 items 的单一来源，这里只铺底座。
    // 优先 running：串行队列里后续消息排队时，live 应显示真正在跑的 turn，
    // 而不是最新那个 queued（否则运行中的流水一刷新就丢）。
    const act = d.turns.find(t => t.status === 'running')
             ?? d.turns.find(t => t.status === 'queued');
    if (act) {
      let seeded = false;
      try {
        const l = await api<{
          live: { turnId: number; status: LiveTurn['status']; items: StreamItem[];
                  todos: { subject: string; status: string }[]; startedAt: string } | null
        }>(`/api/sessions/${encodeURIComponent(sid)}/live`);
        // /live 与本地判定不一致（旧服务端取最新活跃 turn）时以本地为准，
        // 用 turn 行自 seeding——items 留空，SSE 新事件继续流入
        if (l.live && l.live.turnId === act.id && get().currentSid === sid) {
          set({ live: { turnId: l.live.turnId, status: l.live.status,
                        items: [], todos: l.live.todos ?? [],
                        startedAt: new Date(l.live.startedAt).getTime() || Date.now() } });
          seeded = true;
        }
      } catch { /* 引擎侧无活跃信息（刚崩溃等）则不重建 */ }
      if (!seeded && get().currentSid === sid) {
        set({ live: { turnId: act.id, status: act.status as LiveTurn['status'],
                      items: [], todos: [],
                      startedAt: act.started_at ? new Date(act.started_at).getTime() : Date.now() } });
      }
    }
    // SSE：实时事件（W0：一次性 ticket 连接 + 断流自动重新取票重连）
    set({ connected: false });
    void connectSse(
      sid,
      (type, data) => handleEvent(set, get, sid, type, data),
      () => set({ connected: true }),
      () => set({ connected: false }),
      () => get().currentSid === sid,
    ).then((es) => { if (get().currentSid === sid) set({ es }); else es.close(); });
    void get().loadTimeline();     // P3-7 压缩时间线（turn_done 时增量刷新）
    void get().loadSessions();
  },

  closeSession() {
    const { es } = get();
    if (es) { es.close(); }
    localStorage.removeItem('loadn_sid'); localStorage.removeItem('wd_sid');
    set({ es: null, connected: false, currentSid: null, sessionExtras: null,
          timeline: [] });
  },

  /** 前台恢复拉新（visibilitychange/focus 调用）。iOS PWA 后台冻结定时器与
   *  SSE 重连 setTimeout——回前台不等 5s 轮询周期与退避计时器：立即拉会话
   *  列表 + 当前会话全量；SSE 断线即刻重建（后台期间事件已丢，靠精准回放
   *  补齐活跃 turn 尾部）。
   *  不变量：重连路径必须空 items 重播种（回放是 items 的单一来源——保留
   *  旧 items 再叠回放=流水翻倍，v0.6.18 实测「重复返回」根因）。 */
  resync() {
    const now = Date.now();
    if (now - _lastResyncAt < 5000) return;   // focus/visibility 成对触发去重
    _lastResyncAt = now;
    void get().loadSessions();
    const sid = get().currentSid;
    if (!sid) return;
    (async () => {
      try {
        const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[];
                              artifacts: ArtifactInfo[] }>(
          `/api/sessions/${encodeURIComponent(sid)}`);
        if (get().currentSid !== sid) return;
        set({ messages: d.messages, turns: d.turns, artifacts: d.artifacts });
        const wasConnected = get().connected;
        const act = d.turns.find(t => t.status === 'running')
                 ?? d.turns.find(t => t.status === 'queued');
        if (!act) {
          if (get().live) set({ live: null }); // 无活跃 turn：残留 live 即陈旧状态
        } else if (!get().live || get().live!.turnId !== act.id) {
          // live 缺席/turn 更替：空种子铺底座。重连不清空已有 items——
          // SSE 断点续传（last_event_id）只补未见过的事件，清了会丢内容
          set({ live: { turnId: act.id, status: act.status as LiveTurn['status'],
                        items: [], todos: [],
                        startedAt: act.started_at
                          ? new Date(act.started_at).getTime() : Date.now() } });
        }
        if (!wasConnected) {                    // SSE 死了：立即重建（不等退避）
          const { es } = get();
          if (es) es.close();
          set({ es: null, connected: false });
          void connectSse(
            sid,
            (type, data) => handleEvent(set, get, sid, type, data),
            () => set({ connected: true }),
            () => set({ connected: false }),
            () => get().currentSid === sid,
          ).then((es2) => {
            if (get().currentSid === sid) set({ es: es2 }); else es2.close();
          });
        }
        void get().loadTimeline();
      } catch { /* 网络抖动忽略——轮询兜底 */ }
    })();
  },

  async createSession(body) {
    const d = await api<{ session: SessionInfo }>('/api/sessions', {
      method: 'POST', body: JSON.stringify(body) });
    await get().loadSessions();
    return d.session;
  },

  async patchSession(sid, patch) {
    const d = await api<{ ok: boolean; session: SessionInfo }>(
      `/api/sessions/${encodeURIComponent(sid)}`, {
        method: 'PATCH', body: JSON.stringify(patch) });
    set(s => ({ sessions: s.sessions.map(x => x.id === sid ? { ...x, ...d.session } : x) }));
    if (get().currentSid === sid) await refreshExtras(get, set, sid);
  },

  async patchParams(sid, params) { await get().patchSession(sid, { params }); },

  async patchSkills(sid, skills) { await get().patchSession(sid, { skills }); },

  async patchMcp(sid, mcp) { await get().patchSession(sid, { mcp }); },

  async openProps(sid) {
    // 当前会话：只切视图；其他会话：打开后再展示（openSession 异步铺数据）
    set({ panelOpen: true, rightTab: 'properties' });
    if (get().currentSid !== sid) await get().openSession(sid);
    else set({ rightTab: 'properties', panelOpen: true });
  },

  async loadProjects() {
    try {
      const d = await api<{ projects: ProjectInfo[] }>('/api/projects');
      set({ projects: d.projects });
    } catch { /* 项目列表失败不阻塞会话面 */ }
  },

  async createProject(title, categoryId) {
    const d = await api<{ project: ProjectInfo }>('/api/projects', {
      method: 'POST',
      body: JSON.stringify(categoryId != null
        ? { title, category_id: categoryId } : { title }) });
    await get().loadProjects();
    return d.project;
  },

  async createSubtask(pid) {
    const d = await api<{ session: SessionInfo }>('/api/sessions', {
      method: 'POST', body: JSON.stringify({ project_id: pid }) });
    await Promise.all([get().loadSessions(), get().loadProjects()]);
    return d.session;
  },

  async promoteSession(sid) {
    try {
      await api(`/api/sessions/${encodeURIComponent(sid)}/promote`, { method: 'POST' });
      await Promise.all([get().loadSessions(), get().loadProjects()]);
    } catch (e) {
      alert(`升级失败：${e instanceof Error ? e.message : e}`);
      throw e;
    }
  },

  async renameProject(pid, title) {
    await api(`/api/projects/${encodeURIComponent(pid)}`, {
      method: 'PATCH', body: JSON.stringify({ title }) });
    await get().loadProjects();
  },

  async archiveProject(pid) {
    await api(`/api/projects/${encodeURIComponent(pid)}`, { method: 'DELETE' });
    await Promise.all([get().loadSessions(), get().loadProjects()]);
  },

  async restoreProject(pid) {
    await api(`/api/projects/${encodeURIComponent(pid)}/restore`, { method: 'PATCH' });
    await Promise.all([get().loadSessions(), get().loadProjects()]);
  },

  async purgeProject(pid) {
    await api(`/api/projects/${encodeURIComponent(pid)}?purge=true`, { method: 'DELETE' });
    await Promise.all([get().loadSessions(), get().loadProjects()]);
  },

  async loadCategories() {
    try {
      const d = await api<{ categories: CategoryInfo[] }>('/api/categories');
      set({ categories: d.categories });
    } catch { /* 分类列表失败不阻塞侧栏 */}
  },

  async createCategory(name) {
    try {
      const d = await api<{ category: CategoryInfo }>('/api/categories', {
        method: 'POST', body: JSON.stringify({ name }) });
      await get().loadCategories();
      return d.category;
    } catch (e) {
      alert(`新建分类失败：${e instanceof Error ? e.message : e}`);
      return null;
    }
  },

  async renameCategory(cid, name) {
    await api(`/api/categories/${encodeURIComponent(cid)}`, {
      method: 'PATCH', body: JSON.stringify({ name }) });
    await get().loadCategories();
  },

  async deleteCategory(cid) {
    // 后端连坐清 category_id：任务/项目回「最近」，这里三路全刷
    await api(`/api/categories/${encodeURIComponent(cid)}`, { method: 'DELETE' });
    await Promise.all([get().loadCategories(), get().loadSessions(), get().loadProjects()]);
  },

  async moveSession(sid, dest) {
    if (dest === 'archive') { await get().archiveSession(sid); return; }
    // 乐观更新（后端分区操作不 touch updated_at，「最近」内顺序不动）
    const patch = destFlags(dest);
    set(s => ({ sessions: s.sessions.map(x => x.id === sid ? { ...x, ...patch } : x) }));
    try {
      await api(`/api/sessions/${encodeURIComponent(sid)}`, {
        method: 'PATCH', body: JSON.stringify(patch) });
    } catch (e) {
      await get().loadSessions();   // 失败回滚成服务端真相
      alert(`移动失败：${e instanceof Error ? e.message : e}`);
    }
  },

  async moveProject(pid, dest) {
    if (dest === 'archive') { await get().archiveProject(pid); return; }
    const patch = destFlags(dest);
    set(s => ({ projects: s.projects.map(p => p.id === pid ? { ...p, ...patch } : p) }));
    try {
      await api(`/api/projects/${encodeURIComponent(pid)}`, {
        method: 'PATCH', body: JSON.stringify(patch) });
    } catch (e) {
      await get().loadProjects();
      alert(`移动失败：${e instanceof Error ? e.message : e}`);
    }
  },

  async sendMessage(text, mode = 'foreground', attachments = []) {
    const sid = get().currentSid;
    if (!sid) return;
    // 乐观插入用户消息（附件进 blocks_json，与后端一致）
    const optimisticId = -Date.now();
    set(s => ({
      messages: [...s.messages, {
        id: optimisticId, turn_id: null, role: 'user' as const, content: text,
        blocks_json: attachments.length
          ? JSON.stringify(attachments.map(a => ({ type: 'attachment', ...a }))) : null,
        created_at: new Date().toISOString(),
      }],
    }));
    try {
      const d = await api<{ turn: TurnInfo }>(
        `/api/sessions/${encodeURIComponent(sid)}/messages`, {
          method: 'POST', body: JSON.stringify({ text, mode, attachments }) });
      // 回填 turn_id + turns 行：排队消息凭 turns.status==='queued' 被
      // ChatStream 识别进「输入框上方排队区」（不再插在聊天历史中间）
      if (d?.turn?.id) {
        set(s => ({
          messages: s.messages.map(m => m.id === optimisticId
            ? { ...m, turn_id: d.turn.id } : m),
          turns: [...s.turns, d.turn],
        }));
      }
    } catch (e) {
      // 发送失败（server 掉线等）：乐观消息必须撤掉，否则看起来已发出、
      // 刷新即消失——像"消息被吞"。空草稿则回填文本供改后重发。
      set(s => ({ messages: s.messages.filter(m => m.id !== optimisticId) }));
      if (!getDraft(sid)) setDraft(sid, text);
      alert(`发送失败：${e instanceof Error ? e.message : e}\n消息未发出${attachments.length ? '，附件需重新添加' : ''}，已放回输入框`);
      throw e;
    }
    void get().loadSessions();
  },

  async steerOrSend(text, mode = 'foreground', attachments = [], opts) {
    const sid = get().currentSid;
    if (!sid) return;
    // 只有真正 running 的 loadn turn 且未显式选「排队」才插话；
    // queued/非 loadn/带附件/用户选排队 → 直接 sendMessage
    const live = get().live;
    const running = live && live.status === 'running';
    if (running && !attachments.length && !opts?.queue) {
      try {
        const d = await api<{ ok: boolean; steered: boolean }>(
          `/api/sessions/${encodeURIComponent(sid)}/steer`,
          { method: 'POST', body: JSON.stringify({ text }) });
        if (d.steered) return;   // SSE "steer" 事件会把插话铺进流水
      } catch { /* steer 通道挂了回落正常发送 */ }
    }
    await get().sendMessage(text, mode, attachments);
  },

  async uploadFile(file) {
    const sid = get().currentSid;
    if (!sid) throw new Error('未打开会话');
    const d = await apiUpload<{ ok: boolean; path: string; name: string; kb: number; is_image: boolean }>(
      `/api/sessions/${encodeURIComponent(sid)}/upload`, file);
    return { path: d.path, name: d.name, kb: d.kb, is_image: d.is_image };
  },

  async stopTurn(tid) {
    try {
      await api(`/api/turns/${tid}/stop`, { method: 'POST' });
    } catch (e) {
      alert(`停止失败：${e instanceof Error ? e.message : e}`);
      throw e;
    }
  },

  async retractTurn(tid) {
    // 撤回排队中的消息：后端连 turn+消息+事件一起删，等同没发过
    try {
      await api(`/api/turns/${tid}`, { method: 'DELETE' });
    } catch (e) {
      alert(`撤回失败：${e instanceof Error ? e.message : e}`);
      throw e;
    }
    const sid = get().currentSid;
    if (!sid) return;
    const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[]; artifacts: ArtifactInfo[] }>(
      `/api/sessions/${encodeURIComponent(sid)}`);
    if (get().currentSid === sid) {
      set({ messages: d.messages, turns: d.turns, artifacts: d.artifacts });
    }
    void get().loadSessions();
  },

  async deleteMessage(mid) {
    // 删单条气泡（展示层）：后端删 messages 行 + resync 拉全量
    await api(`/api/messages/${mid}`, { method: 'DELETE' });
    const sid = get().currentSid;
    if (!sid) return;
    const d = await api<{ messages: MessageInfo[] }>(
      `/api/sessions/${encodeURIComponent(sid)}`);
    if (get().currentSid === sid) set({ messages: d.messages });
  },

  async editMessage(mid, text) {
    // 编辑单条气泡文本（展示层修正）
    await api(`/api/messages/${mid}`, {
      method: 'PUT', body: JSON.stringify({ text }) });
    const sid = get().currentSid;
    if (!sid) return;
    const d = await api<{ messages: MessageInfo[] }>(
      `/api/sessions/${encodeURIComponent(sid)}`);
    if (get().currentSid === sid) set({ messages: d.messages });
  },

  async renameSession(sid, title) {
    await api(`/api/sessions/${encodeURIComponent(sid)}`, {
      method: 'PATCH', body: JSON.stringify({ title }) });
    set(s => ({ sessions: s.sessions.map(x => x.id === sid ? { ...x, title } : x) }));
  },

  async archiveSession(sid) {
    await api(`/api/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE' });
    if (get().currentSid === sid) get().closeSession();
    await get().loadSessions();
  },

  async restoreSession(sid) {
    await api(`/api/sessions/${encodeURIComponent(sid)}`, {
      method: 'PATCH', body: JSON.stringify({ status: 'active' }) });
    await get().loadSessions();
  },

  async purgeSession(sid) {
    // 彻底删除：DB 行 + workspace 一并清掉，不可恢复
    await api(`/api/sessions/${encodeURIComponent(sid)}?purge=true`, { method: 'DELETE' });
    clearDraft(sid);   // 输入草稿不残留
    if (get().currentSid === sid) get().closeSession();
    await get().loadSessions();
  },

  toggleTheme() {
    const next = get().theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('wd_theme', next);
    set({ theme: next });
  },
}));

/** 移动目标的标记位组合：pinned/starred/category 三位互斥，落位即清另两位 */
function destFlags(dest: Exclude<MoveDest, 'archive'>):
    { pinned: number; starred: number; category_id: number | null } {
  return dest === 'pinned'  ? { pinned: 1, starred: 0, category_id: null }
       : dest === 'starred' ? { pinned: 0, starred: 1, category_id: null }
       : dest === 'recent'  ? { pinned: 0, starred: 0, category_id: null }
       :                      { pinned: 0, starred: 0, category_id: dest.cat };
}

type SetFn = (partial: Partial<Store> | ((s: Store) => Partial<Store>)) => void;
type DetailLike = SessionExtras & Partial<Pick<SessionInfo, 'skills_json' | 'mcp_json' | 'params_json'>>;

/** detail 响应 → 属性面板 extras + 会话行上的挂接字段同步 */
function fillExtras(set: SetFn, sid: string, d: DetailLike) {
  set(s => ({
    sessionExtras: {
      params: d.params ?? {},
      params_effective: d.params_effective ?? {},
      params_profile: d.params_profile ?? {},
      skills_available: d.skills_available ?? [],
    },
    sessions: s.sessions.map(x => x.id === sid
      ? { ...x, skills_json: d.skills_json ?? x.skills_json,
          mcp_json: d.mcp_json ?? x.mcp_json,
          params_json: d.params_json ?? x.params_json }
      : x),
  }));
}

/** PATCH 后轻量重拉 detail：extras 与行内挂接字段回到服务端真相 */
async function refreshExtras(get: () => Store, set: SetFn, sid: string) {
  try {
    const d = await api<DetailLike>(`/api/sessions/${encodeURIComponent(sid)}`);
    if (get().currentSid !== sid) return;
    fillExtras(set, sid, d);
  } catch { /* 拉取失败保持旧值（面板还能用手头数据） */ }
}

/** 文本/思考条目追加：delta=true 时续写到末条同类项（流式滚屏），
 *  否则整块新起一条（回放/无流式引擎的既有语义）。 */
function appendText(items: StreamItem[], kind: 'text' | 'thinking',
                    text: string, delta?: boolean): StreamItem[] {
  if (delta && items.length) {
    const last = items[items.length - 1];
    if (last.kind === kind) {
      return [...items.slice(0, -1), { ...last, text: last.text + text }];
    }
  }
  return [...items, { kind, text } as StreamItem];
}

/** resync 冷却（module 级）：focus 与 visibilitychange 常成对触发，5s 内去重 */
let _lastResyncAt = 0;

function handleEvent(
  set: (fn: (s: Store) => Partial<Store>) => void,
  get: () => Store, sid: string, type: string, data: any,
) {
  if (get().currentSid !== sid) return;
  if (type === 'approval') {
    // W1-2 审批卡片事件：request/decided → 拉最新列表
    void get().loadApprovals();
    return;
  }
  switch (type) {
    case 'turn_queued':
    case 'turn_started':
      set(s => {
        const status = type === 'turn_started' ? 'running' as const : 'queued' as const;
        // 回放的旧 turn 事件不许顶掉当前 live（turn id 单调递增）：否则中间会先把
        // 播种 live 换成历史 turn、计时归零，回放完才换回来
        if (s.live && data.turn_id < s.live.turnId) return {};
        // 本 turn 的 turn_started 只更新状态：保留播种的 startedAt 与已积累流水
        if (s.live && s.live.turnId === data.turn_id)
          return { live: { ...s.live, status } };
        // 排队中的新消息不顶掉正在运行 turn 的流水（会话串行：发消息即排队，
        // live 若切到 queued 新 turn，运行中 turn 的实时事件全被 turnId 过滤丢弃，
        // 看起来就是"一流水就停"）。新 turn 真正 turn_started 时（此刻旧 turn
        // 已终态清空 live）自然切换
        if (type === 'turn_queued' && s.live && s.live.status === 'running') return {};
        return { live: { turnId: data.turn_id, status,
                         items: [] as StreamItem[], todos: [], startedAt: Date.now() } };
      });
      // 顶栏"运行中/停止"读 sessions 列表的 active_turn（不读 live）：turn 启动时
      // 若不刷新列表，长跑 turn（无终态事件期间）顶栏会停留在旧快照上不亮
      void get().loadSessions();
      // 排队 turn 行同步（多标签页/重连：本页没发消息也要识别排队消息 →
      // 排队区；turn_started 时同拉一次，让排队区即时放行开跑的消息）
      if (type === 'turn_queued' || type === 'turn_started') {
        (async () => {
          const sid2 = get().currentSid;
          if (sid2 !== sid) return;
          const d = await api<{ turns: TurnInfo[] }>(
            `/api/sessions/${encodeURIComponent(sid)}`);
          if (get().currentSid === sid) set(() => ({ turns: d.turns }));
        })();
      }
      break;
    case 'text':
      set(s => (s.live && s.live.turnId === data.turn_id
        ? { live: { ...s.live, items: appendText(s.live.items, 'text', data.text, data.delta) } } : {}));
      break;
    case 'thinking':
      set(s => (s.live && s.live.turnId === data.turn_id
        ? { live: { ...s.live, items: appendText(s.live.items, 'thinking', data.text, data.delta) } } : {}));
      break;
    case 'tool_use':
      set(s => (s.live && s.live.turnId === data.turn_id
        ? { live: { ...s.live, items: [...s.live.items, {
            kind: 'tool', id: data.id, name: data.name, brief: data.brief, input: data.input,
          }] } }
        : {}));
      break;
    case 'tool_result':
      set(s => {
        if (!s.live || s.live.turnId !== data.turn_id) return {};
        const items = s.live.items.map(it =>
          it.kind === 'tool' && it.id === data.id
            ? { ...it, is_error: data.is_error, result: data.result }
            : it);
        return { live: { ...s.live, items } };
      });
      break;
    case 'todos':
      set(s => (s.live && s.live.turnId === data.turn_id
        ? { live: { ...s.live, todos: data.todos } } : {}));
      break;
    case 'steer':
      // 运行中插话已投递：铺进流水（下一轮 LLM 调用前注入上下文）
      set(s => (s.live && s.live.turnId === data.turn_id
        ? { live: { ...s.live, items: [...s.live.items, { kind: 'steer', text: data.text }] } }
        : {}));
      break;
    case 'turn_done':
    case 'turn_error':
    case 'turn_stopped':
      // 终态后拉全量（messages 落库 + turns + artifacts）。注意：重进会话时 SSE 会
      // 回放历史事件——旧 turn 的终态事件不许清掉当前活跃 turn 的 live（实测：
      // 上一轮 turn_done 把新一轮刚重建的运行条/停止按钮一起抹掉）
      (async () => {
        await new Promise(r => setTimeout(r, 300));
        const sid2 = get().currentSid;
        if (sid2 !== sid) return;
        const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[]; artifacts: ArtifactInfo[] }>(
          `/api/sessions/${encodeURIComponent(sid)}`);
        const cur = get().live;
        // 旧 turn 的终态：不清当前活跃 turn 的 live；自己的终态：清（消息已落库渲染）
        set(() => ({ messages: d.messages, turns: d.turns, artifacts: d.artifacts,
                     live: cur && cur.turnId !== data.turn_id ? cur : null }));
        void get().loadSessions();
        void get().loadTimeline();   // P3-7：compact/turn 标记可能新增
      })();
      break;
    case 'files':
      // 右侧面板刷新信号（简化：不实时展开文件树，由面板自取）
      break;
    case 'egress':
      // 数据流向推送（payload 带 sid）：本会话的外联变化 → 属性面板刷新信号
      if (data?.sid === sid) set(s => ({ egressTick: s.egressTick + 1 }));
      break;
    case 'turn_deleted':
      // 排队消息被撤回（本标签页或别处操作）→ 拉全量同步，乐观消息一并消失
      (async () => {
        const sid2 = get().currentSid;
        if (sid2 !== sid) return;
        const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[]; artifacts: ArtifactInfo[] }>(
          `/api/sessions/${encodeURIComponent(sid)}`);
        if (get().currentSid === sid) {
          set(() => ({ messages: d.messages, turns: d.turns, artifacts: d.artifacts }));
        }
        void get().loadSessions();
      })();
      break;
    case 'session_meta':
      // 自动标题等会话元信息 → 即时刷新侧边栏
      if (data.title) {
        set(s => ({ sessions: s.sessions.map(x => x.id === sid ? { ...x, title: data.title } : x) }));
      }
      break;
    case 'resync':
      set(() => ({ messages: data.messages ?? [], turns: data.turns ?? [], artifacts: [] }));
      break;
    default:
      break;
  }
}
