/** 轻量线性图标集（feather 风格，24 viewBox / currentColor）——替代按钮里的 emoji，
 *  保证明暗主题、各平台渲染一致。size 走 props，默认 16。 */
import type { CSSProperties, ReactNode } from 'react';

type IconProps = { size?: number; className?: string; style?: CSSProperties };

function Svg({ size = 16, className, style, strokeWidth = 1.9, children }: IconProps & {
  children: ReactNode; strokeWidth?: number;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true" className={className} style={style}>{children}</svg>
  );
}

export const Paperclip = (p: IconProps) => (
  <Svg {...p}><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></Svg>
);

export const Sparkles = (p: IconProps) => (
  <Svg {...p}><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z" /><path d="M19 15l.9 2.4L22 18l-2.1.6L19 21l-.9-2.4L16 18l2.1-.6L19 15z" /></Svg>
);

export const Flask = (p: IconProps) => (
  <Svg {...p}><path d="M10 2v7.5L4.7 18a2 2 0 0 0 1.7 3h11.2a2 2 0 0 0 1.7-3L14 9.5V2" /><path d="M8.5 2h7" /><path d="M7 16h10" /></Svg>
);

export const Code = (p: IconProps) => (
  <Svg {...p}><path d="M8 6l-5 6 5 6" /><path d="M16 6l5 6-5 6" /><path d="M13.5 4l-3 16" /></Svg>
);

export const Chat = (p: IconProps) => (
  <Svg {...p}><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5c-1.4 0-2.8-.3-4-1L3 21l2-5.5a8.5 8.5 0 1 1 16-4z" /></Svg>
);

export const Bot = (p: IconProps) => (
  <Svg {...p}><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 8V4" /><circle cx="12" cy="3" r="1" /><path d="M9 13v1M15 13v1" /></Svg>
);

export const ChevronDown = (p: IconProps) => (
  <Svg {...p} ><path d="M6 9l6 6 6-6" /></Svg>
);

export const Check = (p: IconProps) => (
  <Svg {...p}><path d="M4 12.5l5 5L20 6.5" /></Svg>
);

export const ArrowUp = (p: IconProps) => (
  <Svg {...p}><path d="M12 19V5" /><path d="M5.5 11.5L12 5l6.5 6.5" /></Svg>
);

export const StopSquare = ({ size = 16 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <rect x="7" y="7" width="10" height="10" rx="2.5" />
  </svg>
);

export const FileDoc = (p: IconProps) => (
  <Svg {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z" /><path d="M14 2v6h6" /></Svg>
);

export const FileImage = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="3" width="18" height="18" rx="2.5" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="M21 15.5l-4.5-4.5L6 21.5" /></Svg>
);

export const ExternalLink = (p: IconProps) => (
  <Svg {...p}><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><path d="M15 3h6v6" /><path d="M10 14L21 3" /></Svg>
);

export const Sun = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="4.5" /><path d="M12 2v2.2M12 19.8V22M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2 12h2.2M19.8 12H22M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6" /></Svg>
);

export const Moon = (p: IconProps) => (
  <Svg {...p}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></Svg>
);

export const Settings = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></Svg>
);

export const Plus = (p: IconProps) => (
  <Svg {...p} strokeWidth={2.2}><path d="M12 5v14M5 12h14" /></Svg>
);

/** Star：filled 时填充（收藏实心/空心两态） */
export const Star = ({ size = 16, filled = false, className }: IconProps & { filled?: boolean }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'}
       stroke="currentColor" strokeWidth={1.8} strokeLinejoin="round" aria-hidden="true"
       className={className}>
    <path d="M12 2.6l2.9 5.9 6.5.95-4.7 4.6 1.1 6.5L12 17.5l-5.8 3.05 1.1-6.5-4.7-4.6 6.5-.95L12 2.6z" />
  </svg>
);

export const Pencil = (p: IconProps) => (
  <Svg {...p}><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" /></Svg>
);

export const ListTasks = (p: IconProps) => (
  <Svg {...p}><path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" /></Svg>
);

