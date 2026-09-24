import { useEffect } from 'react';
import { useStore } from '../stores/sessions';

/** P3-7 压缩时间线（ZCode app-compact-timeline 信息架构，TUI 不做）：
 *  沿会话的横向刻度条——turn 是小刻度点，compact 处 ▼ 标记（裁掉 token
 *  数），hover 看被裁摘要首行。数据源 /sessions/{sid}/timeline（transcript
 *  的 result/compact 事件只读扫描）。窄屏：等比压缩 + 横向滚动兜底。 */
export default function CompactTimeline() {
  const timeline = useStore(s => s.timeline);
  const currentSid = useStore(s => s.currentSid);

  useEffect(() => { void useStore.getState().loadTimeline(); }, [currentSid]);

  if (!currentSid || timeline.length === 0) return null;
  const compacts = timeline.filter(m => m.kind === 'compact');
  const n = timeline.length;
  return (
    <div className="compact-timeline" role="img"
         aria-label={`会话刻度：${n} 个 turn 标记，${compacts.length} 次压缩`}>
      {timeline.map((m, i) => {
        const left = ((i + 0.5) / n) * 100;
        if (m.kind === 'compact') {
          const tip = `裁掉 ~${m.tokens_cropped ?? '?'} tokens` +
            (m.summary_first ? `\n摘要：${m.summary_first}` : '');
          return (
            <span key={i} className="ct-mark compact"
                  style={{ left: `${left}%` }}
                  title={tip}>▼{m.tokens_cropped != null
                    ? Math.round(m.tokens_cropped / 1000) + 'k' : ''}</span>
          );
        }
        return <span key={i} className="ct-mark turn" style={{ left: `${left}%` }}
                     title={`turn（${m.tokens ?? 0} tokens）`} />;
      })}
    </div>
  );
}
