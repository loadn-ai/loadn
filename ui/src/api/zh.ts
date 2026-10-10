// skill 卡片中文简介：无汉字的描述批量送译（后端 doubao 小模型 + kv 缓存，二次秒回）
import { api } from './client';

export async function fetchZhDesc(
  items: { name: string; description: string }[],
): Promise<Record<string, string> | null> {   // null=翻译服务不可用（AC-5.10c：调用方可降级提示）
  const need = items
    .filter(x => x.name && x.description && !/[一-鿿]/.test(x.description))
    .slice(0, 30);
  if (!need.length) return {};
  try {
    const d = await api<{ items: { name: string; zh: string | null }[] }>(
      '/api/skills/translate', { method: 'POST', body: JSON.stringify({ items: need }) });
    const m: Record<string, string> = {};
    for (const it of d.items) if (it.zh) m[it.name] = it.zh;
    return m;
  } catch { return null; }   // 翻译不可用——显示原文，由调用方提示
}
