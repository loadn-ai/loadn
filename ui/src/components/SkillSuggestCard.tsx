import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useStore } from '../stores/sessions';
import { Check, ChevronRight, Copy, Sparkles, X } from './icons';

/** P12 技能建议卡——引擎检测到用户教学/纠错后写 .loadn/skill-suggest.json
 *  （单槽 pending），webui 卡片可编辑，确认 → 写项目 .agents/skills/（过
 *  skill_scan 八类扫描）；拒绝 → 同类指纹 7 天抑制；误判 → 仅清槽不记负样本。
 *  演进：
 *  2026-10-10 误判通道+生效说明；2026-10-12 四段式透明化；2026-10-17 设计稿
 *  v3（hero 渐变条/kind 徽章/markdown 渲染）。
 *  2026-10-19 v4 交互重构（用户设计稿「智能技能学习」）：信息架构重排——
 *  ① 核心规则置顶可直接改（原版埋在预览正文里，第一眼不知 AI 学到什么）；
 *  ② 关键约束自动切分成 ✓ 胶囊，2 秒扫读可判断提炼对不对；③ 学习来源
 *  （教导原话+上轮行为）折叠进抽屉双栏对比，不再大段重复占屏；④ 说明书
 *  小字收敛进 ? 悬浮气泡（作用范围/随时可改/安全扫描+指纹 ID）；⑤ 视图
 *  切换 = 可视化卡片 ↔ SKILL.md 源码双 tab，源码 tab 直接改最终落盘文件
 *  （所见即所得，双向联动）；⑥ 底部按钮自解释：保存为工作区技能 / 仅本次
 *  生效，不保存 / 误判这不是规则教导；⑦ 决策后卡片原地收缩为结果条（成功
 *  绿/忽略灰），不再是易闪没的 toast。作用范围不做假 select——后端只写
 *  本工作区，静态胶囊如实标注。 */

interface SuggestCard {
  kind: string; name: string; description: string; body: string;
  fingerprint?: string; trigger?: string;
}

/** decide 端点同款校验（^[a-z0-9][a-z0-9._-]{0,63}$）——非法时保存禁用 */
const NAME_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;

const RULE_TAG = '## 应遵循的规则';
const TAIL_TAG = '## 上一轮行为（被纠正的上下文）';

/** body 三节拆解（引擎 consolidate.py 组装协议：> 原话引用 / 上一轮行为 /
 *  应遵循的规则；小节按场景可省略，解析容错）。 */
function parseBody(body: string): { quote: string; prevTail: string; rule: string } {
  const quoteM = /^> (.+)$/m.exec(body);
  const tailM = body.indexOf(TAIL_TAG) >= 0
    ? new RegExp(`${TAIL_TAG.replace(/[()]/g, '\\$&')}\\n+([\\s\\S]*?)(?=\\n## |\\s*$)`).exec(body)
    : null;
  const ruleM = body.includes(RULE_TAG)
    ? new RegExp(`${RULE_TAG}\\s*\\n+([\\s\\S]*)$`).exec(body)
    : null;
  return {
    quote: quoteM ? quoteM[1].trim() : '',
    prevTail: tailM ? tailM[1].trim() : '',
    rule: ruleM ? ruleM[1].trim() : '',
  };
}

/** 规则节写回 body（有则替换、无则追加——引擎在提炼无增量时省略该节，
 *  用户一旦编辑即落成显式规则节）。 */
function withRule(body: string, rule: string): string {
  const r = rule.trim();
  const i = body.indexOf(RULE_TAG);
  if (i >= 0) return (body.slice(0, i) + RULE_TAG + (r ? '\n\n' + r : '')).trimEnd();
  return r ? `${body.trimEnd()}\n\n${RULE_TAG}\n\n${r}` : body;
}

/** 核心规则 → 关键约束胶囊（分号/句读/换行切分，≤4 条，每条截 18 字） */
function chipsOf(rule: string): string[] {
  return rule.split(/[；;。\n]/)
    .map(s => s.replace(/^[·、\-*\s]+/, '').trim())
    .filter(s => s.length >= 4)
    .slice(0, 4)
    .map(s => (s.length > 18 ? s.slice(0, 18) + '…' : s));
}

/** 最终将写入的 SKILL.md 全文（与 decide 端点组装逐字一致） */
function skillFileText(c: SuggestCard): string {
  return `---\nname: ${c.name}\ndescription: ${c.description}\n`
    + `origin: user-taught:${c.fingerprint ?? ''}\n---\n\n${c.body}\n`;
}

