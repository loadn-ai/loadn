// API client：统一 token 注入与错误处理
// W0（2026-09-22）：token 走自定义头（X-Loadn-Token / X-Loadn-Admin），
// 跨域简单请求带不了自定义头 → CSRF 免疫。?token= URL 参数仅作首次引导
// （读后写 localStorage 并从地址栏清除）。SSE 走一次性 ticket。
const BASE = '';

// 键名迁移（workdaddy→loadn）：读侧双取（旧键活体迁移），写侧只写新键
const TOKEN_KEY = 'loadn_token';
const TOKEN_KEY_LEGACY = 'wd_token';

function bootstrapTokenFromUrl(): void {
  const t = new URLSearchParams(location.search).get('token');
  if (!t) return;
  localStorage.setItem(TOKEN_KEY, t);
  const u = new URL(location.href);
  u.searchParams.delete('token');
  history.replaceState(null, '', u.pathname + (u.search || '') + (u.hash || ''));
}

export function token(): string {
  bootstrapTokenFromUrl();
  const t = localStorage.getItem(TOKEN_KEY);
  if (t) return t;
  const legacy = localStorage.getItem(TOKEN_KEY_LEGACY);   // 旧键活体迁移
  if (legacy) {
    localStorage.setItem(TOKEN_KEY, legacy);
    localStorage.removeItem(TOKEN_KEY_LEGACY);
    return legacy;
  }
  return '';
}

export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t);
}

function authHeaders(): Record<string, string> {
  const t = token();
  // 双头：普通面 token + 管理面 admin（默认同值；服务端 admin 可独立配置）
  return t ? { 'X-Loadn-Token': t, 'X-Loadn-Admin': t } : {};
}

/** 无法带自定义头的场景（<a href> 下载 / <img src>）退回 ?token= 查询串 */
export function withToken(url: string): string {
  const t = token();
  if (!t) return url;
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t);
}

/** 401 时通知全局（App 层弹 token 输入框），避免每个调用点各自处理 */
function notifyUnauthorized(): void {
  window.dispatchEvent(new CustomEvent('wd-unauthorized'));
}

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  // FormData 时不能手动设 Content-Type（浏览器要自己生成 boundary）
  const isForm = init?.body instanceof FormData;
  const resp = await fetch(BASE + path, {
    ...init,
    headers: {
      ...authHeaders(),
      ...(isForm ? {} : { 'Content-Type': 'application/json' }),
      ...(init?.headers as Record<string, string> ?? {}),
    },
  });
  if (resp.status === 401) {
    notifyUnauthorized();
  } else if (resp.status === 403) {
    // 仅 admin-required 的 403 是认证问题（token 缺失/轮换）；其余 403
    // （熔断/kill switch/canary）是业务拒绝，各有场景内文案
    const body = await resp.clone().text().catch(() => '');
    if (body.includes('admin required')) notifyUnauthorized();
  }
  if (!resp.ok) {
    let msg = `${resp.status}`;
    try {
      const j = await resp.json();
      // FastAPI HTTPException 用 detail，自有错误用 error；422 的 detail 是数组
      const d = j.error ?? j.detail;
      msg = typeof d === 'string' ? d : d?.[0]?.msg ?? d?.msg ?? msg;
    } catch { /* ignore */ }
    throw new Error(`${resp.status}：${msg}`);
  }
  return resp.json();
}

/** 文件上传专用：multipart（field 名 file） */
export async function apiUpload<T = any>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append('file', file);
  return api<T>(path, { method: 'POST', body: fd });
}

// ---------------------------------------------------------------- SSE（W0.3）
// EventSource 带不了自定义头：先 fetch 取一次性 ticket（30s single-use），
// 再连 ?ticket=。401/断流时由调用方触发重连（每次重连重新取票）。
export async function fetchSseTicket(): Promise<string> {
  const d = await api<{ ticket: string; expires_in: number }>('/api/sse-ticket');
  return d.ticket;
}

export async function connectSse(
  sid: string,
  onEvent: (type: string, data: any) => void,
  onOpen: () => void,
  onDown: () => void,
  shouldContinue: () => boolean,
): Promise<EventSource> {
  const events = ['turn_queued', 'turn_started', 'text', 'thinking', 'tool_use', 'tool_result',
    'todos', 'files', 'steer', 'turn_done', 'turn_error', 'turn_stopped', 'turn_deleted',
    'session_rotated', 'session_meta', 'resync', 'egress', 'ping'];
  let backoff = 1000;
  // 断点续传游标：手动重建的 EventSource 不带浏览器内建的 Last-Event-ID
  // 状态——不带上次见到的 eid，服务端每次重连都精准回放活跃 turn 尾部，
  // 叠进已积累的流水=「重复输出四五次」的根因。服务端通道现成：
  // ?last_event_id=（sse.py 读 query），事件 id 全局自增且落库跨重启稳定。
  let lastId = 0;

  const open = async (): Promise<EventSource> => {
    let url = `/api/sessions/${encodeURIComponent(sid)}/events`;
    try {
      const t = token();
      if (t) {
        const d = await api<{ ticket: string }>('/api/sse-ticket');
        url += `?ticket=${encodeURIComponent(d.ticket)}`;
      }
    } catch { /* 票取不到（宽限期无 token 或旧服务）：裸连兜底 */ }
    if (lastId > 0) {
      url += `${url.includes('?') ? '&' : '?'}last_event_id=${lastId}`;
    }
    const es = new EventSource(url);
    es.onopen = () => { backoff = 1000; onOpen(); };
    es.onerror = () => {
      onDown();
      es.close();
      if (!shouldContinue()) return;
      // ticket 是 single-use：EventSource 内建重连会复用过期票 → 必须重建
      setTimeout(() => { if (shouldContinue()) void open(); }, backoff);
      backoff = Math.min(backoff * 2, 30000);
    };
    const handler = (ev: MessageEvent) => {
      const eid = Number(ev.lastEventId || 0);
      if (eid && eid <= lastId) return;      // 双保险：续传窗内的重复帧丢弃
      if (eid > lastId) lastId = eid;
      let data: any = {};
      try { data = JSON.parse(ev.data); } catch { /* ignore */ }
      onEvent(ev.type, data);
    };
    es.onmessage = () => { /* default 事件忽略 */ };
    for (const t of events) es.addEventListener(t, handler as EventListener);
    return es;
  };
  return open();
}

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return '-';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return Math.round(n / 1e3) + 'K';
  return String(n);
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}
