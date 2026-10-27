// 全局状态（zustand）：会话列表 / 当前会话 / SSE 实时流
import { create } from 'zustand';
import { api, apiUpload, connectSse, resetSseCursor } from '../api/client';
import { applyResult, applyTool, deriveAgents, type AgentInfo } from './agents';
import { clearDraft, getDraft, setDraft } from './drafts';
import { toast } from './toasts';

/** 用户附件（消息 blocks_json 里的 attachment 条目） */
export interface Attachment {
  path: string; name: string; kb: number | null; is_image: boolean;
}

export interface SessionInfo {
  id: string; title: string; profile: string; status: string;
  project_id?: string | null; project_title?: string | null;
  starred?: number; pinned?: number; category_id?: number | null;
  auto_continue?: number;   // rev18 自动续跑：1=turn 完成后 todo 未完自动续
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
  /** 子任务标签（rev10 分类器打标；NULL=应答形态，留在主控合集） */
  subtask_id?: number | null;
}

/** 会话内子任务（rev10 概念模型：项目 ⊃ 会话=任务 ⊃ 子任务）。
 *  成员=turns.filter(subtask_id===id)；agent/产物经 turn join 派生 */
export interface SubtaskInfo {
  id: number; session_id: string; title: string; status: string;
  created_at: string; updated_at: string;
}

export interface MessageInfo {
  id: number; turn_id: number | null; role: 'user' | 'assistant';
  content: string; blocks_json?: string | null; created_at: string;
  memory_hits_json?: string | null;   // P7：本 turn 注入记忆清单
}

export interface ProjectInfo {
  id: string; title: string; status: string; workspace: string;
  profile?: string | null; updated_at?: string; n_sessions?: number;
  starred?: number; pinned?: number; category_id?: number | null;
  /** 绑定的代码仓根（rev11 项目规则遵循：任务目录落 <repo>/tasks/，宪法链带仓规则） */
  repo?: string | null;
}

/** 侧栏自定义分区（任务/项目通用，与置顶/收藏/归档并列） */
export interface CategoryInfo {
  id: number; name: string;
  /** 空间图标：icons.tsx 图标键或 emoji 字符；空回退 Tag */
  icon?: string | null;
}

/** 「移动到」目标分区：三标记位互斥（由 move* 负责清位），归档走 status */
export type MoveDest = 'pinned' | 'recent' | 'starred' | 'archive' | { cat: number };

export interface ArtifactInfo {
  id: number; path: string; kind: string; title: string;
  summary?: string | null;
  size: number; created_by: string;
  /** 归属（agent_files 轨迹合并）：epoch 秒 */
  mtime?: number;
  turn_id?: number | null;
  agent_id?: string | null;
  agent_name?: string | null;
}

export interface ToolEvent {
  id: string | null; name: string; brief: string; is_error?: boolean;
  input?: Record<string, string | number | boolean>; result?: string;
  /** 子代理归属（宿主派生，PROTOCOL §2.1）：Task 卡带全套，子N· 转发带 id/n/name */
  agent_id?: string | null;
  agent_name?: string | null;
  agent_n?: number | null;
  agent_role?: string | null;
  subagent_type?: string | null;
}

/** 过程流水项：思考 / 工具调用 / 文本 / 运行中插话，按真实顺序穿插渲染。
 *  thinking 带 agent 字段 = 子代理思考低频聚合块（宿主派生归属，
 *  cum 为该子代理累计字数——live 折叠行「思考中… 已 N 字」显示用） */
export interface ThinkItem {
  kind: 'thinking'; text: string;
  agent_id?: string | null; agent_name?: string | null;
  agent_n?: number | null; cum?: number | null;
}
export interface ToolItem extends ToolEvent { kind: 'tool' }
/** 文字项：agent_n 非空 = 子代理文字块（宿主派生归属，渲染成带「子N」
 *  徽标的文字卡）；空 = 主控正文。两态同构，避免 union 重复成员 */
export interface TextItem {
  kind: 'text'; text: string;
  agent_id?: string | null; agent_name?: string | null;
  agent_n?: number | null;
}
/** todos 快照块（blocks_json 回放）：引擎每次 TaskCreate/TaskUpdate/
 *  TodoWrite 落当前全量快照，回放取最后一块还原清单（与 live TodoList
 *  同位同源——重载不丢任务清单） */
