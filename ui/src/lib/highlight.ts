// 轻量语法高亮（任务1 预览编辑器配套）：textarea 透明叠层用 token 流。
// 零依赖红线（package.json 运行时仅 react/react-markdown/mermaid/zustand）——
// 自写每语言族一条主正则的 tokenizer：注释/字符串/数字/关键词四大类 +
// html 标签、yaml 键、sh 变量、md 结构。正确性要求：token 拼接恒等于原文
// （叠层排版对齐的生命线），正则只标记不消费、空隙走 plain。

export interface Tok { t: string; c: string }

export type Lang = 'js' | 'py' | 'css' | 'html' | 'sh' | 'yaml' | 'md' | 'sql' | 'txt';

/** 扩展名 → 语言族（未覆盖回落 txt=纯字符串/数字着色） */
export function langOf(ext: string): Lang {
  const e = ext.replace(/^\./, '').toLowerCase();
  if (['js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx', 'json', 'lock'].includes(e)) return 'js';
  if (['py', 'pyw', 'pyi'].includes(e)) return 'py';
  if (e === 'css' || e === 'scss') return 'css';
  if (['html', 'htm', 'xml', 'svg'].includes(e)) return 'html';
  if (['sh', 'bash', 'zsh'].includes(e)) return 'sh';
  if (['yaml', 'yml', 'toml', 'ini', 'conf', 'env', 'example'].includes(e)) return 'yaml';
  if (e === 'md' || e === 'markdown') return 'md';
  if (e === 'sql') return 'sql';
  return 'txt';
}

interface Rule { re: RegExp; cls: string[] }   // 捕获组下标 → 类名（0 起）

const JS_KW = 'const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|new|class|extends|implements|interface|type|enum|import|export|from|default|async|await|try|catch|finally|throw|typeof|instanceof|this|super|null|undefined|true|false|void|static|readonly|yield|delete|of|in|as|public|private|protected';
const PY_KW = 'def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|raise|with|lambda|pass|break|continue|yield|global|nonlocal|assert|del|and|or|not|in|is|None|True|False|self|cls|print|len|range|str|int|float|list|dict|set|tuple';
const SH_KW = 'if|then|elif|else|fi|for|in|do|done|while|until|case|esac|function|return|export|local|set|echo|cd|source|exit|alias|unset|shift|read|printf|cat|grep|sed|awk|curl|wget|mkdir|cp|mv|rm|ls|chmod|chown|sudo|apt|pip|npm|pnpm|python3?|git|jq|tar|zip';
const SQL_KW = 'select|from|where|and|or|not|null|insert|into|values|update|set|delete|create|table|index|view|join|left|right|inner|outer|full|on|group|by|order|limit|offset|having|as|distinct|count|sum|avg|min|max|primary|key|foreign|references|default|unique|alter|add|drop|with|union|all|case|when|then|end|between|like|exists|asc|desc';

