// 系统页（管理中心「系统」tab，AC-4.1 MISS-1）：版本与升级/运行状态/
// 调度器/备份——平台运维信息此前散在 CLI（ops/backup 命令面），管理页零可见。
// 数据：GET /api/admin/system（挂载/手动刷新）+ /api/health（engines 卡）；
// 备份 running 时 3s 轮询收敛，不进全局 5s 轮询。
import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import { toast } from '../../stores/toasts';
import { RotateCw, Spinner } from '../icons';

interface ReleaseRow {
  version: string; git_sha: string; built_at: string;
  current: boolean; previous: boolean; venv_ok: boolean;
}
interface BackupRow {
  name: string; timestamp: string; full_workspace: boolean;
  errors: string[]; complete: boolean;
}
interface SystemInfo {
  version: {
    running: { version?: string; git_sha?: string; built_at?: string; source?: string };
    current: string | null; upgrade_available: string[]; releases: ReleaseRow[];
  };
  runtime: {
    pid: number; started_at: string | null; uptime_s: number | null;
    disk: { total_gb?: number; free_gb?: number; used_pct?: number };
    db_size_mb: number;
  };
  scheduler: {
    alive: boolean; check_interval_s: number; kill_all: boolean;
    jobs: Record<string, number>; next_due_at: string | null;
  };
  backup: {
    root: string; running: boolean; kind: string; started_at: string;
    finished_at: string; rc: number | null; detail: string; recent: BackupRow[];
  };
}
interface EngineState { bin: string; version: string; ok: boolean; error?: string }

const fmtUptime = (s: number) => {
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d > 0 ? `${d} 天 ${h} 时` : h > 0 ? `${h} 时 ${m} 分` : `${m} 分`;
};

