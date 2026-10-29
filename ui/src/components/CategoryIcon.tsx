// 分类空间图标：icon 值 = icons.tsx 图标键或任意 emoji 字符（未知键按 emoji
// 文本渲染；空回退 Tag）——后端只存 ≤16 字符的字符串，语义在前端收口。
// 2026-10-29 全量化：选择器去 emoji、扩到 10 组全量线性图标（feather 风格）；
// emoji 回退渲染保留（存量/手输值不炸）。
import type { ComponentType } from 'react';
import {
  Tag, Flask, Code, Chat, Bot, Search, Globe, BookOpen, Terminal, Folder,
  Box, Brain, PieChart, Zap, Heart, Cpu, Database, Compass, Layers, Users,
  Star, Archive, Clock, Tool, Cloud, Sun, Moon, Lock, Key, Shield, User,
  Crosshair, Scan, Smartphone, MessageSquare, Mail, Phone, Eye, Server,
  Settings, Sparkles, PenLine, Paperclip, Copy, Download, Upload, Play,
  Pin, CheckSquare, ListTasks, Link,
  Activity, Anchor, Award, BarChart2, Bell, Bookmark, Briefcase, Calendar,
  Camera, Clipboard, Coffee, DollarSign, Film, Filter, Flag, Gift, Grid,
  Headphones, Home, ImageIcon, Inbox, LifeBuoy, MapIcon, Mic, Monitor, Music,
  Navigation, Package, Percent, Send, Share2, ShoppingBag, ShoppingCart,
  Sliders, Volume2, Target, ThumbsUp, TrendingUp, Truck, Video, Wifi, Wind,
  FileText, Printer, Rss, Scissors, Watch, Tv, Radio, Thermometer, Smile,
  Hash, Hexagon,
} from './icons';

const ICON_MAP: Record<string, ComponentType<{ size?: number }>> = {
  brain: Brain, chart: PieChart, code: Code, flask: Flask, chat: Chat,
  bot: Bot, search: Search, globe: Globe, book: BookOpen, terminal: Terminal,
  folder: Folder, box: Box, zap: Zap, heart: Heart, cpu: Cpu,
  database: Database, compass: Compass, layers: Layers, users: Users,
  star: Star, archive: Archive, clock: Clock, tag: Tag, tool: Tool,
  cloud: Cloud, sun: Sun, moon: Moon, lock: Lock, key: Key, shield: Shield,
  user: User, crosshair: Crosshair, scan: Scan, smartphone: Smartphone,
  'message-square': MessageSquare, mail: Mail, phone: Phone, eye: Eye,
  server: Server, settings: Settings, sparkles: Sparkles, 'pen-line': PenLine,
  paperclip: Paperclip, copy: Copy, download: Download, upload: Upload,
  play: Play, pin: Pin, 'check-square': CheckSquare, list: ListTasks,
  activity: Activity, anchor: Anchor, award: Award, 'bar-chart2': BarChart2,
  bell: Bell, bookmark: Bookmark, briefcase: Briefcase, calendar: Calendar,
  camera: Camera, clipboard: Clipboard, coffee: Coffee, 'dollar-sign': DollarSign,
  film: Film, filter: Filter, flag: Flag, gift: Gift, grid: Grid,
  headphones: Headphones, home: Home, image: ImageIcon, inbox: Inbox,
  'life-buoy': LifeBuoy, map: MapIcon, mic: Mic, monitor: Monitor, music: Music,
  navigation: Navigation, package: Package, percent: Percent, send: Send,
  'share-2': Share2, 'shopping-bag': ShoppingBag, 'shopping-cart': ShoppingCart,
  sliders: Sliders, volume2: Volume2, target: Target, 'thumbs-up': ThumbsUp,
  'trending-up': TrendingUp, truck: Truck, video: Video, wifi: Wifi, wind: Wind,
  'file-text': FileText, printer: Printer, rss: Rss, scissors: Scissors,
  watch: Watch, tv: Tv, radio: Radio, thermometer: Thermometer, smile: Smile,
  hash: Hash, hexagon: Hexagon, link: Link,
};