export interface TodosItem {
  kind: 'todos'; todos: { subject: string; status: string }[];
}
export type StreamItem =
  | ThinkItem
  | ToolItem
  | TextItem
  | TodosItem
  | { kind: 'steer'; text: string; id?: string };

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
  /** PERF-2：已冷归档的消息条数（详情 messages_folded；0=无折叠/已展开） */
  foldedCount: number;
  loadArchived: () => Promise<void>;
  /** PERF-4：详情尾窗之前还有多少条更早历史（0=已到底；「加载更早」
   *  按钮的数据源。分页回源每次精确更新——服务端 earlier 相对新页首） */
  earlierCount: number;
  loadingEarlier: boolean;
  loadEarlier: () => Promise<void>;
  turns: TurnInfo[];
  artifacts: ArtifactInfo[];
  /** 当前会话的子代理注册表（turn 域复合键，见 stores/agents.ts） */
  agents: AgentInfo[];
  /** 当前会话的子任务清单（rev10 分类器打标；tab=子任务，agent 走 chips） */
  subtasks: SubtaskInfo[];
  live: LiveTurn | null;
  /** 停止请求在途的 turn（tid → 受理时刻 ms）：停止按钮据此禁用防重复触发
   *  （stop 是异步收尾：active turn 引擎侧置 stop 事件后由循环结账，期间
   *  重复 POST 只会吃 404）。终态确认（SSE turn_stopped/done/error 或
   *  pollStopConfirmed 轮询兜底）后清除；POST 失败立即清除允许重试。 */
  stoppingTids: Record<number, number>;
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
  /** 主区活动 tab：'chat' | 's:<subtaskId>' | 'a:<agentKey>' | 'f:<path>' */
  mainTab: string;
  /** agent 视角筛选（按人名聚合——引擎跨轮重名，名字才是稳定身份；
   *  chips/子任务内点击挂上；清除回原 tab。顶部 tab 恒为子任务 */
  agentFilter: string | null;
  setAgentFilter: (name: string | null) => void;
  /** 交付物/产出物联动筛选（右面板 产物+工作区 两 tab 共用同一条件同步
   *  过滤）：null=全部；'global'=主控/导出产物（无 agent 归属）；number=
   *  子任务 id（turn→subtask join 派生，与产物分组同源）。切会话即重置 */
  artifactScope: number | 'global' | null;
  setArtifactScope: (v: number | 'global' | null) => void;
  /** 侧栏当前空间：'recent' | 'starred' | 'archive' | 'cat:<id>' */
  activeSpace: string;
  /** Composer 注入请求（「新建子任务/招募」模板）：seq 递增防同文本去重失效 */
  composeReq: { seq: number; text: string } | null;

  loadSessions: () => Promise<void>;
  loadMeta: () => Promise<void>;
  loadApprovals: () => Promise<void>;
  decideApproval: (id: number, approve: boolean) => Promise<string | null>;
  loadTimeline: () => Promise<void>;
  loadArtifacts: () => Promise<void>;
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
  createProject: (title: string, categoryId?: number, repo?: string) => Promise<ProjectInfo>;
  /** 绑定/解绑代码仓（rev11 项目规则遵循）；返回服务端提示（gitignore 建议/非 git 仓警告等） */
  setProjectRepo: (pid: string, repo: string) => Promise<string>;
  createSubtask: (pid: string) => Promise<SessionInfo>;
  renameProject: (pid: string, title: string) => Promise<void>;
  /** 现有任务升级为项目容器（原任务成为首个子任务，workspace 零迁移） */
  promoteSession: (sid: string) => Promise<void>;
  archiveProject: (pid: string) => Promise<void>;
  restoreProject: (pid: string) => Promise<void>;
  purgeProject: (pid: string) => Promise<void>;
  loadCategories: () => Promise<void>;
  createCategory: (name: string, icon?: string) => Promise<CategoryInfo | null>;
  /** 改名/改图标（icon 省略=不动；显式 null=清回兜底） */
  updateCategory: (cid: number, name: string | null, icon?: string | null) => Promise<void>;
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
  /** 撤回未注入的插话（引擎下一轮 LLM 调用前有效；已注入 409） */
  retractSteer: (sid: string, steerId: string) => Promise<boolean>;
  deleteMessage: (mid: number) => Promise<void>;
  editMessage: (mid: number, text: string) => Promise<void>;
  renameSession: (sid: string, title: string) => Promise<void>;
  archiveSession: (sid: string) => Promise<void>;
  restoreSession: (sid: string) => Promise<void>;
  purgeSession: (sid: string) => Promise<void>;
  toggleTheme: () => void;
  setMainTab: (t: string) => void;
  setActiveSpace: (s: string) => void;
  /** 往 Composer 注入模板文本（「新建子任务/招募」按钮） */
  requestCompose: (text: string) => void;
}

/** 主题初值：显式选择 > 默认亮色（多 Agent 工作台改版——设计稿为亮色） */
function initialTheme(): 'dark' | 'light' {
  const saved = localStorage.getItem('wd_theme');
  if (saved === 'light' || saved === 'dark') return saved;
  return 'light';
}

/** 侧栏空间初值：上次选择 > 'recent' */
function initialSpace(): string {
  return localStorage.getItem('wd_space') || 'recent';
}