export default function SystemTab() {
  const [data, setData] = useState<SystemInfo | null>(null);
  const [engines, setEngines] = useState<Record<string, EngineState> | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');
  // AC-5.5：操作反馈走全局 toast（加载错误仍常驻 admin-err）
  const timer = useRef<number | null>(null);

  const reload = async () => {
    try {
      const d = await api<SystemInfo>('/api/admin/system');
      setData(d); setErr('');
      return d;
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
      return null;
    }
  };

  // 备份 running 时 3s 轮询（收敛后停——分钟级后台任务不值得常驻轮询）
  useEffect(() => {
    if (!data?.backup.running) {
      if (timer.current) { clearInterval(timer.current); timer.current = null; }
      return;
    }
    timer.current = window.setInterval(() => void reload(), 3000);
    return () => { if (timer.current) { clearInterval(timer.current); timer.current = null; } };
  }, [data?.backup.running]);

  useEffect(() => {
    void reload();
    void api<{ engines: Record<string, EngineState> }>('/api/health')
      .then(d => setEngines(d.engines)).catch(() => setEngines({}));
  }, []);

  const trigger = async (kind: 'backup' | 'verify', fullWorkspace = false) => {
    setBusy(kind);
    try {
      const url = kind === 'verify' ? '/api/admin/system/backup/verify' : '/api/admin/system/backup';
      await api(url, { method: 'POST', body: JSON.stringify({ full_workspace: fullWorkspace }) });
      toast(kind === 'verify' ? '验证已启动（后台进行，稍后刷新查看结果）' : '备份已启动（后台进行）');
      await reload();
    } catch (e) {
      toast(String(e instanceof Error ? e.message : e), false);
    } finally { setBusy(''); }
  };

  const b = data?.backup;
  return (
    <div className="admin-body">
      <div className="admin-toolbar">
        <span className="muted">版本与升级 · 运行状态 · 调度器 · 备份</span>
        <button className="btn ghost sm" onClick={() => void reload()}>
          <RotateCw size={13} /> 刷新
        </button>
      </div>
      {err && <div className="admin-err">{err}</div>}
      {!data && !err && <div className="admin-msg"><Spinner /> 加载中…</div>}
      {data && <>
        {/* ---- KPI 行 ---- */}
        <div className="kpi-row">
          <div className="kpi-card accent">
            <div className="kpi-num">{data.version.running.version || '未知'}</div>
            <div className="kpi-sub">运行版本{data.version.running.source === 'repo' ? '（开发仓 · 包版本）' : ''}</div>
            {data.version.running.git_sha &&
              <div className="kpi-sub">{data.version.running.git_sha.slice(0, 10)} · {data.version.running.built_at?.slice(0, 19)}</div>}
          </div>
          <div className="kpi-card">
            <div className="kpi-num">{data.runtime.uptime_s != null ? fmtUptime(data.runtime.uptime_s) : '未知'}</div>
            <div className="kpi-sub">服务运行时长 · pid {data.runtime.pid}</div>
            <div className="kpi-sub">DB {data.runtime.db_size_mb} MB</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-num">{data.runtime.disk.free_gb ?? '-'}<small> / {data.runtime.disk.total_gb ?? '-'} GB</small></div>
            <div className="kpi-sub">数据盘可用空间（已用 {data.runtime.disk.used_pct ?? '-'}%）</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-num">
              {data.scheduler.kill_all ? '已熔断' : data.scheduler.alive ? '运行中' : '未运行'}
            </div>
            <div className="kpi-sub">调度器 · 每 {data.scheduler.check_interval_s}s 扫描</div>
            <div className="kpi-sub">
              {Object.entries(data.scheduler.jobs).map(([s, n]) => `${s} ${n}`).join(' · ')
                || '无 job'}
            </div>
          </div>
        </div>

        {data.scheduler.kill_all &&
          <div className="admin-msg err">全局熔断生效中（KILL_ALL）——调度投递已暂停，可在「安全」tab 解除。</div>}

        {/* ---- 版本与升级 ---- */}
        <h4 className="admin-h4">版本与升级</h4>
        <div className="setting-card">
          {data.version.upgrade_available.length > 0 && (
            <div className="admin-msg ok" style={{ marginBottom: 8 }}>
              有可升级版本：{data.version.upgrade_available.join('、')}
              （当前部署 {data.version.current}——终端执行 loadn-ops upgrade 切换）
            </div>
          )}
          {data.version.releases.length === 0
            ? <div className="muted" style={{ fontSize: 12 }}>
                无 release 部署结构（开发仓运行）——发布/升级走终端 loadn-ops（build / upgrade / rollback）。
              </div>
            : <div className="tbl-wrap">
                <table className="mcp-table">
                  <thead><tr>
                    <th>版本</th><th>git_sha</th><th>构建时间</th><th>标记</th><th>venv</th>
                  </tr></thead>
                  <tbody>
                    {data.version.releases.map(r => (
                      <tr key={r.version}>
                        <td className="mono-cell">{r.version}</td>
                        <td className="mono-cell dim">{r.git_sha || '-'}</td>
                        <td className="mono-cell dim">{r.built_at || '-'}</td>
                        <td>{r.current && <span className="chip on">current</span>}
                            {r.previous && <span className="chip">previous</span>}
                            {!r.current && !r.previous && '-'}</td>
                        <td className={r.venv_ok ? 'mono-cell' : 'mono-cell err'}>
                          {r.venv_ok ? '✓' : '✗'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>}
        </div>

        {/* ---- 引擎 ---- */}
        <h4 className="admin-h4">引擎状态</h4>
        <div className="tbl-wrap">
          <table className="mcp-table">
            <thead><tr><th>引擎</th><th>可执行</th><th>版本</th><th>状态</th></tr></thead>
            <tbody>
              {engines == null
                ? <tr><td colSpan={4} className="muted">探测中…（各引擎 --version，最长 15s）</td></tr>
                : Object.entries(engines).map(([name, st]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td className="mono-cell dim">{st.bin}</td>
                    <td className="mono-cell dim">{st.version.slice(0, 60)}</td>
                    <td>{st.ok
                      ? <span className="chip" style={{ color: 'var(--green)' }}>正常</span>
                      : <span className="chip warn">{st.error || '不可用'}</span>}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>

        {/* ---- 备份 ---- */}
        <h4 className="admin-h4">备份</h4>
        <div className="setting-card">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {b?.running
              ? <span className="chip warn"><Spinner size={11} /> {b.kind === 'verify' ? '验证进行中…' : '备份进行中…'}（{b.started_at.slice(11, 19)} 起）</span>
              : b?.rc != null && (b.rc === 0
                ? <span className="chip" style={{ color: 'var(--green)' }}>上次 {b.kind} 成功 · {b.finished_at.slice(0, 19).replace('T', ' ')}</span>
                : <span className="chip warn">上次 {b.kind} 失败（rc={b.rc}）{b.detail && `：${b.detail}`}</span>)}
            <span style={{ flex: 1 }} />
            <button className="btn sm" disabled={!!busy || !!b?.running}
              onClick={() => void trigger('backup')}>
              {busy === 'backup' ? <Spinner size={11} /> : null} 立即备份
            </button>
            <button className="btn sm" disabled={!!busy || !!b?.running}
              onClick={() => { if (confirm('全量备份含完整 workspace（含 .git 等可重建内容，耗时长）——确认？')) void trigger('backup', true); }}>
              全量备份
            </button>
            <button className="btn sm ghost" disabled={!!busy || !!b?.running}
              onClick={() => void trigger('verify')}>
              {busy === 'verify' ? <Spinner size={11} /> : null} 验证最近备份
            </button>
          </div>
          <div className="muted" style={{ fontSize: 12, margin: '8px 0' }}>
            备份目标 {b?.root}（异盘）· 关键文件 + 主 DB（backup API）+ workspace（排除可重建目录）
          </div>
          {b && b.recent.length > 0
            ? <div className="tbl-wrap">
                <table className="mcp-table">
                  <thead><tr>
                    <th>备份</th><th>时间</th><th>类型</th><th>状态</th>
                  </tr></thead>
                  <tbody>
                    {b.recent.map(x => (
                      <tr key={x.name}>
                        <td className="mono-cell">{x.name}</td>
                        <td className="mono-cell dim">{x.timestamp.slice(0, 19).replace('T', ' ') || '-'}</td>
                        <td>{x.full_workspace ? '全量' : '选择性'}</td>
                        <td>{!x.complete
                          ? <span className="chip warn">半成品（中断）</span>
                          : x.errors.length > 0
                            ? <span className="chip warn">{x.errors.length} 项错误</span>
                            : <span className="chip" style={{ color: 'var(--green)' }}>完整</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            : <div className="muted" style={{ fontSize: 12 }}>尚无备份记录。</div>}
        </div>
      </>}
    </div>
  );
}