/** 源码 tab 编辑回解析（frontmatter 拆 name/description，origin 由服务端
 *  指纹决定忽略；无合法 frontmatter 时全文当正文——所见即所得不丢字）。 */
function parseSkillFile(text: string, fb: SuggestCard): SuggestCard {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
  if (!m) return { ...fb, body: text.trim() };
  const out = { ...fb, body: m[2].trim() };
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z_][\w-]*):\s?(.*)$/.exec(line);
    if (!kv) continue;
    if (kv[1] === 'name') out.name = kv[2].trim();
    else if (kv[1] === 'description') out.description = kv[2].trim();
  }
  return out;
}

/** 决策后的原地结果条（成功=绿 ✓ / 忽略=灰 –；✕ 可关，换会话自动清） */
type Result = { type: 'saved' | 'skipped' | 'misjudged'; name?: string } | null;

export default function SkillSuggestCard() {
  const currentSid = useStore(s => s.currentSid);
  const [card, setCard] = useState<SuggestCard | null>(null);
  const [wsRoot, setWsRoot] = useState('');
  const [draft, setDraft] = useState<SuggestCard | null>(null);
  const [msg, setMsg] = useState('');
  const [mode, setMode] = useState<'visual' | 'source'>('visual');
  const [srcText, setSrcText] = useState('');
  const [openCtx, setOpenCtx] = useState(false);
  const [quoteOpen, setQuoteOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [result, setResult] = useState<Result>(null);

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
    setResult(null); setMode('visual'); setOpenCtx(false); setQuoteOpen(false);
    if (currentSid) void load(currentSid);
    else { setCard(null); setDraft(null); }
  }, [currentSid]);
  // turn 终态后可能出现新建议——低频轮询兜底（SSE 无该事件类型）。
  // pending 单槽：卡在场（含编辑中）不轮询，避免 20s 重置覆盖用户编辑。
  useEffect(() => {
    if (!currentSid || card) return;
    const t = setInterval(() => void load(currentSid), 20000);
    return () => clearInterval(t);
  }, [currentSid, card]);

  if (card && draft) {
    const p = parseBody(draft.body);
    const quote = p.quote || (card.trigger ?? '');
    const rule = p.rule || draft.description;
    const nameBad = !NAME_RE.test(draft.name);
    const chips = chipsOf(rule);
    const skillPath = `${wsRoot || '〈工作区〉'}/.agents/skills/${draft.name}/SKILL.md`;
    const isTeach = card.kind === 'teach';

    const decide = async (accept: boolean, misjudge = false) => {
      const payload = accept ? draft : (misjudge ? { misjudge: true } : {});
      try {
        await api(
          `/api/sessions/${encodeURIComponent(currentSid!)}/skill-suggest/decide`,
          { method: 'POST', body: JSON.stringify({ accept, ...payload }) });
        setCard(null); setDraft(null); setMsg('');
        setResult(accept ? { type: 'saved', name: draft.name }
          : misjudge ? { type: 'misjudged' } : { type: 'skipped' });
      } catch (e) {
        setMsg(`决策失败：${e instanceof Error ? e.message : e}`);
      }
    };
    const copyPath = () => {
      const done = () => { setCopied(true); setTimeout(() => setCopied(false), 1800); };
      const fallback = () => {
        const ta = document.createElement('textarea');
        ta.value = skillPath; document.body.appendChild(ta);
        ta.select(); document.execCommand('copy'); ta.remove(); done();
      };
      if (navigator.clipboard?.writeText)
        navigator.clipboard.writeText(skillPath).then(done).catch(fallback);
      else fallback();
    };
    const toMode = (m: 'visual' | 'source') => {
      if (m === mode) return;
      if (m === 'source') setSrcText(skillFileText(draft)); // 进源码=从当前草稿合成
      setMode(m); // 回可视化无需转换：源码编辑已即时解析回 draft
    };

    return (
      <div className="approval-banner">
        <div className="approval-card skill-suggest">
          {/* 头部：是什么/为什么出现 + 视图切换 + 生效机制气泡 */}
          <div className="suggest-head">
            <span className="suggest-hicon"><Sparkles size={16} /></span>
            <div className="suggest-hmain">
              <div className="suggest-htitle-row">
                <span className="suggest-htitle">
                  {isTeach ? '发现值得沉淀的工作方法' : '发现可复用的工作流经验'}
                </span>
                <span className={`suggest-hbadge ${card.kind}`}>
                  {isTeach ? '教导提炼' : '纠正提炼'}
                </span>
              </div>
              <div className="suggest-hdesc">
                {isTeach
                  ? 'Agent 从你的教导中提炼出了一条新做法，保存后将在后续会话中自动遵循。'
                  : 'Agent 从你刚才的纠正中提炼出了一条经验，保存后将在后续会话中自动遵循、避免重蹈覆辙。'}
              </div>
            </div>
            <div className="suggest-hops">
              <div className="suggest-seg" role="group" aria-label="视图切换">
                <button className={`suggest-seg-btn${mode === 'visual' ? ' on' : ''}`}
                        aria-pressed={mode === 'visual'} onClick={() => toMode('visual')}>
                  可视化卡片</button>
                <button className={`suggest-seg-btn${mode === 'source' ? ' on' : ''}`}
                        aria-pressed={mode === 'source'} onClick={() => toMode('source')}>
                  SKILL.md 源码</button>
              </div>
              <div className="suggest-info" tabIndex={0} aria-label="生效规则说明">
                <span className="suggest-info-btn" aria-hidden>?</span>
                <div className="suggest-tip" role="tooltip">
                  <div className="suggest-tip-title">技能生效与安全机制</div>
                  <ul>
                    <li><b>作用范围：</b>本工作区的新会话自动加载（当前运行中会话不受影响）</li>
                    <li><b>随时可改：</b>设置 → 技能 页可编辑/停用/删除</li>
                    <li><b>安全扫描：</b>写入前自动经过供应链安全扫描</li>
                  </ul>
                  <div className="suggest-tip-id">ID: user-taught:{draft.fingerprint ?? ''}</div>
                </div>
              </div>
            </div>
          </div>

          {mode === 'visual' ? (
            <>
              {/* 核心提炼区：规则置顶可直接改（视觉焦点） */}
              <div className="suggest-core">
                <div className="suggest-core-row">
                  <div className="suggest-name-wrap">
                    <span className="suggest-lbl">技能标识</span>
                    <input className={`suggest-name${nameBad ? ' bad' : ''}`}
                           value={draft.name} spellCheck={false} autoComplete="off"
                           aria-label="技能标识（目录名，小写字母/数字/._-）"
                           onChange={e => setDraft({ ...draft, name: e.target.value })} />
                    <span className={`suggest-name-hint${nameBad ? ' bad' : ''}`}>
                      {nameBad ? '小写字母/数字/._-' : '点击可改名'}</span>
                  </div>
                  <div className="suggest-scope"
                       title="技能写入当前工作区 .agents/skills/（该工作区新会话自动加载）；平台技能库另存一份跨会话副本">
                    <span className="suggest-lbl">作用范围</span>
                    <span className="suggest-scope-pill">本工作区 · 新会话</span>
                  </div>
                </div>
                <label className="suggest-rule-lbl" htmlFor="suggest-rule">
                  <span className="suggest-rule-bar" aria-hidden />
                  今后将自动遵循的核心规则
                  <span className="suggest-editable-tag"><PencilMark />可编辑</span>
                </label>
                <textarea id="suggest-rule" className="suggest-rule" rows={3} value={rule}
                          title="点击文本可直接修改；保存后写入 SKILL.md 的规则节"
                          onChange={e => setDraft({ ...draft, body: withRule(draft.body, e.target.value) })} />
                {chips.length > 0 && (
                  <div className="suggest-chips">
                    <span className="suggest-lbl">已提取关键约束</span>
                    {chips.map(c => (
                      <span key={c} className="suggest-chip"><Check size={10} />{c}</span>
                    ))}
                  </div>
                )}
              </div>

              {/* 渐进式披露：学习来源默认折叠，展开双栏对比 */}
              <div className="suggest-drawer-wrap">
                <button className="suggest-drawer-head" onClick={() => setOpenCtx(v => !v)}
                        aria-expanded={openCtx}>
                  <ChevronRight className={`suggest-chev${openCtx ? ' open' : ''}`} size={14} />
                  <span className="suggest-drawer-title">查看学习来源与被纠正的上下文</span>
                  <span className="suggest-drawer-sub">
                    — 你的教导原话{quote ? `（${quote.length} 字）` : ''}
                    {p.prevTail ? ' 与上一轮行为快照' : ''}
                  </span>
                  <span className="suggest-drawer-toggle">{openCtx ? '收起' : '展开'}</span>
                </button>
                <div className={`suggest-drawer${openCtx ? ' open' : ''}`}>
                  <div className="suggest-drawer-body">
                    <div className={`suggest-cols${p.prevTail ? '' : ' single'}`}>
                      {p.prevTail && (
                        <div className="suggest-col warn">
                          <div className="suggest-col-title">上一轮行为（被纠正的上下文）</div>
                          <div className="suggest-col-text">{p.prevTail}</div>
                        </div>
                      )}
                      {quote && (
                        <div className="suggest-col teach">
                          <div className="suggest-col-title">你的教导原话（{quote.length} 字）</div>
                          <div className={`suggest-col-text clamp${quoteOpen ? ' open' : ''}`}
                               role="button" tabIndex={0} title="点击展开/收起全文"
                               onClick={() => setQuoteOpen(v => !v)}
                               onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setQuoteOpen(v => !v); }}>
                            {quote}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="suggest-pathrow">
                      <span className="suggest-lbl">落盘路径</span>
                      <code className="suggest-path-code">{skillPath}</code>
                      <button className="suggest-copy" onClick={copyPath}
                              title="复制完整路径">
                        {copied ? '✓ 已复制' : <><Copy size={11} />复制路径</>}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </>
          ) : (
            /* SKILL.md 源码：直接编辑最终落盘文件（编辑即时解析回可视化字段） */
            <div className="suggest-src">
              <div className="suggest-src-note">
                正在直接编辑将写入磁盘的 <code>SKILL.md</code> 源文件
                （frontmatter + markdown 正文，与落盘内容逐字一致）
              </div>
              <textarea className="suggest-src-ta" value={srcText} rows={12}
                        spellCheck={false} aria-label="SKILL.md 源文件"
                        onChange={e => {
                          setSrcText(e.target.value);
                          setDraft(parseSkillFile(e.target.value, draft));
                        }} />
              {nameBad && (
                <div className="suggest-warn-line">
                  技能名需为小写字母/数字/._-（≤64 字符），当前「{draft.name || '（空）'}」不合法，保存被禁用
                </div>
              )}
            </div>
          )}

          {/* 底部操作栏：按钮文案自解释（后果进 tooltip），负向弱化到右侧 */}
          <div className="suggest-foot">
            <div className="suggest-foot-main">
              <button className="btn primary sm" disabled={nameBad || !draft.body.trim()}
                      title={`写入 ${skillPath}（先过安全扫描），本工作区新会话自动加载`}
                      onClick={() => void decide(true)}>
                <Check size={13} /> 保存为工作区技能
              </button>
              <button className="btn sm"
                      title="本次对话已遵循你的指令；不写文件，同类内容 7 天内不再提醒"
                      onClick={() => void decide(false)}>仅本次生效，不保存</button>
            </div>
            <button className="btn ghost sm suggest-misjudge"
                    title="清掉这张卡：不写文件、不记录任何抑制"
                    onClick={() => void decide(false, true)}>误判，这不是规则教导</button>
          </div>
          {msg ? <div className="approval-note">{msg}</div> : null}
        </div>
      </div>
    );
  }

  if (result) {
    return (
      <div className="approval-banner">
        <div className={`approval-card suggest-result ${result.type}`}>
          <span className="suggest-result-ico" aria-hidden>
            {result.type === 'saved' ? <Check size={13} /> : '–'}
          </span>
          <div className="suggest-result-text">
            {result.type === 'saved' ? (
              <>
                <b>已将「{result.name}」写入工作区技能库</b>
                <span>后续新会话将自动加载这条规则；随时可在 设置 → 技能 中编辑或停用。</span>
              </>
            ) : result.type === 'skipped' ? (
              <>
                <b>已忽略保存，仅在当前对话中遵循你的指令</b>
                <span>未写入 SKILL.md；同类内容 7 天内不再提醒。</span>
              </>
            ) : (
              <>
                <b>已清理这张建议卡（按误判处理）</b>
                <span>未写入文件、未记录任何抑制。</span>
              </>
            )}
          </div>
          <button className="suggest-result-x" aria-label="关闭"
                  onClick={() => setResult(null)}><X size={13} /></button>
        </div>
      </div>
    );
  }
  return null;
}

/** 可编辑小徽标里的铅笔符号（不引 Pencil 组件——徽标里 12px 线条图标太密） */
function PencilMark() {
  return <span aria-hidden className="suggest-pencil-mark">✎</span>;
}
