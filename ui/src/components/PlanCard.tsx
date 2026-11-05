import { useEffect, useState } from 'react';
import { useStore } from '../stores/sessions';
import { ChevronDown, ChevronRight, Copy, Pencil } from './icons';
import { toast } from '../stores/toasts';

/** 规划模式计划卡（rev22 首建 / rev23 交互增强）：plan 轮产出的结构化计划
 * 待裁决。批准执行 / 放弃；修订不走按钮（直接发新消息即修订，平台会再进
 * plan 轮）。Claude Code plan mode 的平台化：无头环境里「交互确认」= 异步卡。
 *
 * rev23 卡上交互（不再只读）：
 * - 步骤：勾选纳入/剔除 + 行内编辑（批准时回传 steps 覆盖）
 * - 文件/风险：折叠展示（长计划不霸屏）
 * - 提问：逐题答复输入 + 一键「自动回复」（缺省=按计划执行），批准时随
 *   answers 注入执行 prompt——提问闭环在卡上完成，不用再发消息
 * - 整卡可折叠 + 复制计划为 markdown。 */
export default function PlanCard() {
  const plan = useStore(s => s.plan);
  const busy = useStore(s => !!s.live);
  // 卡上编辑态：steps {text, on}、answers 按题下标；plan 变更（新修订轮）重置
  const [steps, setSteps] = useState<{ text: string; on: boolean }[]>([]);
  const [answers, setAnswers] = useState<string[]>([]);
  const [editing, setEditing] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    setSteps((plan?.steps ?? []).map(text => ({ text, on: true })));
    setAnswers((plan?.questions ?? []).map(() => ''));
    setEditing(null);
    setCollapsed(false);
  }, [plan]);
  if (!plan) return null;
  const fallback = !plan.steps.length && !!plan.raw;
  const chosen = steps.filter(s => s.on && s.text.trim());
  const answeredN = answers.filter(a => a.trim()).length;

  const doApprove = () => {
    const opts: { steps?: string[]; answers?: string[] } = {};
    if (plan.steps.length) {
      if (chosen.length !== plan.steps.length || steps.some((s, i) => s.text !== plan.steps[i])) {
        opts.steps = chosen.map(s => s.text.trim());
      }
    }
    if (plan.questions.length && answeredN) opts.answers = answers;
    void useStore.getState().decidePlan('approve', opts);
  };

  const copyPlan = () => {
    const md = [
      `# 实施计划${plan.revision > 1 ? ` v${plan.revision}` : ''}`, '',
      `目标：${plan.goal || '（无）'}`, '',
      ...(chosen.length ? ['## 步骤', ...chosen.map((s, i) => `${i + 1}. ${s.text}`), ''] : []),
      ...(plan.files.length ? ['## 涉及文件/资源', plan.files.join('；'), ''] : []),
      ...(plan.risks.length ? ['## 风险与对策', plan.risks.join('；'), ''] : []),
      ...(plan.questions.length ? ['## 待确认', ...plan.questions.map(q => `- ${q}`), ''] : []),
    ].join('\n');
    void navigator.clipboard.writeText(md).then(
      () => toast('计划已复制为 Markdown'), () => toast('复制失败', false));
  };

  return (
    <div className={`plan-card${collapsed ? ' collapsed' : ''}`}>
      <div className="plan-head">
        <button className="plan-fold" title={collapsed ? '展开计划卡' : '折叠计划卡'}
                onClick={() => setCollapsed(c => !c)}>
          {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        </button>
        <span className="plan-badge">📋 实施计划{plan.revision > 1 ? ` · v${plan.revision}` : ''}</span>
        {!collapsed && <span className="plan-note">
          agent 已完成规划（只读探索，未动任何文件）——批准后才开始执行</span>}
        <span className="plan-head-ops">
          <button className="btn ghost sm" title="复制计划为 Markdown"
                  onClick={copyPlan}><Copy size={13} /></button>
        </span>
      </div>
      {!collapsed && <>
        {plan.goal && <div className="plan-goal">{plan.goal}</div>}
        {!!steps.length && (
          <div className="plan-steps-box">
            <div className="plan-sec-title">
              步骤（{chosen.length}/{steps.length} 将执行）
              <span className="plan-sec-ops">
                <button className="link" onClick={() => setSteps(ss => ss.map(s => ({ ...s, on: true })))}>全选</button>
                <button className="link" onClick={() => setSteps(ss => ss.map(s => ({ ...s, on: false })))}>全不选</button>
              </span>
            </div>
            <ol className="plan-steps">
              {steps.map((s, i) => (
                <li key={i} className={s.on ? '' : 'off'}>
                  <input type="checkbox" checked={s.on} title={s.on ? '纳入执行' : '剔除'}
                         onChange={e => setSteps(ss => ss.map((x, j) => j === i ? { ...x, on: e.target.checked } : x))} />
                  {editing === i ? (
                    <input className="plan-step-edit" autoFocus value={s.text}
                           onChange={e => setSteps(ss => ss.map((x, j) => j === i ? { ...x, text: e.target.value } : x))}
                           onKeyDown={e => { if (e.key === 'Enter' || e.key === 'Escape') setEditing(null); }}
                           onBlur={() => setEditing(null)} />
                  ) : (
                    <span className="plan-step-text"
                          onDoubleClick={() => setEditing(i)}>{s.text}</span>
                  )}
                  <button className="plan-step-edit-btn" title="编辑此步"
                          onClick={() => setEditing(editing === i ? null : i)}>
                    <Pencil size={11} />
                  </button>
                </li>
              ))}
            </ol>
            <div className="plan-edit-hint">双击步骤或点 ✎ 可改写；去勾=批准时不执行该步</div>
          </div>
        )}
        {!!plan.files.length && (
          <details className="plan-row-box"><summary><b>涉及文件/资源</b>（{plan.files.length}）</summary>
            <div className="plan-row">{plan.files.join('；')}</div></details>
        )}
        {!!plan.risks.length && (
          <details className="plan-row-box"><summary><b>风险与对策</b>（{plan.risks.length}）</summary>
            <div className="plan-row">{plan.risks.join('；')}</div></details>
        )}
        {!!plan.questions.length && (
          <div className="plan-q-box">
            <div className="plan-sec-title">
              待你确认（已答 {answeredN}/{plan.questions.length}）
              <span className="plan-sec-ops">
                <button className="link" title="全部按计划默认执行——答复随批准注入执行指令"
                        onClick={() => setAnswers(plan.questions.map(() => '按计划执行'))}>
                  自动回复全部
                </button>
              </span>
            </div>
            {plan.questions.map((q, i) => (
              <div key={i} className="plan-q-row">
                <div className="plan-q-text">{q}</div>
                <input className="plan-q-input" value={answers[i] ?? ''}
                       placeholder="留空＝按计划执行；填写则随批准注入执行指令"
                       onChange={e => setAnswers(as => as.map((a, j) => j === i ? e.target.value : a))} />
              </div>
            ))}
          </div>
        )}
        {fallback && (
          <details className="plan-raw"><summary>本轮规划原文（模型未按结构输出）</summary>
            <pre>{plan.raw}</pre></details>
        )}
        <div className="plan-ops">
          <button className="btn primary" disabled={busy || !chosen.length && !!steps.length}
                  title={!chosen.length && !!steps.length ? '请至少勾选一个步骤（或改用发消息修订）' : undefined}
                  onClick={doApprove}>
            ✓ 批准并开始执行{chosen.length && chosen.length !== plan.steps.length ? `（${chosen.length} 步）` : ''}
          </button>
          <button className="btn ghost" disabled={busy}
                  onClick={() => void useStore.getState().decidePlan('abort')}>放弃</button>
          <span className="plan-hint">要改计划？直接在输入框发修改意见——agent 会重新规划</span>
        </div>
      </>}
    </div>
  );
}
