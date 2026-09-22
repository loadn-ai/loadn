// 成本分析（管理中心的「成本」tab）：三口径（CLI 假价 / z.ai API 真价 /
// Coding Plan 积分）+ 模型/接口维度。数据一次拉 /api/stats/cost（挂载/手动刷新时），
// 不进 5s 轮询
import { useEffect, useState } from 'react';
import { api, fmtTokens } from '../api/client';
import { useStore } from '../stores/sessions';
import { RotateCw, Spinner } from './icons';

interface ModelRow {
  model: string; norm: string; assumed: boolean; turns: number;
  input: number; output: number; cache_read: number; cache_write: number;
  web_search_requests: number; cost_api_usd: number; plan_credits: number;
}
interface DayRow { day: string; turns: number; tokens: number; cost_cli_usd: number; cost_api_usd: number }
interface ProfileRow { profile: string; turns: number; tokens: number; cost_cli_usd: number; cost_api_usd: number }
interface SessionRow { sid: string; title: string; profile: string; turns: number; tokens: number; cost_cli_usd: number; cost_api_usd: number }
interface ToolRow { name: string; calls: number; errors: number }
interface PriceTier { input: number; cache_read?: number; output: number; cache_input?: number }
interface CostStats {
  totals: {
    tokens: { input: number; output: number; cache_read: number; cache_write: number; total_all: number };
    cost_cli_usd: number; cost_api_usd: number; plan_credits: number;
    turns: number; sessions: number; turns_without_model: number;
  };
  by_model: ModelRow[];
  daily: DayRow[];
  by_profile: ProfileRow[];
  top_sessions: SessionRow[];
  tools: ToolRow[];
  pricing: {
    api: Record<string, PriceTier>;
    plan: Record<string, PriceTier>;
    usd_cny: number;
    default_model: string;
    cli_fallback: PriceTier;
    mcp_tool_multiplier: number;
    offpeak_discount: number;
    note: string;
  };
}

const fmtUsd = (n: number) => '$' + n.toLocaleString('en-US', { maximumFractionDigits: n >= 100 ? 0 : 2 });
const fmtCny = (n: number) => '¥' + Math.round(n).toLocaleString('en-US');

/** 内建工具（非 MCP）：claude CLI 自带 */
const BUILTIN_TOOLS = new Set(['Bash', 'Read', 'Write', 'Edit', 'WebSearch', 'WebFetch',
  'Agent', 'TaskCreate', 'TaskUpdate', 'TaskGet', 'TaskList', 'Glob', 'Grep',
  'NotebookEdit', 'TodoWrite', 'Skill', 'EnterPlanMode', 'ExitPlanMode',
  'AskUserQuestion', 'ScheduleWakeup', 'CronCreate', 'CronDelete', 'CronList']);

