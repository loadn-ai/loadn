import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api } from '../api/client';
import { useStore } from '../stores/sessions';
import { toast } from '../stores/toasts';
import { BookOpen } from './icons';

/** 七轮补 UI（设定审计#2）：P12 技能建议卡——引擎检测到用户教学/纠错后
 *  写 .loadn/skill-suggest.json（单槽 pending），此前前端零引用=用户永远
 *  看不到、无法确认/拒绝，「经验→技能固化」闭环断在决策端。
 *  确认 → 写 .agents/skills/（过供应链八类扫描）；拒绝 → 同类 7 天抑制；
 *  误判 → 仅清槽不记负样本。
 *  2026-10-10 交互优化：补生效说明/长文折叠/误判通道。
 *  2026-10-12 透明化重写（用户反馈「不知道要干啥/改什么成什么/有什么用/
 *  存哪里/怎么编辑取消，一个都不敢保留」）：四段式——①触发原因（哪句话
 *  被识别为教导，直接引用）②将写入什么（最终 SKILL.md 全文预览=所见即
 *  所得，确认前不落任何盘）③生效与管理（何时生效/作用范围/怎么编辑/
 *  怎么删除/技能页入口）④按钮后果随行说明。name 是机器 slug 不占首屏，
 *  人话 description 优先。
 *  2026-10-17 设计稿语言重构 v3（用户需求：按设计稿改 + markdown 解析）：
 *  hero 左缘渐变色条+图标方块+kind 语义徽章（教导=靛/纠错=琥珀）；正文
 *  frontmatter 行式网格 + markdown 渲染（react-markdown+GFM，与聊天流同
 *  栈）；「源码」模式保逐字落盘原文（所见即所得语义不丢）；长文折叠改
 *  段落感知（沿空行切，不撕裂语法块）。决策成功反馈改全局 toast——
 *  修隐性缺陷：旧版 setCard(null) 后组件返回 null，卡内 msg 永不可见
 *  （按钮点了个寂寞）；失败仍走卡内 msg（需常驻可读）。 */
interface SuggestCard {
  kind: string; name: string; description: string; body: string;
  fingerprint?: string; trigger?: string;
}

const PREVIEW_LIMIT = 500;    // 正文预览默认展示上限（全文可展开）

/** 触发摘录：优先 trigger 字段（2026-10-12 起），旧 pending 回退从 body
 *  提取 `> ` 引用行 */
function triggerOf(c: SuggestCard): string {
  if (c.trigger) return c.trigger;
  const m = /^> (.+)$/m.exec(c.body || '');
  return m ? m[1] : '';
}

/** 最终将写入的 SKILL.md 全文（与后端 decide 组装逐字一致——「源码」模式所见即所得） */
function skillFileText(c: SuggestCard): string {
  return `---\nname: ${c.name}\ndescription: ${c.description}\n`
    + `origin: user-taught:${c.fingerprint ?? ''}\n---\n\n${c.body}\n`;
}

/** 段落感知截断：沿空行切段累计到 limit，避免把 markdown 语法块（表格/
 *  引用/代码围栏）从中间撕开；单段超长时字符级兜底。 */
function clipBody(body: string, limit: number): { text: string; clipped: boolean } {
  if (body.length <= limit) return { text: body, clipped: false };
  const paras = body.split(/\n{2,}/);
  const out: string[] = [];
  let acc = 0;
  for (const p of paras) {
    if (out.length && acc + p.length > limit) break;
    out.push(p);
    acc += p.length + 2;
    if (acc > limit) break;
  }
  let text = out.join('\n\n');
  if (text.length > Math.floor(limit * 1.4)) text = text.slice(0, Math.floor(limit * 1.2));
  return { text, clipped: true };
}