function rules(lang: Lang): Rule[] {
  switch (lang) {
    case 'js':
      return [{
        re: new RegExp(
          `(\\/\\*[\\s\\S]*?\\*\\/|\\/\\/[^\\n]*)`
          + `|("(?:[^"\\\\\\n]|\\\\.)*"|'(?:[^'\\\\\\n]|\\\\.)*'|\`(?:[^\`\\\\]|\\\\.)*\`)`
          + `|\\b(0x[\\da-fA-F]+|\\d+(?:\\.\\d+)?)\\b`
          + `|\\b(${JS_KW})\\b`, 'g'),
        cls: ['hl-com', 'hl-str', 'hl-num', 'hl-kw'],
      }];
    case 'py':
      return [{
        re: new RegExp(
          `(#[^\\n]*)`
          + `|('''[\\s\\S]*?'''|"""[\\s\\S]*?"""|'(?:[^'\\\\\\n]|\\\\.)*'|"(?:[^"\\\\\\n]|\\\\.)*")`
          + `|\\b(\\d+(?:\\.\\d+)?)\\b`
          + `|\\b(${PY_KW})\\b`, 'g'),
        cls: ['hl-com', 'hl-str', 'hl-num', 'hl-kw'],
      }];
    case 'css':
      return [{
        re: /(\/\*[\s\S]*?\*\/)|("[^"\n]*"|'[^'\n]*')|(@[\w-]+)|(--[\w-]+)|(#[\da-fA-F]{3,8}\b)|\b(\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|s|ms|deg|fr)?)\b|([a-zA-Z-]+)(?=\s*:)/g,
        cls: ['hl-com', 'hl-str', 'hl-kw', 'hl-var', 'hl-num', 'hl-num', 'hl-attr'],
      }];
    case 'html':
      return [{
        re: /(<!--[\s\S]*?-->)|(<\/?[\w-]+|\/?>)|([\w-]+)(?==)|("[^"]*"|'[^']*')/g,
        cls: ['hl-com', 'hl-tag', 'hl-attr', 'hl-str'],
      }];
    case 'sh':
      return [{
        re: new RegExp(
          `(#[^\\n]*)`
          + `|('(?:[^'\\n]|\\\\.)*'|"(?:[^"\\n]|\\\\.)*")`
          + `|(\\$\\{?[\\w@#?*]+\\}?)`
          + `|\\b(${SH_KW})\\b`, 'g'),
        cls: ['hl-com', 'hl-str', 'hl-var', 'hl-kw'],
      }];
    case 'yaml':
      return [{
        re: /(#[^\n]*)|('(?:[^'\n]|'')*'|"(?:[^"\n]|\\.)*")|(^[ \t]*-?[ \t]*[\w.$-]+)(?=:)|(^\.\.\.|^---)|\b(true|false|null|yes|no|on|off|None)\b|\b(\d+(?:\.\d+)?)\b/gm,
        cls: ['hl-com', 'hl-str', 'hl-attr', 'hl-kw', 'hl-kw', 'hl-num'],
      }];
    case 'sql':
      return [{
        re: new RegExp(
          `(--[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/)`
          + `|('(?:[^'\\n]|'')*')`
          + `|\\b(\\d+(?:\\.\\d+)?)\\b`
          + `|\\b(${SQL_KW})\\b`, 'gim'),
        cls: ['hl-com', 'hl-str', 'hl-num', 'hl-kw'],
      }];
    default:
      return [{
        re: /("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|\b(\d+(?:\.\d+)?)\b/g,
        cls: ['hl-str', 'hl-num'],
      }];
  }
}

/** md 行结构 + 行内标记：返回整行 token（heading/quote/fence 整行着色，
 *  其余行做行内 code/bold/link 标记）。 */
function mdTokens(text: string): Tok[] {
  const out: Tok[] = [];
  const inline = (s: string): Tok[] => {
    const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*|__[^_\n]+__)|(\*[^*\n]+\*|_[^_\n]+_)|(\[[^\]\n]*\]\([^)\n]*\))/g;
    let last = 0; let m: RegExpExecArray | null;
    const toks: Tok[] = [];
    while ((m = re.exec(s))) {
      if (m.index > last) toks.push({ t: s.slice(last, m.index), c: '' });
      const c = m[1] ? 'hl-code' : m[2] ? 'hl-bold' : m[3] ? 'hl-ital' : 'hl-link';
      toks.push({ t: m[0], c });
      last = m.index + m[0].length;
    }
    if (last < s.length) toks.push({ t: s.slice(last), c: '' });
    return toks.length ? toks : [{ t: s, c: '' }];
  };
  const lines = text.split('\n');
  lines.forEach((ln, i) => {
    if (/^\s*```/.test(ln) || /^\s*~~~/.test(ln)) out.push({ t: ln, c: 'hl-fence' });
    else if (/^#{1,6}\s/.test(ln)) out.push({ t: ln, c: 'hl-head' });
    else if (/^>\s?/.test(ln)) out.push({ t: ln, c: 'hl-quote' });
    else {
      // 列表/任务标记行内着色
      const mm = ln.match(/^(\s*(?:[-*+]|\d+[.)])\s(?:\[[ xX]\]\s)?)([\s\S]*)$/);
      if (mm) {
        out.push({ t: mm[1], c: 'hl-kw' });
        out.push(...inline(mm[2]));
      } else out.push(...inline(ln));
    }
    if (i < lines.length - 1) out.push({ t: '\n', c: '' });
  });
  return out;
}

/** 高亮主入口：token 拼接恒等于原文（叠层对齐的生命线）。 */
export function highlight(text: string, lang: Lang): Tok[] {
  if (lang === 'md') return mdTokens(text);
  const rs = rules(lang);
  const out: Tok[] = [];
  let last = 0;
  const push = (s: string, c: string) => { if (s) out.push({ t: s, c }); };
  // 单规则族（每语言一条主正则）：exec 循环吃匹配，空隙 plain
  const { re, cls } = rs[0];
  re.lastIndex = 0;
  let m: RegExpExecArray | null;
  let guard = 0;
  while ((m = re.exec(text)) && guard++ < 200_000) {
    if (m[0] === '') { re.lastIndex++; continue; }   // 防零宽死循环
    if (m.index > last) push(text.slice(last, m.index), '');
    let c = '';
    for (let g = 1; g < m.length; g++) {
      if (m[g] != null && cls[g - 1]) { c = cls[g - 1]; break; }
    }
    push(m[0], c);
    last = m.index + m[0].length;
  }
  if (last < text.length) push(text.slice(last), '');
  return out;
}
