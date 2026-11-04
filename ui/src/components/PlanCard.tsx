import { useStore } from '../stores/sessions';

/** 规划模式计划卡（rev22）：plan 轮产出的结构化计划待裁决——批准执行 /
 * 放弃；修订不走按钮（直接发新消息即修订，平台会再进 plan 轮）。
 * Claude Code plan mode 的平台化：无头环境里「交互确认」= 异步卡片。 */
export default function PlanCard() {
  const plan = useStore(s => s.plan);
  const decidePlan = useStore(s => s.decidePlan);
  const busy = useStore(s => !!s.live);
  if (!plan) return null;
  const fallback = !plan.steps.length && !!plan.raw;
  return (
    <div className="plan-card">
      <div className="plan-head">
        <span className="plan-badge">📋 实施计划{plan.revision > 1 ? ` · v${plan.revision}` : ''}</span>
        <span className="plan-note">agent 已完成规划（只读探索，未动任何文件）——批准后才开始执行</span>
      </div>
      {plan.goal && <div className="plan-goal">{plan.goal}</div>}
      {!!plan.steps.length && (
        <ol className="plan-steps">
          {plan.steps.map((s, i) => <li key={i}>{s}</li>)}
        </ol>
      )}
      {!!plan.files.length && (
        <div className="plan-row"><b>涉及文件/资源</b>{plan.files.join('；')}</div>
      )}
      {!!plan.risks.length && (
        <div className="plan-row"><b>风险与对策</b>{plan.risks.join('；')}</div>
      )}
      {!!plan.questions.length && (
        <div className="plan-row plan-q"><b>待你确认</b>{plan.questions.join('；')}</div>
      )}
      {fallback && (
        <details className="plan-raw"><summary>本轮规划原文（模型未按结构输出）</summary>
          <pre>{plan.raw}</pre></details>
      )}
      <div className="plan-ops">
        <button className="btn primary" disabled={busy}
                onClick={() => void decidePlan('approve')}>
          ✓ 批准并开始执行
        </button>
        <button className="btn ghost" disabled={busy}
                onClick={() => void decidePlan('abort')}>放弃</button>
        <span className="plan-hint">要改计划？直接在输入框发修改意见——agent 会重新规划</span>
      </div>
    </div>
  );
}
