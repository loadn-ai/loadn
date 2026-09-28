/** 轻量线性图标集（feather 风格，24 viewBox / currentColor）——替代按钮里的 emoji，
 *  保证明暗主题、各平台渲染一致。size 走 props，默认 16。 */
import type { ReactNode } from 'react';

type IconProps = { size?: number; className?: string };

function Svg({ size = 16, className, strokeWidth = 1.9, children }: IconProps & {
  children: ReactNode; strokeWidth?: number;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true" className={className}>{children}</svg>
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
  <Svg {...p}><rect x="4" y="8" width="16" height="12" rx="3" /><path d="M12 8V4" /><circle cx="12" cy="3" r="1" /><path d="M9 13v1M15 13v1" /></Svg>
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

export const Box = (p: IconProps) => (
  <Svg {...p}><path d="M21 8l-9-5-9 5v8l9 5 9-5V8z" /><path d="M3.3 8.3L12 13l8.7-4.7" /><path d="M12 22V13" /></Svg>
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