export const PenLine = (p: IconProps) => (
  <Svg {...p}><path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" /></Svg>
);

export const Undo = (p: IconProps) => (
  <Svg {...p}><path d="M1 4v6h6" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" /></Svg>
);

export const Trash = (p: IconProps) => (
  <Svg {...p}><path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><path d="M10 11v6M14 11v6" /></Svg>
);

export const Copy = (p: IconProps) => (
  <Svg {...p}><rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></Svg>
);

export const Folder = (p: IconProps) => (
  <Svg {...p}><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></Svg>
);

export const Globe = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="10" /><path d="M2 12h20" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></Svg>
);

export const Search = (p: IconProps) => (
  <Svg {...p}><circle cx="11" cy="11" r="7.5" /><path d="M21 21l-4.35-4.35" /></Svg>
);

export const Menu = (p: IconProps) => (
  <Svg {...p} strokeWidth={2}><path d="M3 6h18M3 12h18M3 18h18" /></Svg>
);

export const BookOpen = (p: IconProps) => (
  <Svg {...p}><path d="M2 3.5h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z" /><path d="M22 3.5h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z" /></Svg>
);

export const Terminal = (p: IconProps) => (
  <Svg {...p}><path d="M4 17l6-6-6-6" /><path d="M12 19h8" /></Svg>
);

export const Tool = (p: IconProps) => (
  <Svg {...p}><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" /></Svg>
);

export const CheckSquare = (p: IconProps) => (
  <Svg {...p}><path d="M9 11l3 3L21.5 4.5" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></Svg>
);

export const Link = (p: IconProps) => (
  <Svg {...p}><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></Svg>
);

/** 平面包裹箱（分类候选 box/交付）：2026-10-13 风格统一——旧形为等距
 *  立体立方（透视与集内平面正视冲突），重绘为正视包裹箱：圆角箱体 +
 *  盖线 + 中缝胶带三笔。InstallDialog/SecurityPanel 复用同形。 */
export const Box = (p: IconProps) => (
  <Svg {...p}><rect x="4" y="4.5" width="16" height="15" rx="2" /><path d="M4 9h16" /><path d="M12 9v10.5" /></Svg>
);

export const ChevronRight = (p: IconProps) => (
  <Svg {...p}><path d="M9 18l6-6-6-6" /></Svg>
);

export const RotateCw = (p: IconProps) => (
  <Svg {...p}><path d="M23 4v6h-6" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></Svg>
);

export const Cloud = (p: IconProps) => (
  <Svg {...p}><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" /></Svg>
);

export const Upload = (p: IconProps) => (
  <Svg {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M17 8l-5-5-5 5" /><path d="M12 3v12" /></Svg>
);

/** 上传中转圈：stroke-dash 留缺口，CSS wd-spin 旋转 */
export const Spinner = ({ size = 12, className = 'wd-spin' }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth={2.6} strokeLinecap="round" aria-hidden="true" className={className}>
    <circle cx="12" cy="12" r="9" strokeDasharray="40" strokeDashoffset="26" />
  </svg>
);

export const Clock = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" /></Svg>
);

export const Pause = (p: IconProps) => (
  <Svg {...p} strokeWidth={2.2}><path d="M9 5v14M15 5v14" /></Svg>
);

export const Play = (p: IconProps) => (
  <Svg {...p} strokeWidth={2.2}><path d="M7 5l12 7-12 7z" /></Svg>
);

export const X = (p: IconProps) => (
  <Svg {...p} strokeWidth={2.2}><path d="M6 6l12 12M18 6L6 18" /></Svg>
);

