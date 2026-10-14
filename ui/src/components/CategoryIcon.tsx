// 分类空间图标：icon 值 = icons.tsx 图标键或任意 emoji 字符（未知键按 emoji
// 文本渲染；空回退 Tag）——后端只存 ≤16 字符的字符串，语义在前端收口。
import type { ComponentType } from 'react';
import {
  Tag, Flask, Code, Chat, Bot, Search, Globe, BookOpen, Terminal, Folder,
  Box, Brain, PieChart, Zap, Heart, Cpu, Database, Compass, Layers, Users,
  Star, Archive, Clock,
} from './icons';

export const CATEGORY_ICON_KEYS = [
  'brain', 'chart', 'code', 'flask', 'chat', 'bot', 'search', 'globe',
  'book', 'terminal', 'folder', 'box', 'zap', 'heart', 'cpu', 'database',
  'compass', 'layers', 'users',
] as const;

const ICON_MAP: Record<string, ComponentType<{ size?: number }>> = {
  brain: Brain, chart: PieChart, code: Code, flask: Flask, chat: Chat,
  bot: Bot, search: Search, globe: Globe, book: BookOpen, terminal: Terminal,
  folder: Folder, box: Box, zap: Zap, heart: Heart, cpu: Cpu,
  database: Database, compass: Compass, layers: Layers, users: Users,
};

export const EMOJI_CHOICES = ['🧠', '📈', '🔬', '💬', '🤖', '🔍', '🌐', '📚',
  '⚙️', '🚀', '💡', '🗂️', '🎯', '🛠️', '📊', '🔒'];

/** 图标键命中 → 线性 SVG；否则按 emoji 文本（≤2 码位）；空 → Tag 兜底 */
export default function CategoryIcon({ icon, size = 14 }: {
  icon?: string | null; size?: number; name?: string | null;
}) {
  const key = (icon ?? '').trim();
  if (key) {
    const Ico = ICON_MAP[key];
    if (Ico) return <Ico size={size} />;
    if (key.length <= 8) {
      return <span className="cat-icon-emoji" style={{ fontSize: size + 2 }}>{key}</span>;
    }
  }
  // 空图标：按空间名智能兜底（「代码」→ Code 图标而非通用 Tag），
  // 命不中再退 Tag——用户没挑图标时首屏观感也不违和
  const n = (name ?? '').toLowerCase();
  if (n) {
    for (const [re, k] of NAME_HINTS) {
      if (re.test(n)) {
        const Hint = ICON_MAP[k];
        if (Hint) return <Hint size={size} />;
      }
    }
  }
  return <Tag size={size} />;
}

/** 空间名 → 图标键推断（中文关键词 + 常用英文；顺序=优先级） */
const NAME_HINTS: [RegExp, string][] = [
  [/代码|编程|开发|dev|code|frontend|backend/, 'code'],
  [/数据|数据库|db|database|sql/, 'database'],
  [/文档|笔记|知识|doc|wiki|note/, 'book'],
  [/研究|实验|科研|research|lab/, 'flask'],
  [/ai|模型|大模型|llm|gpt|glm|agent|智能/, 'brain'],
  [/聊天|对话|客服|chat/, 'chat'],
  [/自动|机器人|bot|workflow|流水线/, 'bot'],
  [/搜索|调研|情报|search/, 'search'],
  [/网络|爬虫|web|site|网络/, 'globe'],
  [/运维|终端|命令|shell|terminal|ops/, 'terminal'],
  [/项目|proj|归档/, 'folder'],
  [/部署|发布|交付|deploy|docker|box/, 'box'],
  [/性能|测速|efficiency|perf|zap/, 'zap'],
  [/统计|报表|看板|chart|monitor|分析/, 'chart'],
  [/团队|组织|协作|users|team|人/, 'users'],
];

/** 图标键名 → 本地化标签（picker 悬停提示用） */
const KEY_LABEL: Record<string, string> = {
  brain: '脑力/研发', chart: '数据/市场', code: '代码', flask: '实验',
  chat: '沟通', bot: '自动化', search: '检索', globe: '网络',
  book: '文档', terminal: '命令行', folder: '项目', box: '交付',
  zap: '效率', heart: '兴趣', cpu: '算力', database: '数据',
  compass: '方向', layers: '架构', users: '协作', star: '收藏',
  archive: '归档', clock: '最近',
};
export const iconLabel = (k: string) => KEY_LABEL[k] ?? k;

/** 内置空间（最近/收藏/归档）的 rail 图标键 */
export const BUILTIN_ICONS = { recent: Clock, starred: Star, archive: Archive };