export const useStore = create<Store>((set, get) => ({
  sessions: [], projects: [], categories: [], profiles: [], skills: [], engines: {},
  defaultEngine: 'claude',
  currentSid: null, messages: [], foldedCount: 0, earlierCount: 0,
  loadingEarlier: false, turns: [], artifacts: [],
  agents: [], subtasks: [],
  live: null,
  stoppingTids: {},
  approvals: [], timeline: [], es: null, connected: false,
  theme: initialTheme(),
  sessionExtras: null,
  rightTab: 'artifacts',
  // 桌面默认开右面板；窄屏它是遮盖式抽屉，默认收起（原 SessionView 本地态）
  panelOpen: typeof window !== 'undefined' && window.innerWidth > 900,
  egressTick: 0,
  mainTab: 'chat',
  agentFilter: null,
  artifactScope: null,
  activeSpace: initialSpace(),
  composeReq: null,

  setRightTab(t) { set({ rightTab: t }); },
  setPanelOpen(v) { set({ panelOpen: v }); },
  setMainTab(t) { set({ mainTab: t }); },
  setAgentFilter(key) { set({ agentFilter: key }); },
  setArtifactScope(v) { set({ artifactScope: v }); },
  setActiveSpace(s) {
    localStorage.setItem('wd_space', s);
    set({ activeSpace: s });
  },
  requestCompose(text) {
    set(s => ({ composeReq: { seq: (s.composeReq?.seq ?? 0) + 1, text } }));
  },

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
      // 六轮修 A6：ok:false 业务失败（host 非法/并发窗口/非 pending）原先
      // 静默——卡片滞留 pending 用户反复点无效。alert 真实原因
      if (!d.ok) alert(`裁决未生效：${d.error || d.status}`);
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

  /** 产物面板拉新：摘要回填是收尾后数秒落地的后台任务，tab 激活时再取一次 */
  async loadArtifacts() {
    const sid = get().currentSid;
    if (!sid) return;
    try {
      const d = await api<{ artifacts: ArtifactInfo[] }>(
        `/api/sessions/${encodeURIComponent(sid)}`);
      if (get().currentSid === sid) set({ artifacts: d.artifacts });
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
    // 游标重置：重进会话 live.items 从空重铺，SSE 需要 fresh 全量尾窗回放
    // 作单一来源（不重置→增量衔接→叠进空 items 之外还丢尾窗前的内容）
    resetSseCursor(sid);
    set({ currentSid: sid, messages: [], foldedCount: 0,
          earlierCount: 0, loadingEarlier: false,
          turns: [], artifacts: [], live: null,
          sessionExtras: null, timeline: [], agents: [], subtasks: [],
          mainTab: 'chat', agentFilter: null, artifactScope: null });
    localStorage.setItem('loadn_sid', sid);   // 刷新/重开恢复
    localStorage.removeItem('wd_sid');        // 旧键清理（迁移遗留）
    const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[];
                        artifacts: ArtifactInfo[]; subtasks: SubtaskInfo[] } & SessionExtras & {
                        messages_folded?: number; messages_earlier?: number }>(
      `/api/sessions/${encodeURIComponent(sid)}`);
    set({ messages: applyServerWindow(sid, d.messages, d.turns),
          turns: mergeServerTurns(sid, d.turns),
          artifacts: d.artifacts,
          agents: deriveAgents(d.messages, d.turns),
          subtasks: d.subtasks ?? [], foldedCount: d.messages_folded ?? 0,
          earlierCount: d.messages_earlier ?? 0 });
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
    ).then((es) => {
      // 取票是异步的：快速切页（A→B→A）下同 sid 可能有两条连接先后上岸——
      // 后到者上岗时必须关掉前任，否则双流双投（每条事件 apply 两遍）
      if (get().currentSid === sid) {
        const prev = get().es;
        set({ es });
        if (prev && prev !== es) prev.close();
      } else es.close();
    });
    void get().loadTimeline();     // P3-7 压缩时间线（turn_done 时增量刷新）
    void get().loadSessions();
  },

  /** PERF-2：展开冷归档段（ChatStream「⋯ N 条已归档」按钮）——回源端点
   *  拉全文 prepend 进 messages；归档 id 恒小于库内保留区，天然有序 */
  async loadArchived() {
    const sid = get().currentSid;
    if (!sid) return;
    const d = await api<{ messages: MessageInfo[]; count: number }>(
      `/api/sessions/${encodeURIComponent(sid)}/messages/archived`);
    if (get().currentSid !== sid) return;
    set({ messages: [...d.messages, ...get().messages], foldedCount: 0 });
  },

  /** PERF-4：加载更早一页历史（ChatStream「⋯ 加载更早的 N 条」按钮）。
   *  before_id=当前最早已加载消息 id；响应 id 升序、严格更早且连续——
   *  前插天然有序。earlier 由服务端相对新页首精确给出（0=到底收按钮）。 */
  async loadEarlier() {
    const sid = get().currentSid;
    const msgs = get().messages;
    if (!sid || !msgs.length || get().loadingEarlier) return;
    set({ loadingEarlier: true });
    try {
      const d = await api<{ messages: MessageInfo[]; earlier: number }>(
        `/api/sessions/${encodeURIComponent(sid)}/messages/page?before_id=${msgs[0].id}`);
      if (get().currentSid !== sid) {         // 竞态：用户已切走
        set({ loadingEarlier: false });       // 残留 true 会卡死按钮（重进兜底前）
        return;
      }
      set(s => s.messages[0]?.id === msgs[0].id   // 首条未被并发刷新变更
        ? { messages: [...d.messages, ...s.messages],
            earlierCount: d.earlier, loadingEarlier: false }
        : { earlierCount: d.earlier, loadingEarlier: false });
    } catch (e) {
      set({ loadingEarlier: false });
      throw e;
    }
  },

  closeSession() {
    const { es } = get();
    if (es) { es.close(); }
    localStorage.removeItem('loadn_sid'); localStorage.removeItem('wd_sid');
    set({ es: null, connected: false, currentSid: null, sessionExtras: null,
          timeline: [], agents: [], subtasks: [], live: null, mainTab: 'chat',
          agentFilter: null, artifactScope: null });
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
                              artifacts: ArtifactInfo[]; subtasks: SubtaskInfo[];
                              messages_folded?: number; messages_earlier?: number }>(
          `/api/sessions/${encodeURIComponent(sid)}`);
        if (get().currentSid !== sid) return;
        set({ messages: applyServerWindow(sid, d.messages, d.turns),
              turns: mergeServerTurns(sid, d.turns),
              artifacts: d.artifacts,
              agents: deriveAgents(d.messages, d.turns),
              subtasks: d.subtasks ?? [], foldedCount: d.messages_folded ?? 0 });
        // earlierCount 不在此更新：服务端 earlier 相对其尾窗首条，本地
        // 若已前插更早页则口径不同——分页状态只由 openSession 初始化+
        // loadEarlier 精确维护（本次打开会话内一致）
        // 停止在途标志对账：重拉后已终态的 turn 清标志（SSE 掉线时回前台的
        // resync 就是「引擎已停止」确认通道，不用等 pollStopConfirmed 超时）
        for (const st of Object.keys(get().stoppingTids).map(Number)) {
          const t = d.turns.find(x => x.id === st);
          if (!t || (t.status !== 'running' && t.status !== 'queued')) clearStopping(set, st);
        }
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
          // 重建是全新 connectSse：游标已持久化（client.ts sseCursors），
          // 带 last_event_id 增量补发衔接保留的 items——若游标归零会
          // fresh 全量回放尾窗叠进保留流水 = 切页回来「输出/插话两遍」
          void connectSse(
            sid,
            (type, data) => handleEvent(set, get, sid, type, data),
            () => set({ connected: true }),
            () => set({ connected: false }),
            () => get().currentSid === sid,
          ).then((es2) => {
            if (get().currentSid === sid) {
              const prev = get().es;
              set({ es: es2 });
              if (prev && prev !== es2) prev.close();   // 同 sid 双连接：上岗关前任
            } else es2.close();
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

  async createProject(title, categoryId, repo) {
    const body: Record<string, unknown> = { title };
    if (categoryId != null) body.category_id = categoryId;
    if (repo) body.repo = repo;
    const d = await api<{ project: ProjectInfo }>('/api/projects', {
      method: 'POST', body: JSON.stringify(body) });
    await get().loadProjects();
    return d.project;
  },

  async setProjectRepo(pid, repo) {
    const d = await api<{ repo_hint?: string }>(`/api/projects/${encodeURIComponent(pid)}`, {
      method: 'PATCH', body: JSON.stringify({ repo }) });
    await get().loadProjects();
    return d.repo_hint ?? '';
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

  async createCategory(name, icon) {
    try {
      const d = await api<{ category: CategoryInfo }>('/api/categories', {
        method: 'POST',
        body: JSON.stringify(icon != null ? { name, icon } : { name }) });
      await get().loadCategories();
      return d.category;
    } catch (e) {
      alert(`新建分类失败：${e instanceof Error ? e.message : e}`);
      return null;
    }
  },

  async updateCategory(cid, name, icon) {
    const body: Record<string, unknown> = {};
    if (name != null) body.name = name;
    if (icon !== undefined) body.icon = icon;
    await api(`/api/categories/${encodeURIComponent(cid)}`, {
      method: 'PATCH', body: JSON.stringify(body) });
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
    // 乐观插入用户消息（附件进 blocks_json，与后端一致）。id 走页内发号器
    // （-1,-2,… 单调递减）：原 -Date.now() 同毫秒连续发送会撞 id。
    // 除进数组外同步登记 inflightSends——POST 落定前任何 messages 全量
    // 覆盖（resync/终态回填等）都会经 mergeServerMsgs 把它合回，不再被吞
    const optimisticId = -(++_optSeq);
    const optMsg: MessageInfo = {
      id: optimisticId, turn_id: null, role: 'user' as const, content: text,
      blocks_json: attachments.length
        ? JSON.stringify(attachments.map(a => ({ type: 'attachment', ...a }))) : null,
      created_at: new Date().toISOString(),
    };
    registerInflight(sid, optMsg);
    set(s => ({ messages: [...s.messages, optMsg] }));
    try {
      const d = await api<{ turn: TurnInfo }>(
        `/api/sessions/${encodeURIComponent(sid)}/messages`, {
          method: 'POST', body: JSON.stringify({ text, mode, attachments }) });
      const tid = d?.turn?.id;
      // 回填 turn_id + turns 行：排队消息凭 turns.status==='queued' 被
      // ChatStream 识别进「输入框上方排队区」（不再插在聊天历史中间）
      if (tid) {
        if (retractedTurns.has(tid)) {
          // 落定前已被撤回（别处撤了刚排队的消息，撤回事件早于本响应）：
          // 不回填、不铺 turns，乐观项一并撤除——等同没发过
          retractedTurns.delete(tid);
          dropInflight(sid, optimisticId);
          set(s => (s.currentSid === sid
            ? { messages: s.messages.filter(m => m.id !== optimisticId) } : {}));
        } else {
          settleInflight(sid, optimisticId, tid, d.turn);
          set(s => {
            // 发送期间已切走：不许把 A 会话的消息/turn 写进正开着的 B 会话
            // （A 的登记留着 turn_id，切回 A 时 openSession 快照自会对账）
            if (s.currentSid !== sid) return {};
            // 全量覆盖先落地且快照已含服务端副本（POST 响应后到）：撤掉
            // 乐观项防双条；否则给乐观项回填 turn_id（或被覆盖后补插回）
            const serverCopy = s.messages.some(
              m => m.turn_id === tid && m.id !== optimisticId);
            return {
              messages: serverCopy
                ? s.messages.filter(m => m.id !== optimisticId)
                : s.messages.map(m => m.id === optimisticId ? { ...m, turn_id: tid } : m),
              // turn_queued SSE 的 turns 重拉可能先到（引擎在响应前同步发布）：
              // 按 id 去重，不重复追加
              turns: s.turns.some(t => t.id === tid) ? s.turns : [...s.turns, d.turn],
            };
          });
        }
      }
    } catch (e) {
      // 发送失败（server 掉线等）：乐观消息必须撤掉，否则看起来已发出、
      // 刷新即消失——像"消息被吞"。空草稿则回填文本供改后重发。
      dropInflight(sid, optimisticId);
      set(s => (s.currentSid === sid
        ? { messages: s.messages.filter(m => m.id !== optimisticId) } : {}));
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
        if (d.steered) {
          // 插话回显原先完全依赖 SSE "steer" 事件铺卡片——SSE 掉线窗口内
          // 插话无任何本地显示（看起来被吞）。这里收到受理回执即乐观铺进
          // 流水；SSE 事件先到（引擎在响应前同步发布）则查重跳过，后到则
          // 由 handleEvent 给无 id 的同文本项补 id 收编
          set(s => (s.live
            && !s.live.items.some(it => it.kind === 'steer' && it.text === text)
            ? { live: { ...s.live,
                        items: [...s.live.items, { kind: 'steer', text } as StreamItem] } }
            : {}));
          return;   // SSE "steer" 事件负责补 id / 回放去重
        }
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
    // 防重复触发：同 turn 停止请求在途（引擎尚未确认停止）时吞掉后续点击。
    // zustand set 同步生效——双击的第二次点击也读得到标志，store 层守卫
    // 先于按钮 disabled 生效，双保险
    if (get().stoppingTids[tid] != null) return;
    const sid = get().currentSid;
    set(s => ({ stoppingTids: { ...s.stoppingTids, [tid]: Date.now() } }));
    try {
      await api(`/api/turns/${tid}/stop`, { method: 'POST' });
    } catch (e) {
      // 请求未送达/被拒：立即解除禁用（引擎侧多半没收到），允许重试
      clearStopping(set, tid);
      alert(`停止失败：${e instanceof Error ? e.message : e}`);
      throw e;
    }
    // POST ok 只代表引擎已受理。真正的「已停止」确认走两条路：
    // ① SSE 终态事件（turn_stopped/done/error → handleEvent 清 live 与本
    //    标志，按钮随 live 消失）；② SSE 掉线时 pollStopConfirmed 轮询
    // 兜底，查到 turn 终态即确认。确认前按钮保持禁用（loading 态）。
    void pollStopConfirmed(get, set, sid, tid);
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
    const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[];
                          artifacts: ArtifactInfo[]; subtasks: SubtaskInfo[] }>(
      `/api/sessions/${encodeURIComponent(sid)}`);
    if (get().currentSid === sid) {
      set({ messages: applyServerWindow(sid, d.messages, d.turns),
            turns: mergeServerTurns(sid, d.turns),
            artifacts: d.artifacts,
            agents: deriveAgents(d.messages, d.turns),
            subtasks: d.subtasks ?? [] });
    }
    void get().loadSessions();
  },

  async retractSteer(sid, steerId) {
    // 撤回未注入的插话：成功由 steer_retracted SSE 事件摘条，这里只报错
    try {
      await api(`/api/sessions/${encodeURIComponent(sid)}/steer/retract`,
        { method: 'POST', body: JSON.stringify({ id: steerId }) });
      return true;
    } catch (e) {
      toast(String(e), false);
      return false;
    }
  },

  async deleteMessage(mid) {
    // 删单条气泡（展示层）：后端删 messages 行 + resync 拉全量
    await api(`/api/messages/${mid}`, { method: 'DELETE' });
    const sid = get().currentSid;
    if (!sid) return;
    const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[] }>(
      `/api/sessions/${encodeURIComponent(sid)}`);
    if (get().currentSid === sid) {
      set({ messages: applyServerWindow(sid, d.messages, d.turns) });
    }
  },

  async editMessage(mid, text) {
    // 编辑单条气泡文本（展示层修正）
    await api(`/api/messages/${mid}`, {
      method: 'PUT', body: JSON.stringify({ text }) });
    const sid = get().currentSid;
    if (!sid) return;
    const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[] }>(
      `/api/sessions/${encodeURIComponent(sid)}`);
    if (get().currentSid === sid) {
      set({ messages: applyServerWindow(sid, d.messages, d.turns) });
    }
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

// ============ 在途乐观发送登记（「吞消息」修复核心） ============
// 根因：sendMessage 的乐观消息只活在 messages 数组里，而 POST 落库前后
// 有多路「服务端快照全量覆盖 messages」会落地（resync 前台恢复 /
// turn 终态 300ms 后回填 / turn_deleted 回填 / openSession 重进 / SSE
// resync 事件 / 撤回·删改气泡回填）。快照在服务端落库**前**读出时，
// 覆盖落地即把乐观消息抹掉；POST 成功回调再按 optimisticId map 找不
// 到目标 → 消息从 UI 消失，直到该 turn 终态才被下次全量拉回——长任务
// 期间用户看到的就是「刚发的消息被吞了」。
// 修复：乐观消息同时登记到本表（按 sid），所有全量覆盖路径统一经
// mergeServerMsgs 合并——未落定（POST 在途）无条件合回；落定后凭
// turn_id 与快照对账，服务端副本出现即清除。

/** 乐观消息 id 发号器：页内单调递减（-1,-2,…）。原 -Date.now() 同毫秒
 *  连续发送会撞 id——成功回调 map 回填错条目 + React key 冲突 */
let _optSeq = 0;

interface InflightSend {
  msg: MessageInfo;        // 乐观消息（负 id；落定后同步回填 turn_id）
  turnId: number | null;   // POST 落定后回填；null=响应未回
  turn?: TurnInfo | null;  // POST 回执的 turn 行（陈旧快照替换 turns 时合回）
  settledAt: number;       // 登记回填时刻（老化清理锚点）
}

const inflightSends = new Map<string, InflightSend[]>();

function registerInflight(sid: string, msg: MessageInfo): void {
  const arr = inflightSends.get(sid) ?? [];
  arr.push({ msg, turnId: null, settledAt: Date.now() });
  inflightSends.set(sid, arr);
}

/** POST 落定：回填 turnId 与 turn 行（后续全量覆盖按 turn 对账） */
function settleInflight(sid: string, optimisticId: number, turnId: number,
                        turn?: TurnInfo | null): void {
  for (const it of inflightSends.get(sid) ?? []) {
    if (it.msg.id === optimisticId) {
      it.turnId = turnId;
      it.turn = turn ?? null;
      it.settledAt = Date.now();
      if (it.msg.turn_id == null) it.msg = { ...it.msg, turn_id: turnId };
    }
  }
}

/** 发送失败 / 排队消息被撤回：登记清除 */
function dropInflight(sid: string, optimisticId: number): void {
  const arr = inflightSends.get(sid);
  if (!arr) return;
  const next = arr.filter(it => it.msg.id !== optimisticId);
  if (next.length) inflightSends.set(sid, next);
  else inflightSends.delete(sid);
}

/** turn_deleted（本 tab 或别处撤回）：按 turn 清登记，防乐观项复活成鬼条 */
function dropInflightByTurn(sid: string, turnId: number): void {
  const arr = inflightSends.get(sid);
  if (!arr) return;
  const next = arr.filter(it => it.turnId !== turnId);
  if (next.length) inflightSends.set(sid, next);
  else inflightSends.delete(sid);
}

/** 最近被撤回的 turn id。撤回事件可能先于 POST 响应到达——登记项尚未
 *  回填 turnId，dropInflightByTurn 杀不掉它；等 sendMessage 落定时凭
 *  此表补杀（POST 回执里的 turn 已被删除 → 不回填、不复活）。
 *  有界：超限先出（撤回后 POST 失败不落定的残余自然淘汰） */
const retractedTurns = new Set<number>();
function rememberRetractedTurn(tid: number): void {
  retractedTurns.add(tid);
  if (retractedTurns.size > 512) {
    const first = retractedTurns.values().next();
    if (!first.done) retractedTurns.delete(first.value);
  }
}

/** PERF-4 窗口合并：服务端快照只有尾窗（详情/resync/终态回填 100 条），
 *  本地已「加载更早」前插的段（id < 快照首条）不属快照管辖——原样保留
 *  拼接，防刷新路径把已加载历史截断回尾窗。所有同 sid 快照落地点统一
 *  走这里（再交 mergeServerMsgs 做乐观合并）。head 与快照 id 严格不交
 *  （< 快照首条），无重复；乐观消息恒在尾部，不受影响。 */
function applyServerWindow(sid: string, incoming: MessageInfo[],
                           turns?: TurnInfo[]): MessageInfo[] {
  const st = useStore.getState();
  if (st.currentSid !== sid || !st.messages.length || !incoming.length)
    return mergeServerMsgs(sid, incoming, turns);
  const firstId = incoming[0].id;
  const head = st.messages.filter(m => m.id < firstId);
  return head.length
    ? mergeServerMsgs(sid, [...head, ...incoming], turns)
    : mergeServerMsgs(sid, incoming, turns);
}

/** messages 全量替换的统一入口：服务端快照 + 仍无着落的乐观消息合并。
 *  - 未落定（POST 在途）：无条件合回——任何快照都不可能已含它
 *  - 已落定：快照含同 turn_id 消息 → 服务端副本接管（登记对账清除）；
 *    快照 turns 里出现比它新的 turn 却没有它 → 已被撤回（快照读取晚于
 *    本 turn 创建，不可能是陈旧快照漏读——turn id 全局自增），不复活；
 *    超过 10 分钟 → 老化清除（任何后续快照必然已含服务端副本）
 *  合回项追加在快照尾部：它们都是刚发出的最新消息，天然有序 */
function mergeServerMsgs(sid: string, incoming: MessageInfo[],
                         turns?: TurnInfo[]): MessageInfo[] {
  const pend = inflightSends.get(sid);
  if (!pend?.length) return incoming;
  const seenTurns = new Set(incoming.map(m => m.turn_id)
    .filter((x): x is number => x != null));
  const keep: InflightSend[] = [];
  const tail: MessageInfo[] = [];
  for (const it of pend) {
    const tid = it.turnId;
    if (tid != null && (seenTurns.has(tid)
        || (turns != null && turns.some(t => t.id > tid))
        || Date.now() - it.settledAt > 600_000)) continue;
    keep.push(it);
    tail.push(it.msg);
  }
  if (keep.length !== pend.length) {
    if (keep.length) inflightSends.set(sid, keep);
    else inflightSends.delete(sid);
  }
  return tail.length ? [...incoming, ...tail] : incoming;
}

/** turns 全量替换的统一入口（与 mergeServerMsgs 成对使用，先调它清登记）：
 *  陈旧快照（读取早于本 turn 落库）不含刚发出的 turn 行 → 排队区靠
 *  `messages.turn_id + turns 行` 双条件识别会失配——把登记里已落定、
 *  快照缺席的 turn 行合回尾部 */
function mergeServerTurns(sid: string, incoming: TurnInfo[]): TurnInfo[] {
  const pend = inflightSends.get(sid);
  if (!pend?.length) return incoming;
  const ids = new Set(incoming.map(t => t.id));
  const tail = pend.filter(it => it.turnId != null && it.turn && !ids.has(it.turnId))
    .map(it => it.turn!);
  return tail.length ? [...incoming, ...tail] : incoming;
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
                    text: string, delta?: boolean,
                    attrs?: { agent_id?: string | null; agent_n?: number | null;
                              agent_name?: string | null; cum?: number | null }): StreamItem[] {
  if (delta && items.length) {
    const last = items[items.length - 1];
    // 归属一致才合并：主控 delta 不能拼到子代理聚合卡尾（反之亦然）
    if (last.kind === kind && (last as ThinkItem).agent_n === (attrs?.agent_n ?? undefined)) {
      return [...items.slice(0, -1), { ...last, text: last.text + text }];
    }
  }
  return [...items, { kind, text, ...attrs } as StreamItem];
}

/** resync 冷却（module 级）：focus 与 visibilitychange 常成对触发，5s 内去重 */
let _lastResyncAt = 0;

/** 清除 turn 的在途停止标志（SSE 终态确认 / 轮询兜底 / 失败重试共用；幂等） */
function clearStopping(set: (fn: (s: Store) => Partial<Store>) => void, tid: number) {
  set(s => {
    if (s.stoppingTids[tid] == null) return {};
    const next = { ...s.stoppingTids };
    delete next[tid];
    return { stoppingTids: next };
  });
}

/** 停止受理后的「已停止」确认兜底。正常路径 SSE turn_stopped 先到（handleEvent
 *  已清标志），轮询开头即检测退出；SSE 断线时它是唯一确认通道——每 2s 查
 *  会话详情，turn 不再 running/queued 即引擎已收尾：清标志（按钮解除禁用，
 *  通常随 live 清空直接消失），SSE 没来导致 live 仍挂在已停 turn 上的一并清。
 *  30s 仍非终态（引擎卡在长工具调用里收不了尾）也解除禁用——用户可再点
 *  停止（重发 stop 对引擎幂等），不能把按钮永久锁死。 */
async function pollStopConfirmed(
  get: () => Store,
  set: (fn: (s: Store) => Partial<Store>) => void,
  sid: string | null, tid: number,
) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2000));
    if (get().stoppingTids[tid] == null) return;   // SSE 终态事件已确认
    if (!sid) continue;
    let terminal = false;
    try {
      const d = await api<{ turns: TurnInfo[] }>(`/api/sessions/${encodeURIComponent(sid)}`);
      const t = d.turns.find(x => x.id === tid);
      terminal = !!t && t.status !== 'running' && t.status !== 'queued';
    } catch { /* 网络抖动：等下一轮 */ }
    if (terminal) {
      set(s => {
        const patch: Partial<Store> = {};
        if (s.stoppingTids[tid] != null) {
          const next = { ...s.stoppingTids };
          delete next[tid];
          patch.stoppingTids = next;
        }
        if (s.live && s.live.turnId === tid) patch.live = null;
        return patch;
      });
      return;
    }
  }
  clearStopping(set, tid);   // 超时兜底：恢复可点击（允许再次停止）
}