export const Download = (p: IconProps) => (
  <Svg {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5" /><path d="M12 15V3" /></Svg>
);

/** 「…」行菜单入口（三个竖点） */
export const MoreVertical = (p: IconProps) => (
  <svg width={p.size ?? 16} height={p.size ?? 16} viewBox="0 0 24 24" fill="currentColor"
       aria-hidden="true" className={p.className}>
    <circle cx="12" cy="5" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="12" cy="19" r="1.7" />
  </svg>
);

/** 置顶图钉（分区标记：置顶） */
export const Pin = (p: IconProps) => (
  <Svg {...p}><path d="M12 17v5" /><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1z" /></Svg>
);

export const ChevronLeft = (p: IconProps) => (
  <Svg {...p}><path d="M15 18l-6-6 6-6" /></Svg>
);

/** 归档箱（分区标记：归档） */
export const Archive = (p: IconProps) => (
  <Svg {...p}><path d="M21 8v13H3V8" /><rect x="1" y="3" width="22" height="5" rx="1" /><path d="M10 12h4" /></Svg>
);

/** 分类标签（自定义分区） */
export const Tag = (p: IconProps) => (
  <Svg {...p}><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.83z" /><circle cx="7" cy="7" r="1.5" /></Svg>
);

export const ArrowRight = (p: IconProps) => (
  <Svg {...p}><path d="M5 12h14" /><path d="M12 5l7 7-7 7" /></Svg>
);

// ---- 分类空间图标（icon rail / IconPicker 候选集） ----
// 风格契约（2026-10-13 统一审计后固化，新增候选必须遵守）：
//   线条 stroke=currentColor / strokeWidth=1.9（Svg 默认，不覆写）；
//   填充 fill=none（纯描边，无实心/半实心混排）；
//   透视 平面正视（flat）——禁等距立体/斜投影（Box/Layers 旧立体形已重绘）；
//   圆角 容器类 rect rx=2、内嵌元素 rx≤1.5（内外层级语言一致）；
//   尖角主题形（Zap/Heart/Compass 指针）靠 round join 软化，属例外许可。
export const Brain = (p: IconProps) => (
  <Svg {...p}><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44A2.5 2.5 0 0 1 4.5 18 2.5 2.5 0 0 1 2 15.5a2.5 2.5 0 0 1 1.42-2.25A2.5 2.5 0 0 1 2 11a2.5 2.5 0 0 1 2-2.45A2.5 2.5 0 0 1 6 5.2 2.5 2.5 0 0 1 9.5 2z" /><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44A2.5 2.5 0 0 0 19.5 18a2.5 2.5 0 0 0 2.5-2.5 2.5 2.5 0 0 0-1.42-2.25A2.5 2.5 0 0 0 22 11a2.5 2.5 0 0 0-2-2.45A2.5 2.5 0 0 0 18 5.2 2.5 2.5 0 0 0 14.5 2z" /></Svg>
);

export const PieChart = (p: IconProps) => (
  <Svg {...p}><path d="M21.2 15.9A10 10 0 1 1 8 2.8" /><path d="M22 12A10 10 0 0 0 12 2v10z" /></Svg>
);

export const Zap = (p: IconProps) => (
  <Svg {...p}><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" /></Svg>
);

export const Heart = (p: IconProps) => (
  <Svg {...p}><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21.2l7.8-7.8 1-1a5.5 5.5 0 0 0 0-7.8z" /></Svg>
);

export const Cpu = (p: IconProps) => (
  <Svg {...p}><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" rx="1.5" /><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" /></Svg>
);

export const Database = (p: IconProps) => (
  <Svg {...p}><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" /><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" /></Svg>
);

export const Compass = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="10" /><path d="M16.2 7.8l-2.1 6.3-6.3 2.1 2.1-6.3z" /></Svg>
);

/** 平面层叠（分类候选 layers/架构）：2026-10-13 风格统一——旧形为等距
 *  立体菱形堆叠（透视冲突），重绘为正视三片层板（薄片 rx=1.5，非容器，
 *  无交叠描边穿过）。 */
export const Layers = (p: IconProps) => (
  <Svg {...p}><rect x="5" y="3.5" width="14" height="4" rx="1.5" /><rect x="5" y="10" width="14" height="4" rx="1.5" /><rect x="5" y="16.5" width="14" height="4" rx="1.5" /></Svg>
);

export const Users = (p: IconProps) => (
  <Svg {...p}><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></Svg>
);

