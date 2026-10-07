import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useStore } from '../stores/sessions';

/** P3-7 审批一屏三要素（ZCode app-approval-panel 信息架构）：
 *  顶部 what（平台渲染 summary + 本 turn 文件改动——P3-1 diff 摘要，
 *  无 diff 降级文件清单/「无文件改动」）、中部 why（justification /
 *  agent_note 弱化展示）、底部操作（批准/拒绝 + 一次性确认码）。
 *  summary 由平台从参数渲染（agent 不可控）——防摘要伪造不变。 */
export default function ApprovalBanner() {
  const approvals = useStore(s => s.approvals);
  const decideApproval = useStore(s => s.decideApproval);
  const loadApprovals = useStore(s => s.loadApprovals);
  const [codes, setCodes] = useState<Record<number, string>>({});
  const [alwaysMsg, setAlwaysMsg] = useState('');

  /** P10 三档窄化（七轮补 UI）：exact=仅此确切参数 / domain-action=此域名
   * +此动作类 / target=此目标全放行——先批准（策略作用于同类未来请求），
   * 再落 always 策略 */
  const alwaysAllow = async (aid: number, scope: string) => {
    try {
      const d = await api<{ ok: boolean; match?: string }>(
        `/api/admin/target-policy/from-approval/${aid}`,
        { method: 'POST', body: JSON.stringify({ scope }) });
      setAlwaysMsg(d.ok
        ? `已建常设放行策略（${scope}）——同类请求今后免审批`
        : '策略创建失败');
    } catch (e) {
      setAlwaysMsg(`策略创建失败：${e instanceof Error ? e.message : e}`);
    }
  };

  useEffect(() => { void loadApprovals(); }, [loadApprovals]);

  const pending = approvals.filter(a => a.status === 'pending');
  if (!pending.length) return null;
  return (
    <div className="approval-banner">
      {pending.map(a => (
        <div key={a.id} className="approval-card">
          {/* ① what：平台渲染摘要 + 本 turn 改动 */}
          <div className="approval-head">
            <span className="approval-badge">需确认 · {a.action_type}</span>
            <span className="approval-summary">{a.summary}</span>
          </div>
          {a.turn_changes && a.turn_changes.length > 0 ? (
            <div className="approval-diff">
              <div className="approval-sec">本 turn 文件改动</div>
              {a.turn_changes.map((d, i) => (
                <div key={i} className="diff-file"
                     title={`hash ${d.hash ?? ''}`}>{d.path}
                  {d.lines ? <span className="diff-lines">{d.lines}</span> : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="approval-diff dim">本 turn 无文件改动（非文件动作）</div>
          )}
          {/* ② why：justification（P0-4 规则文案）+ agent 弱化说明 */}
          {a.justification ? (
            <div className="approval-why">
              <span className="approval-sec">理由</span>{a.justification}
            </div>
          ) : null}
          {a.agent_note ? (
            <div className="approval-note">agent 说明（不构成授权）：{a.agent_note}</div>
          ) : null}
          {/* ③ 底部操作 */}
          <div className="approval-actions">
            <button className="btn primary sm"
              onClick={async () => {
                const code = await decideApproval(a.id, true);
                if (code) setCodes(c => ({ ...c, [a.id]: code }));
              }}>批准</button>
            <button className="btn sm"
              onClick={() => void decideApproval(a.id, false)}>拒绝</button>
            <span className="always-wrap">
              <button className="btn ghost sm"
                onClick={() => void alwaysAllow(a.id, 'exact')}
                title="仅此确切参数（action+参数指纹）今后免审批">始终允许·确切</button>
              <button className="btn ghost sm"
                onClick={() => void alwaysAllow(a.id, 'domain-action')}
                title="此域名+此动作类今后免审批（域名类动作）">·域名+动作</button>
              <button className="btn ghost sm"
                onClick={() => void alwaysAllow(a.id, 'target')}
                title="此目标全放行（域名类=host；无域名=此动作类）">·目标全放行</button>
            </span>
          </div>
          {alwaysMsg ? <div className="approval-note">{alwaysMsg}</div> : null}
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
