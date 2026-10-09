// 子代理注册表（多 Agent 工作台）：从消息 blocks / SSE 事件派生 agent 清单。
// 身份是 turn 域——SubagentManager 每 turn 重建，跨 turn 的 sub_1 是不同人，
// 键必须 (turnId, agentId) 复合（引擎侧 build.py 每 turn 新建 manager）。
import type { MessageInfo, ToolEvent, TurnInfo } from './sessions';

export const SUB_TAG_RE = /^子(\d+)·/;

/** 引擎 AGENT_NAME_POOL 镜像（loadn/constants.py，顺序敏感：兜底取名公式
 *  (n-1)%len 必须与引擎 subagent.py 一致——旧数据无 agent_name 时确定性
 *  retro 命名）。同步纪律：tests/contract/test_agent_name_pool.py 对赌。 */
export const AGENT_NAME_POOL: readonly string[] = [
  // 侦探/调研系
  '马洛', '波洛', '布朗', '梅格雷', '马普尔', '阿彻',
  '斯佩德', '昆恩', '万斯', '华生', '霍桑', '雷斯垂德',
  // 科研/工程系（三体/基地/银河系漫游指南/我，机器人）
  '谢顿', '罗辑', '汪淼', '章北海', '丁仪', '云天明',
  '史强', '韦德', '叶文洁', '程心', '关一帆', '凯文',
  // 航海/实干系（凡尔纳/海明威/麦尔维尔/大仲马/银英）
  '尼摩', '阿龙纳斯', '康塞尔', '圣地亚哥', '以实玛利', '法利亚',
  '格列佛', '杨威利', '安德', '马文',
  // 东方侠义/写实系（金庸/西游/骆驼祥子/平凡的世界）
  '黄蓉', '风清扬', '沙僧', '韦小宝', '令狐冲', '唐僧',
  '悟空', '祥子', '孙少平', '孙少安',
];

export interface AgentInfo {
  /** `${turnId}:${agentId}` —— turn 域复合键（跨 turn 不串台） */
  key: string;
  turnId: number;
  agentId: string;          // sub_N
  n: number;
  /** 引擎人名（AGENT_NAME_POOL）；旧数据回退「子N」 */
  name: string;
  role?: string;            // Task input.description（3-5 词用途标签）
  type?: string;            // subagent_type（general/explore/plan/自定义）
  status: 'running' | 'done' | 'error';
  lastTool?: string;        // 最近转发的工具名（子N·X 的 X）
}

/** blocks/事件里的 agent 归属（新结构化字段优先，旧数据回退解析 name/id） */
export interface AgentRef {
  agentId: string; n: number;
  name?: string; role?: string; type?: string;
}

function numFromSub(id: string): number | null {
  const m = /^sub_(\d+)$/.exec(id || '');
  return m ? Number(m[1]) : null;
}

type ToolLike = Partial<ToolEvent> & {
  id?: string | null; name?: string | null;
  agent_id?: string | null; agent_name?: string | null; agent_n?: number | null;
  agent_role?: string | null; subagent_type?: string | null;
};

export function agentOf(tool: ToolLike): AgentRef | null {
  const name = tool.name || '';
  if (tool.agent_id) {
    const n = tool.agent_n ?? numFromSub(tool.agent_id);
    if (n == null) return null;
    return { agentId: tool.agent_id, n,
             name: tool.agent_name || undefined,
             role: tool.agent_role || undefined,
             type: tool.subagent_type || undefined };
  }
  // 旧数据回退：Task 卡的 id 即 sub_N；子活动靠「子N·」前缀
  if (name === 'Task') {
    const n = numFromSub(tool.id || '');
    return n == null ? null : { agentId: `sub_${n}`, n };
  }
  const m = SUB_TAG_RE.exec(name);
  return m ? { agentId: `sub_${m[1]}`, n: Number(m[1]) } : null;
}

/** SSE tool_use 增量：Task 卡 upsert 人名/角色；子N· 活动刷新 lastTool。
 *  无 agent 归属的普通工具原样返回（同引用——不触发订阅重渲染）。 */
export function applyTool(list: AgentInfo[], turnId: number, tool: ToolLike): AgentInfo[] {
  const ref = agentOf(tool);
  if (!ref) return list;
  const key = `${turnId}:${ref.agentId}`;
  const idx = list.findIndex(a => a.key === key);
  const base = idx >= 0 ? list[idx]
    : { key, turnId, agentId: ref.agentId, n: ref.n,
        name: ref.name || AGENT_NAME_POOL[(ref.n - 1) % AGENT_NAME_POOL.length],
        status: 'running' as const };
  const next: AgentInfo = { ...base };
  if (ref.name) next.name = ref.name;
  if (ref.role) next.role = ref.role;
  if (ref.type) next.type = ref.type;
  if (tool.name && SUB_TAG_RE.test(tool.name)) {
    next.lastTool = tool.name.slice(SUB_TAG_RE.exec(tool.name)![0].length);
  }
  if (idx >= 0) {
    if (Object.is(list[idx], next)) return list;
    const out = list.slice();
    out[idx] = next;
    return out;
  }
  return [...list, next];
}

/** SSE tool_result 增量：Task 结束卡（id=sub_N）落终态；普通结果原样返回。 */
export function applyResult(list: AgentInfo[], turnId: number,
                            id: string | null, isError: boolean): AgentInfo[] {
  if (!id || !numFromSub(id)) return list;
  const idx = list.findIndex(a => a.turnId === turnId && a.agentId === id);
  if (idx < 0) return list;
  const status = isError ? 'error' as const : 'done' as const;
  if (list[idx].status === status) return list;
  const out = list.slice();
  out[idx] = { ...out[idx], status };
  return out;
}

const TERMINAL = new Set(['done', 'error', 'stopped', 'interrupted']);

/** 历史 messages 一次派生（openSession / 终态重拉后重建）。
 *  terminalTurns：已终态的 turn id 集——其中无结束卡的 agent 标 error
 *  （turn 已收尾而 agent 未正常结束）；live turn 里的保持 running。 */
export function deriveAgents(
  messages: Pick<MessageInfo, 'turn_id' | 'role' | 'blocks_json'>[],
  turns?: Pick<TurnInfo, 'id' | 'status'>[],
): AgentInfo[] {
  const terminal = new Set((turns ?? []).filter(t => TERMINAL.has(t.status)).map(t => t.id));
  let list: AgentInfo[] = [];
  for (const m of messages) {
    if (m.role !== 'assistant' || !m.blocks_json) continue;
    let blocks: unknown[];
    try { blocks = JSON.parse(m.blocks_json); } catch { continue; }
    for (const b of blocks) {
      if (!b || typeof b !== 'object') continue;
      const blk = b as ToolLike & { result?: string; is_error?: boolean };
      if ((blk as { type?: string }).type !== 'tool') continue;
      list = applyTool(list, m.turn_id ?? 0, blk);
      if (blk.result != null || blk.is_error != null) {
        list = applyResult(list, m.turn_id ?? 0, blk.id ?? null, !!blk.is_error);
      }
    }
  }
  // 终态 turn 里没有结束卡的 agent：turn 收尾而 agent 未落终态 → error
  return list.map(a => a.status === 'running' && terminal.has(a.turnId)
    ? { ...a, status: 'error' as const } : a);
}