/* AC-5.10b：emoji→线性图标补充件（feather 同源路径）——姿态卡/资源卡/渠道卡 */
export const Lock = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></Svg>
);

export const Key = (p: IconProps) => (
  <Svg {...p}><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3-3.5 3.5z" /></Svg>
);

export const Shield = (p: IconProps) => (
  <Svg {...p}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></Svg>
);

export const User = (p: IconProps) => (
  <Svg {...p}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></Svg>
);

export const Crosshair = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="10" /><path d="M22 12h-4M6 12H2M12 6V2M12 22v-4" /></Svg>
);

export const Maximize2 = (p: IconProps) => (
  <Svg {...p}><path d="M15 3h6v6" /><path d="M9 21H3v-6" /><path d="M21 3l-7 7" /><path d="M3 21l7-7" /></Svg>
);

export const Minimize2 = (p: IconProps) => (
  <Svg {...p}><path d="M4 14h6v6" /><path d="M20 10h-6V4" /><path d="M14 10l7-7" /><path d="M3 21l7-7" /></Svg>
);

export const ChevronUpDown = (p: IconProps) => (
  <Svg {...p}><path d="M7 9l5-5 5 5" /><path d="M7 15l5 5 5-5" /></Svg>
);

/* ---- 资源 Hub 图标（2026-10-21 设计稿批：SVG 彻底取代资源面板 emoji） ---- */

export const Scan = (p: IconProps) => (
  <Svg {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z" /><path d="M14 2v6h6" /><path d="M16 13H8" /><path d="M16 17H8" /></Svg>
);

export const Smartphone = (p: IconProps) => (
  <Svg {...p}><rect x="5" y="2" width="14" height="20" rx="2" /><path d="M12 18h.01" /></Svg>
);

export const MessageSquare = (p: IconProps) => (
  <Svg {...p}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></Svg>
);

export const Mail = (p: IconProps) => (
  <Svg {...p}><rect x="2" y="4" width="20" height="16" rx="2" /><path d="M22 7l-10 6L2 7" /></Svg>
);

export const Phone = (p: IconProps) => (
  <Svg {...p}><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" /></Svg>
);

export const Eye = (p: IconProps) => (
  <Svg {...p}><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></Svg>
);

export const Server = (p: IconProps) => (
  <Svg {...p}><rect x="2" y="2" width="20" height="8" rx="2" /><rect x="2" y="14" width="20" height="8" rx="2" /><path d="M6 6h.01" /><path d="M6 18h.01" /></Svg>
);

/* ---- 分类图标全量化（2026-10-29）：feather 同源路径 53 枚——分类编辑器
 *  彻底去 emoji，选择器扩到全量线性图标（几何契约：24 viewBox / stroke 线条） ---- */

export const Activity = (p: IconProps) => (
  <Svg {...p}><path d="M22 12h-4l-3 9-6-18-3 9H2" /></Svg>
);

export const Anchor = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="5" r="3" /><path d="M12 22V8" /><path d="M5 12H2a10 10 0 0 0 20 0h-3" /></Svg>
);

export const Award = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="8" r="7" /><path d="M8.21 13.89L7 23l5-3 5 3-1.21-9.12" /></Svg>
);

export const BarChart2 = (p: IconProps) => (
  <Svg {...p}><path d="M18 20V10" /><path d="M12 20V4" /><path d="M6 20v-6" /></Svg>
);

export const Bell = (p: IconProps) => (
  <Svg {...p}><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></Svg>
);

export const Bookmark = (p: IconProps) => (
  <Svg {...p}><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" /></Svg>
);

export const Briefcase = (p: IconProps) => (
  <Svg {...p}><rect x="2" y="7" width="20" height="14" rx="2" /><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" /></Svg>
);

export const Calendar = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4" /><path d="M8 2v4" /><path d="M3 10h18" /></Svg>
);

export const Camera = (p: IconProps) => (
  <Svg {...p}><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" /><circle cx="12" cy="13" r="4" /></Svg>
);

