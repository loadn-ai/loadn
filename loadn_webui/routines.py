"""P11 Routine 模板包 + Heartbeat 巡检。

模板=预制例行（{name, cron 建议, prompt, 所需资源声明}）；「模板库」页
状态 Ready/Needs setup（缺什么一目了然）。**安装=复制为用户 schedule**
（与平台升级解耦——模板改版不影响已装实例）。

Heartbeat=系统级内置 schedule（可关）：30min cron、assistant 档巡检、
三防（忙跳过/空输出不投递不落 transcript/连续 3 轮无产出自动降频）。
降频状态存 job 的 label 前缀（🫀·low）+ 审计，供面板提示。
"""
from __future__ import annotations

from .config import CONFIG

# --------------------------------------------------------------- 模板包（5）
ROUTINES: list[dict] = [
    {
        "key": "morning_brief",
        "name": "晨报",
        "cron": "0 8 * * *",
        "prompt": "生成今日晨报：1) 昨天活跃会话的关键进展（标题+一行）2) 今天到期的"
                  "定时任务清单 3) 待审审批清单。简洁列表输出，无内容的项目直接跳过。",
        "resources": [],
        "description": "每日 08:00 汇总会话进展/待办/待审。",
    },
    {
        "key": "news_watch",
        "name": "资讯/论文巡检",
        "cron": "0 9,21 * * *",
        "prompt": "巡检关注的资讯/论文源（源清单在会话 PROGRESS.md 的「巡检源」节；"
                  "首次运行先向用户要源清单并记录）。有新东西才输出条目并注明来源；"
                  "没有就只回「本轮无更新」。",
        "resources": ["web"],
        "description": "每日两轮外部源扫描，有更新才报告。",
    },
    {
        "key": "cred_budget_check",
        "name": "凭证与预算体检",
        "cron": "0 10 * * 1",
        "prompt": "做凭证与预算体检：1) 平台各资源凭证的 updated_at（vault 列表，"
                  "只看日期不看密钥）列出超过 90 天未更新的 2) 近 7 天成本趋势"
                  "（/api/stats/cost 数据）异常点。只报告事实与建议，不做任何写操作。",
        "resources": [],
        "description": "每周一 10:00 凭证新鲜度+成本异常体检。",
    },
    {
        "key": "day_reminder",
        "name": "日程提醒",
        "cron": "*/30 9-22 * * *",
        "prompt": "检查日程提醒（提醒清单在会话 PROGRESS.md 的「日程」节；首次运行"
                  "先向用户要清单并记录）。到点项输出提醒；没有到点的输出「本轮无提醒」。",
        "resources": ["notify"],
        "description": "工作时间每 30 分钟检查提醒清单，到点推送。",
    },
    {
        "key": "repo_daily",
        "name": "仓库日报",
        "cron": "0 20 * * *",
        "prompt": "为当前默认代码仓库生成日报：git log --since=1.day 的提交分组摘要"
                  "（按功能/修复/测试）、当前分支与未合并 PR 状态、测试基线是否有变化。"
                  "只读操作，报告控制在 20 行内。",
        "resources": [],
        "description": "每日 20:00 仓库活动日报（只读）。",
    },
]

HEARTBEAT_KEY = "__heartbeat__"


def heartbeat_prompt() -> str:
    """巡检 prompt（可经 config channels 覆盖——保持零新增配置面，先内置）。"""
    return (
        "【心跳巡检】快速检查平台状态，正常就只回「OK」加一行摘要：\n"
        "1) 活跃会话是否有 error 终态的 turn（近 30 分钟）\n"
        "2) 待审审批积压数量\n"
        "3) 你上一轮留的 TODO 是否到点。\n"
        "发现需要用户注意的事才展开说明；无事不要长篇输出。")


def template_status(t: dict) -> dict:
    """Ready / Needs setup（缺什么）。资源声明映射到平台配置检测。"""
    missing = []
    for res in t.get("resources") or []:
        if res == "web" and not (CONFIG.resources.proxy or True):
            missing.append("出网能力（resources.proxy）")
        if res == "notify" and not CONFIG.notify.provider:
            missing.append("通知通道（设置→通知）")
    return {"ready": not missing, "missing": missing}