function handleEvent(
  set: (fn: (s: Store) => Partial<Store>) => void,
  get: () => Store, sid: string, type: string, data: any,
) {
  // 六轮修 A3：sid='*' 是广播（egress 决策流水）——跳过会话匹配直入分发
  if (get().currentSid !== sid && sid !== '*') return;
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
        ? { live: { ...s.live, items: appendText(s.live.items, 'text', data.text, data.delta,
            // 子代理文字块带 agent 归属（与 thinking 同构）：无归属=主控正文
            data.agent_n != null
              ? { agent_id: data.agent_id, agent_n: data.agent_n,
                  agent_name: data.agent_name }
              : undefined) } } : {}));
      break;
    case 'thinking':
      set(s => (s.live && s.live.turnId === data.turn_id
        ? { live: { ...s.live, items: appendText(s.live.items, 'thinking', data.text, data.delta,
            data.agent_n != null
              ? { agent_id: data.agent_id, agent_n: data.agent_n,
                  agent_name: data.agent_name, cum: data.cum }
              : undefined) } } : {}));
      break;
    case 'tool_use':
      set(s => {
        // 双连接/回放窗口双投去重：同 id 工具卡已入列则整体跳过
        // （agents 统计也不重复计）
        if (s.live && s.live.turnId === data.turn_id
            && s.live.items.some(it => it.kind === 'tool' && it.id === data.id)) return {};
        const agents = applyTool(s.agents, data.turn_id, data);
        const live = s.live && s.live.turnId === data.turn_id
          ? { live: { ...s.live, items: [...s.live.items, {
              kind: 'tool', id: data.id, name: data.name, brief: data.brief, input: data.input,
              agent_id: data.agent_id, agent_name: data.agent_name,
              agent_n: data.agent_n, agent_role: data.agent_role,
              subagent_type: data.subagent_type } as ToolItem] } }
          : {};
        return agents === s.agents ? live : { ...live, agents };
      });
      break;
    case 'tool_result':
      set(s => {
        const agents = applyResult(s.agents, data.turn_id, data.id, !!data.is_error);
        if (!s.live || s.live.turnId !== data.turn_id) {
          return agents === s.agents ? {} : { agents };
        }
        const items = s.live.items.map(it =>
          it.kind === 'tool' && it.id === data.id
            ? { ...it, is_error: data.is_error, result: data.result }
            : it);
        return { live: { ...s.live, items },
                 ...(agents === s.agents ? {} : { agents }) };
      });
      break;
    case 'todos':
      set(s => (s.live && s.live.turnId === data.turn_id
        ? { live: { ...s.live, todos: data.todos } } : {}));
      break;
    case 'steer':
      // 运行中插话已投递：铺进流水（下一轮 LLM 调用前注入上下文）。
      // id=撤回凭据（POST /steer/retract，注入前有效）+回放/双连接去重键。
      // steerOrSend 收到受理回执会先乐观铺一条无 id 的同文本项（SSE 掉线
      // 时插话才有本地回显）——事件后到则给它补 id 收编，不另起一条
      set(s => {
        if (!s.live || s.live.turnId !== data.turn_id) return {};
        if (s.live.items.some(it => it.kind === 'steer' && it.id === data.id)) return {};
        const idx = s.live.items.findIndex(
          it => it.kind === 'steer' && it.id == null && it.text === data.text);
        if (idx >= 0) {
          const items = [...s.live.items];
          const twin = items[idx] as { kind: 'steer'; text: string; id?: string };
          items[idx] = { ...twin, id: data.id };
          return { live: { ...s.live, items } };
        }
        return { live: { ...s.live, items: [...s.live.items, { kind: 'steer', text: data.text, id: data.id }] } };
      });
      break;
    case 'steer_retracted':
      // 插话已撤回：流水里摘除该条（等同没说过）
      set(s => (s.live && s.live.turnId === data.turn_id
        ? { live: { ...s.live, items: s.live.items.filter(
            it => !(it.kind === 'steer' && it.id === data.id)) } } : {}));
      break;
    case 'turn_done':
    case 'turn_error':
    case 'turn_stopped':
      // 引擎终态确认（停止请求的权威回执）：清该 turn 在途停止标志——
      // pollStopConfirmed 据此退出，按钮若仍在也解除禁用
      clearStopping(set, data.turn_id);
      // 终态后拉全量（messages 落库 + turns + artifacts）。注意：重进会话时 SSE 会
      // 回放历史事件——旧 turn 的终态事件不许清掉当前活跃 turn 的 live（实测：
      // 上一轮 turn_done 把新一轮刚重建的运行条/停止按钮一起抹掉）
      (async () => {
        await new Promise(r => setTimeout(r, 300));
        const sid2 = get().currentSid;
        if (sid2 !== sid) return;
        const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[];
                              artifacts: ArtifactInfo[]; subtasks: SubtaskInfo[] }>(
          `/api/sessions/${encodeURIComponent(sid)}`);
        const cur = get().live;
        // 旧 turn 的终态：不清当前活跃 turn 的 live；自己的终态：清（消息已落库渲染）
        set(() => ({ messages: applyServerWindow(sid, d.messages, d.turns),
                     turns: mergeServerTurns(sid, d.turns), artifacts: d.artifacts,
                     agents: deriveAgents(d.messages, d.turns),
                     subtasks: d.subtasks ?? [],
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
    case 'job_fired': {
      // 六轮修 A4：定时触发即时反馈——会话列表/next_wake 徽章刷新
      void get().loadSessions();
      break;
    }
    case 'interrupted_salvaged': {
      // 六轮修 A4：重启补记账落库——重开当前会话拉全量让
      // 「⚠️ 服务重启中断」的部分输出可见
      void get().openSession(get().currentSid!);
      break;
    }
    case 'turn_deleted':
      // 排队消息被撤回（本标签页或别处操作）→ 拉全量同步，乐观消息一并消失。
      // 先同步清在途登记：被撤回的正是刚发出的消息时，防 merge 把乐观项复活；
      // 撤回早于 POST 响应（登记未回填 turnId）的情形由 rememberRetractedTurn
      // 记下，sendMessage 落定时补杀
      rememberRetractedTurn(data.turn_id);
      dropInflightByTurn(sid, data.turn_id);
      (async () => {
        const sid2 = get().currentSid;
        if (sid2 !== sid) return;
        const d = await api<{ messages: MessageInfo[]; turns: TurnInfo[];
                              artifacts: ArtifactInfo[]; subtasks: SubtaskInfo[] }>(
          `/api/sessions/${encodeURIComponent(sid)}`);
        if (get().currentSid === sid) {
          set(() => ({ messages: applyServerWindow(sid, d.messages, d.turns),
                       turns: mergeServerTurns(sid, d.turns), artifacts: d.artifacts }));
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
      set(() => ({ messages: mergeServerMsgs(sid, data.messages ?? [], data.turns),
                   turns: mergeServerTurns(sid, data.turns ?? []), artifacts: [],
                   agents: deriveAgents(data.messages ?? [], data.turns ?? []),
                   subtasks: data.subtasks ?? [] }));
      break;
    case 'subtask':
      // 分类器打标：subtasks 全量替换 + turns 局部 patch（turn_queued 重拉的
      // turns 无 subtask_id，不 patch 则 live 期间子任务 tab 漏成员）
      set(s => ({ subtasks: data.subtasks ?? s.subtasks,
        turns: s.turns.map(t => t.id === data.turn_id
          ? { ...t, subtask_id: data.subtask_id ?? null } : t) }));
      break;
    default:
      break;
  }
}