export const Clipboard = (p: IconProps) => (
  <Svg {...p}><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><rect x="8" y="2" width="8" height="4" rx="1" /></Svg>
);

export const Coffee = (p: IconProps) => (
  <Svg {...p}><path d="M18 8h1a4 4 0 0 1 0 8h-1" /><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z" /><path d="M6 1v3M10 1v3M14 1v3" /></Svg>
);

export const DollarSign = (p: IconProps) => (
  <Svg {...p}><path d="M12 1v22" /><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" /></Svg>
);

export const Film = (p: IconProps) => (
  <Svg {...p}><rect x="2" y="2" width="20" height="20" rx="2.18" /><path d="M7 2v20M17 2v20M2 12h20M2 7h5M2 17h5M17 17h5M17 7h5" /></Svg>
);

export const Filter = (p: IconProps) => (
  <Svg {...p}><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" /></Svg>
);

export const Flag = (p: IconProps) => (
  <Svg {...p}><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" /><path d="M4 22v-7" /></Svg>
);

export const Gift = (p: IconProps) => (
  <Svg {...p}><path d="M20 12v10H4V12" /><rect x="2" y="7" width="20" height="5" /><path d="M12 22V7" /><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z" /><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z" /></Svg>
);

export const Grid = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /></Svg>
);

export const Headphones = (p: IconProps) => (
  <Svg {...p}><path d="M3 18v-6a9 9 0 0 1 18 0v6" /><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z" /></Svg>
);

export const Home = (p: IconProps) => (
  <Svg {...p}><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M9 22V12h6v10" /></Svg>
);

export const ImageIcon = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="M21 15l-5-5L5 21" /></Svg>
);

export const Inbox = (p: IconProps) => (
  <Svg {...p}><path d="M22 12h-4l-2 3h-4l-2-3H2" /><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /></Svg>
);

export const LifeBuoy = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="4" /><path d="M4.93 4.93l4.24 4.24M14.83 14.83l4.24 4.24M14.83 9.17l4.24-4.24M4.93 19.07l4.24-4.24" /></Svg>
);

export const MapIcon = (p: IconProps) => (
  <Svg {...p}><path d="M1 6v16l7-4 8 4 7-4V2l-7 4-8-4-7 4z" /><path d="M8 2v16M16 6v16" /></Svg>
);

export const Mic = (p: IconProps) => (
  <Svg {...p}><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><path d="M12 19v4" /><path d="M8 23h8" /></Svg>
);

export const Monitor = (p: IconProps) => (
  <Svg {...p}><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8" /><path d="M12 17v4" /></Svg>
);

export const Music = (p: IconProps) => (
  <Svg {...p}><path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" /></Svg>
);

export const Navigation = (p: IconProps) => (
  <Svg {...p}><path d="M3 11l19-9-9 19-2-9-8-1z" /></Svg>
);

export const Package = (p: IconProps) => (
  <Svg {...p}><path d="M16.5 9.4L7.5 4.21" /><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /><path d="M3.27 6.96L12 12.01l8.73-5.05" /><path d="M12 22.08V12" /></Svg>
);

export const Percent = (p: IconProps) => (
  <Svg {...p}><path d="M19 5L5 19" /><circle cx="6.5" cy="6.5" r="2.5" /><circle cx="17.5" cy="17.5" r="2.5" /></Svg>
);

export const Send = (p: IconProps) => (
  <Svg {...p}><path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4 20-7z" /></Svg>
);

export const Share2 = (p: IconProps) => (
  <Svg {...p}><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="M8.59 13.51l6.83 3.98" /><path d="M15.41 6.51l-6.82 3.98" /></Svg>
);

export const ShoppingBag = (p: IconProps) => (
  <Svg {...p}><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z" /><path d="M3 6h18" /><path d="M16 10a4 4 0 0 1-8 0" /></Svg>
);

export const ShoppingCart = (p: IconProps) => (
  <Svg {...p}><circle cx="9" cy="21" r="1" /><circle cx="20" cy="21" r="1" /><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6" /></Svg>
);

