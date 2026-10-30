import type { ComponentPropsWithoutRef } from 'react';
import Mermaid from '../components/Mermaid';

/**
 * react-markdown 的 code 自定义渲染（v0.7.60）：
 * language-mermaid 块 → <Mermaid/> 渲染成流程图；其余走原生 <pre><code>。
 * 五个 md 渲染面（FilePreview/ChatStream/MemoryTab/SkillEditor/SubtaskTab）
 * 统一挂 components={{ code: mdCode }} 保持一致。
 */
type CodeProps = ComponentPropsWithoutRef<'code'> & { className?: string; node?: unknown };

export function mdCode(props: CodeProps) {
  // node：react-markdown 注入的 hast 节点对象——绝不能透传到 DOM
  // （此前 ...rest 把它吐成 node="[object Object]" 脏属性）
  const { className, children, node: _node, ...rest } = props;
  const lang = /language-(\w+)/.exec(className ?? '')?.[1] ?? '';
  if (lang === 'mermaid' || lang === 'flowchart') {
    const text = String(children ?? '').replace(/\n$/, '');
    if (text.startsWith('flowchart') || text.startsWith('graph ')
        || lang === 'mermaid') {
      return <Mermaid code={text} />;
    }
  }
  return <code className={className} {...rest}>{children}</code>;
}

/** pre 直通：块级 code 渲染成 <Mermaid/> 时去掉 <pre> 包裹（防代码块样式套图上）。 */
export function mdPre({ children }: ComponentPropsWithoutRef<'pre'>) {
  const arr = Array.isArray(children) ? children : [children];
  const only = arr.length === 1 ? (arr[0] as { type?: unknown }) : null;
  if (only && only.type === Mermaid) return <>{children}</>;
  return <pre>{children}</pre>;
}

/** 挂到 ReactMarkdown 的 components 片段（code 与 pre 内联块双形态兼容）。 */
export const mdComponents = { code: mdCode, pre: mdPre };