/** 分类图标分组（编辑器按组展示；顺序=展示顺序） */
export const CATEGORY_GROUPS: { label: string; keys: string[] }[] = [
  { label: '工作事务', keys: ['briefcase', 'calendar', 'clipboard', 'inbox', 'check-square', 'list', 'pin', 'folder', 'archive', 'clock', 'home'] },
  { label: '研发技术', keys: ['code', 'terminal', 'cpu', 'database', 'server', 'box', 'layers', 'tool', 'monitor', 'settings', 'sliders', 'wifi'] },
  { label: 'AI 智能', keys: ['brain', 'bot', 'sparkles', 'zap', 'eye', 'crosshair', 'scan', 'compass'] },
  { label: '研究分析', keys: ['flask', 'search', 'target', 'chart', 'bar-chart2', 'activity', 'trending-up', 'filter', 'percent', 'hash'] },
  { label: '文档写作', keys: ['book', 'file-text', 'pen-line', 'paperclip', 'printer', 'copy'] },
  { label: '媒体影音', keys: ['camera', 'video', 'film', 'image', 'music', 'headphones', 'mic', 'radio', 'tv', 'volume2'] },
  { label: '通信社交', keys: ['chat', 'message-square', 'mail', 'phone', 'smartphone', 'send', 'share-2', 'bell', 'users', 'user', 'heart', 'thumbs-up', 'smile'] },
  { label: '商业金融', keys: ['dollar-sign', 'shopping-bag', 'shopping-cart', 'package', 'truck', 'award', 'gift'] },
  { label: '环境出行', keys: ['globe', 'cloud', 'sun', 'moon', 'wind', 'thermometer', 'anchor', 'map', 'navigation', 'life-buoy'] },
  { label: '标识其他', keys: ['tag', 'star', 'bookmark', 'flag', 'grid', 'link', 'hexagon', 'watch', 'rss', 'coffee', 'scissors', 'download', 'upload', 'play'] },
];

/** 全量可选键（各组摊平；兼容旧导出名） */
export const CATEGORY_ICON_KEYS: string[] = CATEGORY_GROUPS.flatMap(g => g.keys);

/** 图标键名 → 本地化标签（悬停提示用） */
const KEY_LABEL: Record<string, string> = {
  brain: '脑力/研发', chart: '数据/市场', code: '代码', flask: '实验',
  chat: '沟通', bot: '自动化', search: '检索', globe: '网络',
  book: '文档', terminal: '命令行', folder: '项目', box: '交付',
  zap: '效率', heart: '兴趣', cpu: '算力', database: '数据',
  compass: '方向', layers: '架构', users: '协作', star: '收藏',
  archive: '归档', clock: '最近', tag: '标签', tool: '工具',
  cloud: '云服务', sun: '白昼', moon: '夜间', lock: '锁定',
  key: '密钥', shield: '安全', user: '个人', crosshair: '聚焦',
  scan: '扫描', smartphone: '手机', 'message-square': '消息', mail: '邮件',
  phone: '电话', eye: '观察', server: '服务', settings: '设置',
  sparkles: '灵感', 'pen-line': '写作', paperclip: '附件', copy: '复制',
  download: '下载', upload: '上传', play: '播放', pin: '置顶',
  'check-square': '待办', list: '清单', activity: '动态', anchor: '锚定',
  award: '奖项', 'bar-chart2': '柱状图', bell: '提醒', bookmark: '书签',
  briefcase: '公务', calendar: '日程', camera: '摄影', clipboard: '剪贴板',
  coffee: '咖啡', 'dollar-sign': '资金', film: '影片', filter: '筛选',
  flag: '标记', gift: '礼赠', grid: '网格', headphones: '音频',
  home: '主页', image: '图片', inbox: '收件', 'life-buoy': '救援',
  map: '地图', mic: '录音', monitor: '屏幕', music: '音乐',
  navigation: '导航', package: '包裹', percent: '比例', send: '发送',
  'share-2': '分享', 'shopping-bag': '购物袋', 'shopping-cart': '购物车',
  sliders: '调参', volume2: '音量', target: '目标', 'thumbs-up': '点赞',
  'trending-up': '趋势', truck: '物流', video: '录像', wifi: '网络',
  wind: '风', 'file-text': '文稿', printer: '打印', rss: '订阅',
  scissors: '裁剪', watch: '手表', tv: '电视', radio: '电台',
  thermometer: '温度', smile: '心情', hash: '话题', hexagon: '结构',
  link: '链接',
};
export const iconLabel = (k: string) => KEY_LABEL[k] ?? k;

/** 图标键命中 → 线性 SVG；否则按 emoji 文本（≤8 字符）；空 → Tag 兜底 */
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
  [/网络|爬虫|web|site/, 'globe'],
  [/运维|终端|命令|shell|terminal|ops/, 'terminal'],
  [/项目|proj/, 'folder'],
  [/部署|发布|交付|deploy|docker|box/, 'box'],
  [/性能|测速|efficiency|perf|zap/, 'zap'],
  [/统计|报表|看板|chart|monitor|分析/, 'chart'],
  [/团队|组织|协作|users|team|人/, 'users'],
  [/日程|日历|计划|schedule|calendar|plan/, 'calendar'],
  [/媒体|视频|影像|video|media|影片/, 'video'],
  [/财务|资金|金融|钱|finance|money|fund/, 'dollar-sign'],
  [/安全|加密|防御|security|sec/, 'shield'],
  [/邮件|邮箱|mail|email/, 'mail'],
  [/购物|电商|商城|shop|mall/, 'shopping-bag'],
  [/天气|气候|weather/, 'sun'],
  [/音乐|音频|music|audio/, 'music'],
  [/奖项|比赛|竞赛|award|contest/, 'award'],
];

/** 内置空间（最近/收藏/归档）的 rail 图标键 */
export const BUILTIN_ICONS = { recent: Clock, starred: Star, archive: Archive };
