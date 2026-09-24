#!/usr/bin/env python3
"""场景 evals 发布门（P3-2，pi evals harness 同构）：CC 方言演进防破。

形态：场景 yaml（任务/初始文件/工具脚本轮/判定器）→ ScriptedProvider
回放工具调用轮 → 真 AgentCore 跑（真权限引擎/真工具/真 transcript——
只有模型是 scripted，方言全链路真实）。判定器=**确定性**（文件内容
断言 / transcript 事件断言，无模型评审——零 token 且可重复）。

- 场景目录 evals/scenarios/*.yaml；suite=目录（smoke=冒烟子集，场景
  yaml 里 `suites: [smoke]` 标记）
- 报告 markdown 落 evals/reports/<ts>-<suite>.md（快照工件，复用台账
  习惯）；exit 0=全绿 / 1=有红（发布阻断）
- 用法：python evals/run.py --suite smoke [--scenarios name1,name2]
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# helpers（ScriptedProvider/tool_round）经 importlib 载入（tests 包不在
# sys.path 的运行形态）
_spec = importlib.util.spec_from_file_location(
    "loadn_evals_helpers", REPO / "tests" / "helpers.py")
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def load_scenarios(suite: str) -> list[dict]:
    import yaml
    out = []
    for p in sorted(SCENARIO_DIR.glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            out.append({"name": p.stem, "error": f"yaml 解析失败：{e}"})
            continue
        d.setdefault("name", p.stem)
        if suite == "all" or suite in (d.get("suites") or ["smoke"]):
            out.append(d)
    return out


# ---------------------------------------------------------------- 判定器
def judge_files(ws: Path, checks: list[dict]) -> list[str]:
    """确定性文件断言：[{path, contains|equals|missing, all?}]。"""
    problems = []
    for c in checks:
        p = ws / c["path"]
        if c.get("missing"):
            if p.exists():
                problems.append(f"{c['path']} 应不存在但存在")
            continue
        if not p.exists():
            problems.append(f"{c['path']} 缺失")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for needle in c.get("contains") or ([c["contains_one"]] if c.get(
                "contains_one") else []):
            if needle not in text:
                problems.append(f"{c['path']} 不含 {needle!r}")
        if c.get("equals") is not None and text != c["equals"]:
            problems.append(f"{c['path']} 内容不等于预期")
    return problems


def judge_transcript(session, checks: list[dict]) -> list[str]:
    """transcript 事件断言：[{type, count_min?}]。"""
    problems = []
    events = session.transcript.read_events()
    for c in checks:
        n = sum(1 for e in events if e.get("type") == c["type"])
        if c.get("count_min", 1) > n:
            problems.append(f"transcript {c['type']} 期望 ≥{c.get('count_min', 1)} 实得 {n}")
    return problems


# ---------------------------------------------------------------- 运行器
async def run_scenario(sc: dict) -> dict:
    """跑一个场景：初始文件 → scripted 工具轮 → 判定。"""
    name = sc["name"]
    tmp = Path(tempfile.mkdtemp(prefix=f"eval_{name}_"))
    ws = tmp / "ws"
    ws.mkdir(parents=True)
    import os
    os.environ["LOADN_HOME"] = str(tmp / "home")
    os.environ["LOADN_TOOL_REPAIR"] = "0"          # 场景自控，不叠修复
    os.environ["LOADN_REPOMAP_TOKENS"] = "0"

    # 初始文件
    for rel, content in (sc.get("files") or {}).items():
        f = ws / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")

    # scripted 工具轮（tools 键=name → (args,) 列表）。工具按进程 cwd 解析
    # 相对路径——场景 yaml 的相对 file_path 统一锚到 ws（绝对化）
    def _abs_input(tool: str, input_: dict) -> dict:
        out = dict(input_)
        for k in ("file_path", "notebook_path"):
            if k in out and isinstance(out[k], str) \
                    and not Path(out[k]).is_absolute():
                out[k] = str(ws / out[k])
        return out

    rounds = []
    for turn in sc.get("script", []):
        chunks = []
        for call in turn:
            if isinstance(call, str):              # 纯文本回复
                chunks.extend(_text_chunks(call))
                continue
            chunks.extend(H.tool_round(
                f"ev_{len(rounds)}_{call['tool']}", call["tool"],
                _abs_input(call["tool"], call.get("input") or {})))
        rounds.append(chunks)
    if sc.get("final_text", True):
        rounds.append(_text_chunks(sc.get("final_text_text", "(done)")))

    provider = H.ScriptedProvider(rounds)
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.permissions import PermissionEngine
    from loadn.core.session import SessionManager
    session = SessionManager.create(ws, home=Path(os.environ["LOADN_HOME"]))
    tools = {}
    for tname in sc.get("tools_enabled",
                        ["Write", "Edit", "Read", "TodoWrite"]):
        mod = {
            "Write": "loadn.tools.write", "Edit": "loadn.tools.edit",
            "Read": "loadn.tools.read", "TodoWrite": "loadn.tools.todowrite",
            "MultiEdit": "loadn.tools.multiedit", "Bash": "loadn.tools.bash",
        }.get(tname)
        if mod:
            tools[tname] = importlib.import_module(mod).tool
    core = AgentCore(
        provider=provider, tools=tools, session=session, cwd=ws,
        settings=LoopSettings(max_turns=sc.get("max_turns", 10)),
        permissions=PermissionEngine(
            mode="bypassPermissions",
            deny=sc.get("deny_tools") or []))
    t0 = time.time()
    try:
        summary = await core.run_turn(sc.get("prompt", "执行任务"))
        err = None
    except Exception as e:                              # noqa: BLE001
        summary, err = None, repr(e)

    problems = []
    if err:
        problems.append(f"run_turn 异常：{err}")
    else:
        problems += judge_files(ws, sc.get("assert_files") or [])
        problems += judge_transcript(session, sc.get("assert_events") or [])
        if sc.get("assert_result_subtype"):
            st = getattr(summary, "subtype", "")
            if st != sc["assert_result_subtype"]:
                problems.append(f"result.subtype={st} 期望 {sc['assert_result_subtype']}")
    return {"name": name, "ok": not problems, "problems": problems,
            "duration_s": round(time.time() - t0, 2)}


def _text_chunks(text: str):
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk
    return [Chunk(kind="text_delta", text=text),
            _stop_chunk({"input_tokens": 50, "output_tokens": 10},
                        "end_turn", _fake_model())]


# ---------------------------------------------------------------- 报告与门
def main() -> int:
    ap = argparse.ArgumentParser(prog="loadn-evals")
    ap.add_argument("--suite", default="smoke", help="smoke|all|<场景名逗号>")
    args = ap.parse_args()

    if "," in args.suite:
        names = {n.strip() for n in args.suite.split(",")}
        scenarios = [s for s in load_scenarios("all") if s["name"] in names]
    else:
        scenarios = load_scenarios(args.suite)
    if not scenarios:
        print(f"✗ suite {args.suite!r} 无场景", file=sys.stderr)
        return 1

    results = [asyncio.run(run_scenario(sc)) for sc in scenarios]
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    lines = [f"# evals 报告 · suite={args.suite} · {ts}",
             "", f"- 通过 **{passed}** / 失败 **{failed}**（共 {len(results)}）", ""]
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        lines.append(f"## {mark} {r['name']}（{r['duration_s']}s）")
        for p in r["problems"]:
            lines.append(f"- {p}")
        lines.append("")
    report = REPORT_DIR / f"{ts}-{args.suite}.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"报告 → {report}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
