// 全局 Toast 浮层（AC-5.5）：右上角堆叠，成功绿/失败红，2.6s 自清，点击即散
import { useToasts } from '../stores/toasts';

export default function Toasts() {
  const toasts = useToasts(s => s.toasts);
  const dismiss = useToasts(s => s.dismiss);
  if (!toasts.length) return null;
  return (
    <div className="toast-stack">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.ok ? '' : 'err'}`}
          onClick={() => dismiss(t.id)}>{t.text}</div>
      ))}
    </div>
  );
}
