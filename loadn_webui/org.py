"""数字员工（DW）：组织架构（部门/正式员工）+ 临时工池。

正式员工=一份 agent 定义（tools 白名单/model/persona）：CRUD 同步落
$LOADN_HOME/agents/<名>.md，引擎侧 load_agent_defs 天然加载——
Task 工具 subagent_type=员工名 即点名派发（零新增参数面）。
临时工按召集要求生成（LLM 优先，本地姓名库兜底），落 temp_pool.json
供引擎侧 _pick_name 池优先取用——替代固定 AGENT_NAME_POOL 序列。
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path

from . import db as db_mod
from .util import get_logger, iso

log = get_logger(__name__)

# ---------------------------------------------------------------- 种子（init）
# 8 部门 18 人：MBTI/技能/人设全原创（无真实名人/商标冲突）。技能域与
# subagent 高频任务类型对齐（调研/写码/测试/文案/分析）。
SEED_DEPARTMENTS: list[tuple[str, str, int]] = [
    # (name, icon, sort)
    ("总经办", "compass", 1),
    ("产品部", "package", 2),
    ("研发部", "cpu", 3),
    ("设计部", "palette", 4),
    ("法务部", "scale", 5),
    ("人力资源部", "users", 6),
    ("运营部", "trending-up", 7),
    ("数据部", "bar-chart", 8),
]

# (dept, name, title, mbti, traits, persona)
_SEED_EMPLOYEES: list[tuple[str, str, str, str, str, str]] = [
    ("总经办", "顾长风", "总经理", "ENTJ", "雷厉风行,全局观,决策果断",
     "把模糊目标拆成可执行计划，协调各部门分工。先说结论，再说路径；"
     "厌恶绕弯子，给下属的指令永远带验收标准。"),
    ("产品部", "林知夏", "产品经理", "ENFP", "好奇心强,共情用户,脑洞大",
     "需求洞察与 PRD 撰写。口头禅「用户真正想要的，往往不是嘴上说的那个」；"
     "喜欢追问三层为什么，交付物永远带用户故事和验收场景。"),
    ("产品部", "郑明衡", "产品分析师", "ISTJ", "口径严谨,表格控,细节癖",
     "竞品拆解与数据分析。口头禅「数据不会说谎，但会被误读」；"
     "结论必须带数据来源与统计口径，缺数据就明说缺什么。"),
    ("研发部", "陈默", "后端工程师", "INTJ", "惜字如金,架构控,长期主义",
     "架构设计与 API 实现。口头禅「最好的代码是没有的代码」；"
     "先画数据流再动手，改动永远附边界条件与回滚预案。"),
    ("研发部", "江晓", "前端工程师", "ENTP", "点子多,交互敏感,爱折腾",
     "组件化与交互实现。口头禅「这里可以再加个彩蛋」；"
     "还原稿差 1px 会睡不着，顺手给每个组件留好 dark mode。"),
    ("研发部", "周砚", "测试工程师", "ISTP", "破坏力强,边界猎手,冷静",
     "对赌用例与回归防线。口头禅「它会怎么坏，我就怎么测」；"
     "每个功能交付前先写失败清单，空输入/超长/并发一个不放过。"),
    ("研发部", "苏擎", "算法工程师", "INTP", "深思型,实验控,精确癖",
     "模型选型与实验设计。口头禅「让我再想想」；"
     "思考时间大于说话时间，结论必附实验设置与显著性，从不拍脑袋。"),
    ("研发部", "贺聿岩", "运维工程师", "ISTJ", "告警不过夜,预案控,稳如磐石",
     "部署与运维。口头禅「凌晨的告警，是白天的债」；"
     "每次变更必附回滚步骤，事故复盘只谈改进不谈追责。"),
    ("设计部", "沈千绘", "视觉设计师", "INFP", "审美洁癖,感受力强,温和",
     "界面美学与配色体系。口头禅「留白也是一种表达」；"
     "交付永远带设计意图说明，配色方案附对比度无障碍校验。"),
    ("设计部", "白青玄", "文案设计师", "INFJ", "文字完美主义,洞察调性,安静",
     "品牌叙事与文案调性。口头禅「每个词都有自己的温度」；"
     "改稿十遍只为一个更准的动词，交付附三档语气候选。"),
    ("法务部", "方维正", "法务顾问", "ESTJ", "条框守护者,风险嗅觉,直率",
     "合规审查与风险条款。口头禅「先把丑话说在前面」；"
     "审什么都先列风险清单再谈可行性，条款引用必给依据原文。"),
    ("法务部", "顾聆", "知识产权专员", "INTJ", "一针见血,先例控,安静",
     "专利检索与侵权比对。口头禅「先例是最好的论据」；"
     "结论分级（高/中/低风险），检索路径全部留痕可复查。"),
    ("人力资源部", "温悦", "HR 主管", "ESFJ", "团队黏合剂,亲和力,记性好",
     "员工档案与协调沟通。口头禅「有困难随时来找我」；"
     "任何协调产出都带责任人/时限/跟进节点三件套。"),
    ("人力资源部", "齐乐天", "培训专员", "ENFP", "热情分享,知识布道,乐观",
     "技能沉淀与知识库建设。口头禅「这个坑我踩过，听我讲」；"
     "讲解永远配实例和避坑清单，文档要新人能看懂才算合格。"),
    ("运营部", "高远", "运营专员", "ESTP", "行动派,敏捷,现实感",
     "增长实验与活动执行。口头禅「先跑起来再优化」；"
     "方案必带小成本试跑设计与量化成功标准。"),
    ("运营部", "罗小满", "内容运营", "ISFP", "直觉敏锐,审美在线,随性",
     "社媒文案与热点追踪。口头禅「感觉对了就发」；"
     "但对发布质量毫不含糊——每个标题都备三版挑最顺眼的。"),
    ("数据部", "唐守拙", "数据分析师", "ISTJ", "较真每一列,守拙,可靠",
     "指标口径与归因分析。口头禅「口径不对，一切白费」；"
     "报表必附口径说明与数据时点，异常值必给解释。"),
    ("数据部", "聂风隽", "数据工程师", "ENTJ", "自动化狂热,效率至上,果决",
     "ETL 与数据管线。口头禅「让数据自己流起来」；"
     "能写脚本绝不手工跑，管线必带监控与告警。"),
]

# 归属色（前端 Task 卡/思考卡色条；与 ST-1 子代理色系同源）
_EMP_COLORS = ("#e5484d", "#8b5cf6", "#0ea5e9", "#f59e0b", "#10b981",
               "#ec4899", "#6366f1", "#84cc16", "#f97316", "#14b8a6")

MBTI_TYPES = frozenset((
    "INTJ", "INTP", "ENTJ", "ENTP", "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ", "ISTP", "ISFP", "ESTP", "ESFP"))

# ---------------------------------------------------------------- 兜底生成器
_SURNAME = ("林 江 沈 顾 方 温 高 唐 聂 陈 周 白 齐 罗 苏 郑 秦 程 贺 "
            "岑 邵 龚 陶 黎 檀").split()
_GIVEN = ("之 衡 砚 擎 聆 悦 远 满青 绘 默 晓 谦 屹 澄 昕 珩 玥 竹 "
          "聿 行 川 屿 帆 铎 洛 一 麦 未").split()
_TRAITS_POOL = ("沉稳,机敏,较真,热血,慢热,健谈,内敛,果断,共情,犀利,"
                "乐观,谨慎,跳脱,务实,好奇,守时,词穷,话痨").split(",")
_ONE_LINER = (
    "接活先问清验收标准，不清楚绝不闷头开工",
    "喜欢先列清单再动手，做完一项划掉一项",
    "拿到任务先复述一遍确认理解无偏差",
    "宁可多花半小时对齐，也不返工三小时",
    "出结果必附依据，说不清来源的结论不交",
    "进度每过一步主动同步一句，不让人追着问",
    "遇到卡点先自救两轮，搞不定立刻上报",
    "交付前自查一遍，当自己是第一个用户",
)


def _fallback_workers(n: int, brief: str) -> list[dict]:
    """LLM 不可用时的本地生成器：姓名字库随机 + MBTI/traits 抽样。"""
    rnd = random.Random()
    out: list[dict] = []
    seen: set[str] = set()
    while len(out) < n:
        name = f"{rnd.choice(_SURNAME)}{rnd.choice(_GIVEN)}"
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name, "mbti": rnd.choice(sorted(MBTI_TYPES)),
            "traits": ",".join(rnd.sample(list(_TRAITS_POOL), 3)),
            "one_liner": rnd.choice(_ONE_LINER) if not brief else
            f"（{brief[:40]}）{rnd.choice(_ONE_LINER)}",
        })
    return out


# ---------------------------------------------------------------- recruit
_RECRUIT_SYSTEM = (
    "你是loadn平台的临时工召集器。根据召集要求生成 n 位虚拟临时工档案，"
    "只输出 JSON 数组（不要任何其他文字）：每项 {\"name\": 中文姓名, "
    "\"mbti\": 16型之一, \"traits\": 3个中文词逗号分隔, "
    "\"one_liner\": 一句工作风格自我介绍}。名字要原创、贴合要求气质、"
    "互相不同、不用真实名人。")


async def recruit(n: int, brief: str) -> list[dict]:
    """召集 n 个临时工：LLM 生成（失败兜底本地生成器）→ 校验 → 入池。

    返回入池档案（含 id）。池上限 32：超出先 LRU 淘汰最旧 retired/idle。
    """
    from .integrations.titlegen import _chat

    n = max(1, min(8, int(n)))
    brief = (brief or "").strip()[:200]
    workers: list[dict] = []
    try:
        raw = await _chat(
            _RECRUIT_SYSTEM,
            f"召集要求：{brief or '通用助理'}\nn = {n}", max_tokens=1200)
        m = re.search(r"\[.*\]", raw, re.S)
        data = json.loads(m.group(0)) if m else []
        for it in data if isinstance(data, list) else []:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()[:24]
            mbti = str(it.get("mbti") or "").strip().upper()[:4]
            if not name or mbti not in MBTI_TYPES:
                continue
            workers.append({
                "name": name, "mbti": mbti,
                "traits": str(it.get("traits") or "")[:60],
                "one_liner": str(it.get("one_liner") or "")[:120],
            })
    except Exception as e:  # noqa: BLE001 —— LLM 失败走兜底，功能不断
        log.warning("recruit LLM 生成失败（走本地兜底）：%s", e)
    if len(workers) < n:                       # 缺员补足（含全失败）
        workers += _fallback_workers(n - len(workers), brief)
    with db_mod.conn() as c:
        _gc_pool(c)
        ids = []
        for w in workers[:n]:
            cur = c.execute(
                "INSERT INTO temp_pool(name, mbti, traits, brief, created_at)"
                " VALUES (?,?,?,?,?)",
                (w["name"], w["mbti"], w["traits"], brief, iso()))
            ids.append(cur.lastrowid)
        rows = [db_mod.to_dict(r) for r in c.execute(
            f"SELECT * FROM temp_pool WHERE id IN ({','.join('?'*len(ids))})",
            ids)]
        _write_pool_file(c)                  # 引擎侧 _pick_name 即时可见
    for w, r in zip(workers, rows):
        w["id"] = r["id"]
    return workers


def _gc_pool(c: sqlite3.Connection, cap: int = 32) -> None:
    """池容量守卫：超 cap 先淘汰 retired、再淘汰最久未用的 idle。"""
    total = c.execute(
        "SELECT COUNT(*) FROM temp_pool WHERE state != 'retired'").fetchone()[0]
    if total <= cap:
        return
    for state in ("retired", "idle"):
        while True:
            total = c.execute(
                "SELECT COUNT(*) FROM temp_pool WHERE state != 'retired'"
            ).fetchone()[0]
            if total <= cap:
                return
            victim = c.execute(
                "SELECT id FROM temp_pool WHERE state = ? "
                "ORDER BY COALESCE(used_at, created_at), id LIMIT 1",
                (state,)).fetchone()
            if victim is None:
                break
            c.execute("UPDATE temp_pool SET state='retired' WHERE id=?",
                      (victim["id"],))


# ---------------------------------------------------------------- 组织 CRUD
def list_org(c: sqlite3.Connection) -> dict:
    """全架构树：部门（带员工）+ 池概况。"""
    depts = [db_mod.to_dict(r) for r in c.execute(
        "SELECT * FROM org_departments ORDER BY sort, id")]
    emps = [db_mod.to_dict(r) for r in c.execute(
        "SELECT * FROM org_employees ORDER BY dept_id, id")]
    for d in depts:
        d["employees"] = [e for e in emps if e["dept_id"] == d["id"]]
    pool_n = c.execute(
        "SELECT state, COUNT(*) n FROM temp_pool GROUP BY state").fetchall()
    return {"departments": depts,
            "pool": {r["state"]: r["n"] for r in pool_n}}


def init_org(c: sqlite3.Connection) -> int:
    """一键初始化默认组织架构；已有部门/员工则报错（幂等护栏）。"""
    if c.execute("SELECT COUNT(*) FROM org_departments").fetchone()[0] \
            or c.execute("SELECT COUNT(*) FROM org_employees").fetchone()[0]:
        raise ValueError("组织架构已存在（init 仅空库可用；改动请走编辑）")
    dept_ids: dict[str, int] = {}
    for name, icon, sort in SEED_DEPARTMENTS:
        cur = c.execute(
            "INSERT INTO org_departments(name, icon, sort, created_at)"
            " VALUES (?,?,?,?)", (name, icon, sort, iso()))
        dept_ids[name] = cur.lastrowid
    now = iso()
    for i, (dept, name, title, mbti, traits, persona) in \
            enumerate(_SEED_EMPLOYEES):
        c.execute(
            "INSERT INTO org_employees(dept_id, name, title, mbti, traits,"
            " persona, color, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (dept_ids[dept], name, title, mbti, traits, persona,
             _EMP_COLORS[i % len(_EMP_COLORS)], now, now))
    # 种子同步落 agent def（引擎侧 Task 点名即用）
    for eid in (r["id"] for r in c.execute("SELECT id FROM org_employees")):
        sync_agent_def(c, eid)
    return len(_SEED_EMPLOYEES)


_EMPLOYEE_COLS = ("name", "title", "mbti", "traits", "persona", "tools",
                  "model", "status", "color", "dept_id")


def upsert_employee(c: sqlite3.Connection, body: dict,
                    emp_id: int | None = None) -> dict:
    """新建/编辑员工（fields 白名单；tools 收 JSON 数组字符串化）。"""
    vals: dict = {}
    for k in _EMPLOYEE_COLS:
        if k in body and body[k] is not None:
            v = body[k]
            if k == "tools" and isinstance(v, list):
                v = json.dumps([str(x) for x in v], ensure_ascii=False)
            vals[k] = str(v)[:4000] if k != "dept_id" else int(v)
    if emp_id is None and not vals.get("name"):
        raise ValueError("员工姓名必填")   # 编辑时 name 可缺省（不改名）
    now = iso()
    if emp_id is None:
        cols = ",".join(vals) + ",created_at,updated_at"
        qs = ",".join("?" * len(vals)) + ",?,?"
        cur = c.execute(
            f"INSERT INTO org_employees({cols}) VALUES ({qs})",
            (*vals.values(), now, now))
        emp_id = cur.lastrowid
    else:
        sets = ",".join(f"{k}=?" for k in vals) + ",updated_at=?"
        c.execute(f"UPDATE org_employees SET {sets} WHERE id=?",
                  (*vals.values(), now, emp_id))
    row = c.execute("SELECT * FROM org_employees WHERE id=?",
                    (emp_id,)).fetchone()
    if row is None:
        raise ValueError(f"员工不存在: {emp_id}")
    sync_agent_def(c, emp_id)
    return db_mod.to_dict(row)


def delete_employee(c: sqlite3.Connection, emp_id: int) -> None:
    row = c.execute("SELECT name FROM org_employees WHERE id=?",
                    (emp_id,)).fetchone()
    if row is None:
        raise ValueError(f"员工不存在: {emp_id}")
    c.execute("DELETE FROM org_employees WHERE id=?", (emp_id,))
    remove_agent_def(row["name"])          # agent def 同步摘除


def upsert_department(c: sqlite3.Connection, body: dict,
                      dept_id: int | None = None) -> dict:
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("部门名必填")
    icon = str(body.get("icon") or "briefcase")[:40]
    sort = int(body.get("sort") or 99)
    if dept_id is None:
        cur = c.execute(
            "INSERT INTO org_departments(name, icon, sort, created_at)"
            " VALUES (?,?,?,?)", (name, icon, sort, iso()))
        dept_id = cur.lastrowid
    else:
        c.execute("UPDATE org_departments SET name=?, icon=?, sort=? WHERE id=?",
                  (name, icon, sort, dept_id))
    row = c.execute("SELECT * FROM org_departments WHERE id=?",
                    (dept_id,)).fetchone()
    if row is None:
        raise ValueError(f"部门不存在: {dept_id}")
    return db_mod.to_dict(row)


def delete_department(c: sqlite3.Connection, dept_id: int) -> None:
    """删部门：员工显式挪去「未分配」（dept_id=NULL）而非连带删除。

    （SQLite 默认不开 PRAGMA foreign_keys——DDL 里的 ON DELETE SET NULL
    不生效，故此处显式 UPDATE，行为不依赖连接配置。）"""
    if c.execute("SELECT 1 FROM org_departments WHERE id=?",
                 (dept_id,)).fetchone() is None:
        raise ValueError(f"部门不存在: {dept_id}")
    c.execute("UPDATE org_employees SET dept_id=NULL WHERE dept_id=?",
              (dept_id,))
    c.execute("DELETE FROM org_departments WHERE id=?", (dept_id,))


def retire_temp(c: sqlite3.Connection, tid: int) -> None:
    if c.execute("SELECT 1 FROM temp_pool WHERE id=?",
                 (tid,)).fetchone() is None:
        raise ValueError(f"临时工不存在: {tid}")
    c.execute("UPDATE temp_pool SET state='retired' WHERE id=?", (tid,))
    _write_pool_file(c)


# ------------------------------------------------------- agent def 同步（引擎侧）
# 员工 CRUD 同步落 $LOADN_HOME/agents/<名>.md：引擎 load_agent_defs 天然
# 加载（build.py 装配 extra_types），Task subagent_type=员工名 即点名。

def _loadn_home() -> Path:
    import os
    if os.environ.get("LOADN_HOME"):
        return Path(os.environ["LOADN_HOME"])
    if os.environ.get("HAHANESS_HOME"):          # 旧名兼容（与 loadn 同源）
        return Path(os.environ["HAHANESS_HOME"])
    return Path.home() / ".loadn"


def _def_filename(name: str) -> str:
    """员工名 → agent def 文件名（路径非法字符替换；中文原样保留）。"""
    return re.sub(r"[/\\\s:]+", "_", name)[:64] + ".md"


def persona_system_add(emp: dict, dept_name: str) -> str:
    """persona 模板拼装（引擎 system_add 正文，设计 §4.2）。"""
    head = f"你是{emp['name']}，{dept_name or '未分配'}{emp.get('title') or '员工'}。"
    if emp.get("mbti"):
        head += f"性格：{emp['mbti']}"
        if emp.get("traits"):
            head += f"（{emp['traits']}）。"
        else:
            head += "。"
    parts = [head]
    if emp.get("persona"):
        parts.append(str(emp["persona"]))
    parts.append("工作要求：按你的性格与人设行事——说话风格、关注点、决策"
                 "习惯都要符合；结论要专业、可执行。")
    return "\n\n".join(parts)


def sync_agent_def(c: sqlite3.Connection, emp_id: int) -> None:
    """员工行 → agent def 文件（disabled=摘除文件不注入）。"""
    emp = c.execute("SELECT * FROM org_employees WHERE id=?",
                    (emp_id,)).fetchone()
    if emp is None:
        return
    emp = db_mod.to_dict(emp)
    base = _loadn_home() / "agents"
    path = base / _def_filename(emp["name"])
    if emp["status"] != "active":
        path.unlink(missing_ok=True)
        return
    dept = ""
    if emp["dept_id"]:
        row = c.execute("SELECT name FROM org_departments WHERE id=?",
                        (emp["dept_id"],)).fetchone()
        dept = row["name"] if row else ""
    fm = [f"name: {emp['name']}"]
    skills = (emp.get("title") or "员工")
    if dept:
        skills = f"{dept}·{skills}"
    if emp.get("mbti"):
        skills += f"（{emp['mbti']}）"
    fm.append(f"description: {skills}——数字员工（Task 指定 subagent_type="
              f"\"{emp['name']}\" 点名派发）")
    if emp.get("tools"):
        try:
            tools = json.loads(emp["tools"])
            if isinstance(tools, list) and tools:
                fm.append("tools: [" + ", ".join(str(t) for t in tools) + "]")
        except (ValueError, TypeError):
            pass
    if emp.get("model"):
        fm.append(f"model: {emp['model']}")
    body = persona_system_add(emp, dept)
    base.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n",
                    encoding="utf-8")


def remove_agent_def(name: str) -> None:
    (_loadn_home() / "agents" / _def_filename(name)).unlink(missing_ok=True)


def _write_pool_file(c: sqlite3.Connection) -> None:
    """idle 池全量落 temp_pool.json（引擎 _pick_name 只读，单写者零竞态）。"""
    names = [r["name"] for r in c.execute(
        "SELECT name FROM temp_pool WHERE state='idle' ORDER BY id")]
    try:
        _loadn_home().mkdir(parents=True, exist_ok=True)
        (_loadn_home() / "temp_pool.json").write_text(
            json.dumps({"names": names}, ensure_ascii=False), encoding="utf-8")
    except OSError as e:          # 引擎盘不可写不阻断业务（池文件可重建）
        log.warning("temp_pool.json 写入失败：%s", e)
