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
  icon?: string | null; size?: number;
}) {
  const key = (icon ?? '').trim();
  if (key) {
    const Ico = ICON_MAP[key];
    if (Ico) return <Ico size={size} />;
    if (key.length <= 8) {
      return <span className="cat-icon-emoji" style={{ fontSize: size + 2 }}>{key}</span>;
    }
  }
  return <Tag size={size} />;
}

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
