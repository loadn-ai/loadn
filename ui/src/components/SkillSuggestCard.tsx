import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useStore } from '../stores/sessions';

/** 七轮补 UI（设定审计#2）：P12 技能建议卡——引擎检测到用户教学/纠错后
 *  写 .loadn/skill-suggest.json（单槽 pending），此前前端零引用=用户永远
 *  看不到、无法确认/拒绝，「经验→技能固化」闭环断在决策端。
 *  确认 → 写 .agents/skills/（过供应链八类扫描）；拒绝 → 同类 7 天抑制。 */
interface SuggestCard {
  kind: string; name: string; description: string; body: string;
  fingerprint?: string;
}

export default function SkillSuggestCard() {
  const currentSid = useStore(s => s.currentSid);
  const [card, setCard] = useState<SuggestCard | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<SuggestCard | null>(null);
  const [msg, setMsg] = useState('');

  const load = async (sid: string) => {
    try {
      const d = await api<{ pending: SuggestCard | null }>(
        `/api/sessions/${encodeURIComponent(sid)}/skill-suggest`);
      setCard(d.pending);
      setDraft(d.pending ? { ...d.pending } : null);
    } catch { /* 404 等：无建议=常态 */ }
  };
  useEffect(() => {
    if (currentSid) void load(currentSid);
    else setCard(null);
  }, [currentSid]);
  // turn 终态后可能出现新建议——低频轮询兜底（SSE 无该事件类型）
  useEffect(() => {
    if (!currentSid) return;
    const t = setInterval(() => void load(currentSid), 20000);
    return () => clearInterval(t);
  }, [currentSid]);

  if (!card) return null;
  const kindZh: Record<string, string> = { teach: '教导', correct: '纠错' };
  const decide = async (accept: boolean) => {
    const payload = accept && draft ? draft : {};
    try {
      await api(
        `/api/sessions/${encodeURIComponent(currentSid!)}/skill-suggest/decide`,
        { method: 'POST', body: JSON.stringify({ accept, ...payload }) });
      setMsg(accept ? '已固化为技能（下轮可用）' : '已拒绝（同类 7 天内不再提示）');
      setCard(null);
    } catch (e) {
      setMsg(`决策失败：${e instanceof Error ? e.message : e}`);
    }
  };
  const d = editing && draft ? draft : card;
  return (
    <div className="approval-banner">
      <div className="approval-card">
        <div className="approval-head">
          <span className="approval-badge">技能建议 · {kindZh[card.kind] || card.kind}</span>
          <span className="approval-summary">
            检测到{kindZh[card.kind] === '纠错' ? '纠正' : '教学'}信号——把这次经验固化为技能？
          </span>
        </div>
        <div className="approval-why" style={{ flexDirection: 'column' }}>
          {editing ? (
            <>
              <label>技能名（小写/数字/._-）
                <input value={d.name}
                       onChange={e => setDraft({ ...d, name: e.target.value })} />
              </label>
              <label>简介（单行）
                <input value={d.description}
                       onChange={e => setDraft({ ...d, description: e.target.value })} />
              </label>
              <label>正文（用户原话+上下文，可编辑）
                <textarea rows={6} value={d.body} className="cinput"
                          onChange={e => setDraft({ ...d, body: e.target.value })} />
              </label>
            </>
          ) : (
            <>
              <div><b>{card.name}</b>——{card.description}</div>
              <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 160, overflow: 'auto' }}>
                {card.body}
              </pre>
            </>
          )}
        </div>
        <div className="approval-actions">
          <button className="btn primary sm" onClick={() => void decide(true)}>固化为技能</button>
          <button className="btn sm" onClick={() => void decide(false)}>拒绝</button>
          {!editing
            ? <button className="btn ghost sm" onClick={() => setEditing(true)}>编辑</button>
            : <button className="btn ghost sm" onClick={() => setEditing(false)}>预览</button>}
        </div>
        {msg ? <div className="approval-note">{msg}</div> : null}
      </div>
    </div>
  );
}