export default function CostTab() {
  const [data, setData] = useState<CostStats | null>(null);
  const [err, setErr] = useState('');
  const openSession = useStore(s => s.openSession);

  const reload = async () => {
    try {
      setData(await api<CostStats>('/api/stats/cost?days=30'));
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    }
  };
  useEffect(() => { void reload(); }, []);

  const openAndLeave = (sid: string) => {
    location.hash = '';
    void openSession(sid);
  };

  return (
    <div className="admin-body cost-tab">
      <div className="admin-toolbar">
        <span className="muted">三口径成本核算：CLI 假价（虚高）/ z.ai API 按量真价 / Coding Plan 积分</span>
        <button className="btn ghost sm" onClick={() => void reload()}>
          <RotateCw size={13} /> 刷新
        </button>
      </div>
      {err && <div className="admin-err">{err}</div>}
        {!data && !err && <div className="admin-msg"><Spinner /> 统计中…</div>}
        {data && <>
          {/* ---- KPI 行：三口径成本 + token 结构 ---- */}
          <div className="kpi-row">
            <div className="kpi-card accent">
              <div className="kpi-num">{fmtUsd(data.totals.cost_api_usd)}</div>
              <div className="kpi-sub">真实成本 · ≈{fmtCny(data.totals.cost_api_usd * data.pricing.usd_cny)}</div>
              <div className="kpi-sub">z.ai API 按量价口径</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-num">{Math.round(data.totals.plan_credits).toLocaleString('en-US')}</div>
              <div className="kpi-sub">Coding Plan 积分（订阅实际口径）</div>
              <div className="kpi-sub">估算 · 不含 MCP 工具增益与非高峰 5 折</div>
            </div>
            <div className="kpi-card strike">
              <div className="kpi-num">{fmtUsd(data.totals.cost_cli_usd)}</div>
              <div className="kpi-sub">CLI 计价口径（虚高）</div>
              <div className="kpi-sub">claude CLI 对未知模型按 Opus ${data.pricing.cli_fallback.input}/${data.pricing.cli_fallback.output} 计</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-num">{fmtTokens(data.totals.tokens.total_all)}</div>
              <div className="kpi-sub">总 tokens（含缓存）</div>
              <div className="kpi-sub">
                cache read {fmtTokens(data.totals.tokens.cache_read)}
                （{Math.round(data.totals.tokens.cache_read / Math.max(1, data.totals.tokens.total_all) * 100)}%）
                · in {fmtTokens(data.totals.tokens.input)} · out {fmtTokens(data.totals.tokens.output)}
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-num">{data.totals.turns}</div>
              <div className="kpi-sub">turns · {data.totals.sessions} 个任务</div>
              {data.totals.turns_without_model > 0 &&
                <div className="kpi-sub">（{data.totals.turns_without_model} 个 turn 无模型记录，按 {data.pricing.default_model} 估）</div>}
            </div>
          </div>

          {/* ---- 每日成本（真实 vs CLI 口径）---- */}
          <h4 className="admin-h4">每日成本（近 {data.daily.length} 天）</h4>
          <div className="setting-card cost-chart-card">
            <div className="cost-bars">
              {data.daily.map(d => {
                const max = Math.max(...data.daily.map(x => x.cost_cli_usd), 0.01);
                return (
                  <div key={d.day} className="cost-bar-col" title={`${d.day}
真实 ${fmtUsd(d.cost_api_usd)} · CLI ${fmtUsd(d.cost_cli_usd)}
${fmtTokens(d.tokens)} tokens · ${d.turns} turns`}>
                    <div className="cost-bar cli" style={{ height: `${Math.max(1.5, d.cost_cli_usd / max * 100)}%` }} />
                    <div className="cost-bar" style={{ height: `${Math.max(1.5, d.cost_api_usd / max * 100)}%` }} />
                    <span className="cost-bar-label">{d.day.slice(5)}</span>
                  </div>
                );
              })}
            </div>
            <div className="cost-legend">
              <span><i className="dot api" /> 真实成本</span>
              <span><i className="dot cli" /> CLI 口径（虚高）</span>
            </div>
          </div>

          {/* ---- 按模型 ---- */}
          <h4 className="admin-h4">按模型</h4>
          <div className="tbl-wrap">
            <table className="mcp-table">
              <thead><tr>
                <th>模型</th><th>turns</th><th>input</th><th>cache read</th><th>output</th>
                <th>web search</th><th>真实成本</th><th>积分</th>
              </tr></thead>
              <tbody>
                {data.by_model.map(m => (
                  <tr key={m.model}>
                    <td>
                      {m.model}
                      {m.assumed
                        ? <span className="chip warn">按默认价估</span>
                        : <span className="chip">→ {m.norm}</span>}
                    </td>
                    <td className="mono-cell">{m.turns}</td>
                    <td className="mono-cell">{fmtTokens(m.input)}</td>
                    <td className="mono-cell">{fmtTokens(m.cache_read)}</td>
                    <td className="mono-cell">{fmtTokens(m.output)}</td>
                    <td className="mono-cell">{m.web_search_requests || '-'}</td>
                    <td className="mono-cell">{fmtUsd(m.cost_api_usd)}</td>
                    <td className="mono-cell">{Math.round(m.plan_credits).toLocaleString('en-US')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---- 工具与接口调用 ---- */}
          <h4 className="admin-h4">工具 / 接口调用（Top {data.tools.length}）</h4>
          <div className="tbl-wrap">
            <table className="mcp-table">
              <thead><tr><th>接口</th><th>类型</th><th>调用次数</th><th>失败</th></tr></thead>
              <tbody>
                {data.tools.map(t => (
                  <tr key={t.name}>
                    <td className="mono-cell">{t.name}</td>
                    <td>{t.name.startsWith('mcp__')
                      ? <span className="chip mcp">MCP</span>
                      : BUILTIN_TOOLS.has(t.name) ? <span className="chip">内建</span> : '-'}</td>
                    <td className="mono-cell">{t.calls.toLocaleString('en-US')}</td>
                    <td className={`mono-cell ${t.errors ? 'err' : ''}`}>{t.errors || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---- Top 会话 ---- */}
          <h4 className="admin-h4">成本最高的任务（Top 10）</h4>
          <div className="tbl-wrap">
            <table className="mcp-table">
              <thead><tr><th>任务</th><th>角色</th><th>turns</th><th>tokens</th><th>真实成本</th><th>CLI 口径</th></tr></thead>
              <tbody>
                {data.top_sessions.map(s => (
                  <tr key={s.sid} className="clickable" onClick={() => openAndLeave(s.sid)}>
                    <td>{s.title}</td>
                    <td>{s.profile}</td>
                    <td className="mono-cell">{s.turns}</td>
                    <td className="mono-cell">{fmtTokens(s.tokens)}</td>
                    <td className="mono-cell">{fmtUsd(s.cost_api_usd)}</td>
                    <td className="mono-cell dim">{fmtUsd(s.cost_cli_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---- 单价参考 ---- */}
          <h4 className="admin-h4">单价参考（z.ai 官方价目 · {new Date().getFullYear()}-{String(new Date().getMonth() + 1).padStart(2, '0')} 调研）</h4>
          <div className="skill-grid">
            {Object.entries(data.pricing.api).map(([name, p]) => {
              const pc = data.pricing.plan[name];
              return (
                <div key={name} className="skill-card">
                  <div className="sk-head"><b>{name}</b></div>
                  <div className="kv"><span>输入</span><b>${p.input}/M</b></div>
                  <div className="kv"><span>缓存读</span><b>${p.cache_read}/M</b></div>
                  <div className="kv"><span>输出</span><b>${p.output}/M</b></div>
                  {pc && <div className="kv"><span>积分（in/cache/out）</span>
                    <b>{pc.input} / {pc.cache_input} / {pc.output}</b></div>}
                </div>
              );
            })}
            <div className="skill-card">
              <div className="sk-head"><b>订阅计费说明</b></div>
              <div className="kv"><span>MCP 工具</span><b>每次调用 ×{data.pricing.mcp_tool_multiplier} 输出乘数</b></div>
              <div className="kv"><span>非高峰</span><b>积分 {Math.round(data.pricing.offpeak_discount * 100)}%（工作日 14-18 点外）</b></div>
              <div className="kv"><span>汇率</span><b>1 USD ≈ {data.pricing.usd_cny} CNY</b></div>
            </div>
          </div>
          <p className="cost-note">{data.pricing.note}</p>
        </>}
    </div>
  );
}