export default function SkillSuggestCard() {
  const currentSid = useStore(s => s.currentSid);
  const [card, setCard] = useState<SuggestCard | null>(null);
  const [wsRoot, setWsRoot] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<SuggestCard | null>(null);
  const [msg, setMsg] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [mode, setMode] = useState<'preview' | 'source'>('preview');

  const load = async (sid: string) => {
    try {
      const d = await api<{ pending: SuggestCard | null; ws_root?: string }>(
        `/api/sessions/${encodeURIComponent(sid)}/skill-suggest`);
      setCard(d.pending);
      setWsRoot(d.ws_root ?? '');
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
  const decide = async (accept: boolean, misjudge = false) => {
    const payload = accept && draft ? draft : (misjudge ? { misjudge: true } : {});
    try {
      await api(
        `/api/sessions/${encodeURIComponent(currentSid!)}/skill-suggest/decide`,
        { method: 'POST', body: JSON.stringify({ accept, ...payload }) });
      const skillPath = `${wsRoot || '〈工作区〉'}/.agents/skills/${draft?.name ?? card.name}/SKILL.md`;
      toast(accept
        ? `已写入：${skillPath}（下个新会话起生效；设置 → 技能 可编辑/禁用/删除）`
        : misjudge ? '已按误判清掉这张卡（不写文件、不记录抑制）'
        : '已跳过（同类内容 7 天内不再提醒）');
      setCard(null);
    } catch (e) {
      setMsg(`决策失败：${e instanceof Error ? e.message : e}`);
    }
  };
  const d = editing && draft ? draft : card;
  const trigger = triggerOf(d);
  const body = d.body || '';
  const shown = clipBody(body, PREVIEW_LIMIT);
  const fileText = skillFileText(d);
  const skillPath = `${wsRoot || '〈工作区〉'}/.agents/skills/${d.name}/SKILL.md`;

  return (
    <div className="approval-banner">
      <div className="approval-card skill-suggest">
        {/* ① 为什么出现：hero 头（图标方块 + kind 语义徽章）+ 触发原因 + 原话引用 */}
        <div className="suggest-head">
          <span className="suggest-hicon"><BookOpen size={16} /></span>
          <div style={{ minWidth: 0 }}>
            <div className="suggest-htitle-row">
              <span className="suggest-htitle">技能建议</span>
              <span className={`suggest-hbadge ${card.kind}`}>
                {kindZh[card.kind] || card.kind}
              </span>
            </div>
            <div className="suggest-hdesc">
              这句话像是你在教我规矩——要记下来复用吗？（只在你点确认后才写文件）
            </div>
          </div>
        </div>
        {trigger && (
          <div className="suggest-quote" title="被识别为教导/纠正的原文（截断展示）">
            ❝ {trigger.length > 160 ? trigger.slice(0, 160) + '…' : trigger} ❞
          </div>
        )}

        {/* ② 将写入什么：路径 + 预览（markdown 渲染）/源码（逐字原文）双模，编辑态为表单 */}
        <div className="suggest-section">
          <div className="suggest-sec-head">
            <div className="suggest-sec-title">将写入的文件（{editing ? '编辑中' : '可先编辑'}）</div>
            {!editing && (
              <div className="suggest-seg" role="group" aria-label="预览模式">
                <button className={`suggest-seg-btn${mode === 'preview' ? ' on' : ''}`}
                        aria-pressed={mode === 'preview'} title="markdown 渲染后的效果"
                        onClick={() => setMode('preview')}>预览</button>
                <button className={`suggest-seg-btn${mode === 'source' ? ' on' : ''}`}
                        aria-pressed={mode === 'source'} title="最终落盘的原文（逐字一致）"
                        onClick={() => setMode('source')}>源码</button>
              </div>
            )}
          </div>
          {editing ? (
            <>
              <div className="suggest-path"><code>{skillPath}</code></div>
              <div className="suggest-edit">
              <label>技能名（小写/数字/._-，即目录名）
                <input value={d.name}
                       onChange={e => setDraft({ ...d, name: e.target.value })}  autoComplete="off"/>
              </label>
              <label>简介（新会话里看到的一句话说明）
                <input value={d.description}
                       onChange={e => setDraft({ ...d, description: e.target.value })}  autoComplete="off"/>
              </label>
              <label>正文（你的原话引用 + 上轮行为 + 提炼的规则，markdown）
                <textarea rows={6} value={d.body}
                          onChange={e => setDraft({ ...d, body: e.target.value })}  autoComplete="off"/>
              </label>
              </div>
            </>
          ) : mode === 'preview' ? (
            <>
              <div className="suggest-path"><code>{skillPath}</code></div>
              <div className="suggest-meta">
                <div className="suggest-meta-item"><span>name</span><code>{d.name}</code></div>
                <div className="suggest-meta-item"><span>description</span>
                  <span>{d.description}</span></div>
                <div className="suggest-meta-item"><span>origin</span>
                  <code>user-taught:{d.fingerprint ?? ''}</code></div>
              </div>
              <div className="md suggest-md" style={{ maxHeight: expanded ? 440 : 170 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {expanded ? body : shown.text}
                </ReactMarkdown>
              </div>
              {shown.clipped ? (
                <button className="btn ghost sm" style={{ alignSelf: 'flex-start' }}
                        onClick={() => setExpanded(v => !v)}>
                  {expanded ? '收起' : `展开全文（${body.length} 字）`}
                </button>
              ) : null}
            </>
          ) : (
            <>
              <div className="suggest-path"><code>{skillPath}</code></div>
              <pre className="suggest-preview" style={{ maxHeight: expanded ? 440 : 170 }}>
                {fileText}
              </pre>
            </>
          )}
          <div className="suggest-note-line">
            预览 = markdown 渲染效果；源码 = 逐字落盘原文 ｜ 另存一份到平台技能库（跨会话共享）；内容会先过供应链安全扫描
          </div>
        </div>

        {/* ③ 生效与管理：作用范围 + 编辑/取消路径 */}
        <div className="suggest-section">
          <div className="suggest-sec-title">确认后会怎样</div>
          <ul className="suggest-facts">
            <li>仅<b>本工作区</b>的<b>新会话</b>自动加载这条技能（当前会话不受影响）</li>
            <li>随时可改：直接编辑上面这个文件，或到 <b>设置 → 技能</b> 页编辑/禁用/删除</li>
            <li>不想要了就删掉该目录（或技能页删除），立即失效</li>
          </ul>
        </div>

        {/* ④ 决策按钮 + 后果随行 */}
        <div className="approval-actions">
          <button className="btn primary sm" title="按预览内容写入上述文件，新会话生效"
                  onClick={() => void decide(true)}>确认写入</button>
          <button className="btn sm" title="本次不写；同类内容 7 天内不再提醒"
                  onClick={() => void decide(false)}>跳过</button>
          <button className="btn ghost sm"
                  title="清掉这张卡，不写文件、不记录任何抑制"
                  onClick={() => void decide(false, true)}>这不是教导</button>
          {!editing
            ? <button className="btn ghost sm" onClick={() => setEditing(true)}>编辑内容</button>
            : <button className="btn ghost sm" onClick={() => setEditing(false)}>回到预览</button>}
        </div>
        <div className="suggest-note-line">
          确认=写入上述文件 ｜ 跳过=7 天内同类不再提醒 ｜ 这不是教导=仅清卡、什么都不记
        </div>
        {msg ? <div className="approval-note">{msg}</div> : null}
      </div>
    </div>
  );
}