export const Sliders = (p: IconProps) => (
  <Svg {...p}><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" /><path d="M1 14h6M9 8h6M17 16h6" /></Svg>
);

export const Volume2 = (p: IconProps) => (
  <Svg {...p}><path d="M11 5L6 9H2v6h4l5 4V5z" /><path d="M15.54 8.46a5 5 0 0 1 0 7.07" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" /></Svg>
);

export const Target = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" /></Svg>
);

export const ThumbsUp = (p: IconProps) => (
  <Svg {...p}><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" /></Svg>
);

export const TrendingUp = (p: IconProps) => (
  <Svg {...p}><path d="M23 6l-9.5 9.5-5-5L1 18" /><path d="M17 6h6v6" /></Svg>
);

export const Truck = (p: IconProps) => (
  <Svg {...p}><rect x="1" y="3" width="15" height="13" /><path d="M16 8h4l3 3v5h-7V8" /><circle cx="5.5" cy="18.5" r="2.5" /><circle cx="18.5" cy="18.5" r="2.5" /></Svg>
);

export const Video = (p: IconProps) => (
  <Svg {...p}><rect x="1" y="5" width="15" height="14" rx="2" /><path d="M23 7l-7 5 7 5V7z" /></Svg>
);

export const Wifi = (p: IconProps) => (
  <Svg {...p}><path d="M5 12.55a11 11 0 0 1 14.08 0" /><path d="M1.42 9a16 16 0 0 1 21.16 0" /><path d="M8.53 16.11a6 6 0 0 1 6.95 0" /><path d="M12 20h.01" /></Svg>
);

export const Wind = (p: IconProps) => (
  <Svg {...p}><path d="M9.59 4.59A2 2 0 1 1 11 8H2" /><path d="M19.59 7.59A2 2 0 1 1 21 11H11" /><path d="M13.59 15.59A2 2 0 1 1 15 19H2" /></Svg>
);

export const FileText = (p: IconProps) => (
  <Svg {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M16 13H8M16 17H8" /><path d="M10 9H8" /></Svg>
);

export const Printer = (p: IconProps) => (
  <Svg {...p}><path d="M6 9V2h12v7" /><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" /><rect x="6" y="14" width="12" height="8" /></Svg>
);

export const Rss = (p: IconProps) => (
  <Svg {...p}><path d="M4 11a9 9 0 0 1 9 9" /><path d="M4 4a16 16 0 0 1 16 16" /><circle cx="5" cy="19" r="1" /></Svg>
);

export const Scissors = (p: IconProps) => (
  <Svg {...p}><circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="M20 4L8.12 15.88" /><path d="M14.47 14.48L20 20" /><path d="M8.12 8.12L12 12" /></Svg>
);

export const Watch = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="7" /><path d="M12 9v3l1.5 1.5" /><path d="M16.51 17.35l-.35 3.83a12 12 0 0 1-8.32 0l-.35-3.83" /><path d="M7.49 6.65l.35-3.83a12 12 0 0 1 8.32 0l.35 3.83" /></Svg>
);

export const Tv = (p: IconProps) => (
  <Svg {...p}><rect x="2" y="7" width="20" height="15" rx="2" /><path d="M17 2l-5 5-5-5" /></Svg>
);

export const Radio = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="2" /><path d="M16.24 7.76a6 6 0 0 1 0 8.49" /><path d="M7.76 16.24a6 6 0 0 1 0-8.49" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" /><path d="M4.93 19.07a10 10 0 0 1 0-14.14" /></Svg>
);

export const Thermometer = (p: IconProps) => (
  <Svg {...p}><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z" /></Svg>
);

export const Smile = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="10" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><path d="M9 9h.01M15 9h.01" /></Svg>
);

export const Hash = (p: IconProps) => (
  <Svg {...p}><path d="M4 9h16M4 15h16" /><path d="M10 3L8 21M16 3l-2 18" /></Svg>
);

export const Hexagon = (p: IconProps) => (
  <Svg {...p}><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /></Svg>
);
