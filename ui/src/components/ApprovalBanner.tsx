import { useEffect, useState } from 'react';
import { useStore } from '../stores/sessions';

/** W1-2 审批卡片（会话顶部横幅）：pending → 批准/拒绝；批准显示一次性 6 位码。
 *  summary 由平台从参数渲染（agent 的 note 弱化展示）——防摘要伪造。 */
export default function ApprovalBanner() {
  const approvals = useStore(s => s.approvals);
  const decideApproval = useStore(s => s.decideApproval);
  const loadApprovals = useStore(s => s.loadApprovals);
  const [codes, setCodes] = useState<Record<number, string>>({});

  useEffect(() => { void loadApprovals(); }, [loadApprovals]);

  const pending = approvals.filter(a => a.status === 'pending');
  if (!pending.length) return null;
  return (
    <div className="approval-banner">
      {pending.map(a => (
        <div key={a.id} className="approval-card">
          <div className="approval-head">
            <span className="approval-badge">需确认 · {a.action_type}</span>
            <span className="approval-summary">{a.summary}</span>
          </div>
          {a.agent_note ? (
            <div className="approval-note">agent 说明（不构成授权）：{a.agent_note}</div>
          ) : null}
          <div className="approval-actions">
            <button className="btn primary sm"
              onClick={async () => {
                const code = await decideApproval(a.id, true);
                if (code) setCodes(c => ({ ...c, [a.id]: code }));
              }}>批准</button>
            <button className="btn sm"
              onClick={() => void decideApproval(a.id, false)}>拒绝</button>
          </div>
          {codes[a.id] ? (
            <div className="approval-code">
              确认码 <b>{codes[a.id]}</b>（一次性）——请把此码发给 agent 执行；
              10 分钟内有效
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
