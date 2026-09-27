"""skill 供应链静态扫描（W4 第②段）：八类，红=拒装 / 黄=装但留痕待确认。

R1 出网端点｜R2 安装器（slopsquatting 面）｜R3 动态执行｜R4 隐写指令
R5 危险指令文本（含对 .claude/.mcp.json/CLAUDE.md 的写路径）｜R6 密钥正则
R7 配置篡改｜R8 体积规避（无「超限跳过」豁免——大文件分片照样扫）。
扫描对象=SKILL.md 与全部文本类文件（指令不只藏在代码里——R4 连文档扫）。
"""
from __future__ import annotations

import re
from pathlib import Path

from .audit import audit

# 红线（拒装）
_RE_CURL_PIPE_SH = re.compile(r"curl[^|\n]{0,200}\|\s*(sudo )?(ba)?sh|wget[^|\n]{0,200}\|\s*(ba)?sh")
# R7 按行共现：写动作动词 + 平台控制文件路径同现即红旗（形态万变，行内
# 共现是稳态特征；纯提及（文档说明）不带写动词不误伤）
_WRITE_VERBS = re.compile(
    r"\b(open|write_text|write_bytes|rename|replace|move|copy|unlink|rm )\b|"
    r"Path\(|\bos\.remove|shutil\.|>>\s*")
_RE_CTRL_FILES = re.compile(
    r"\.claude/settings\.json|\.mcp\.json|\bCLAUDE\.md\b|\.loadn/")
_RE_EXFIL = re.compile(r"(发送到|上传到|传送至|post to|upload to)[^\n]{0,80}"
                       r"(https?://|attacker|外发)")
_RE_EVAL = re.compile(r"\beval\(|\bexec\(|shell\s*=\s*True")
_RE_KEY = re.compile(r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{20,}"
                     r"|-----BEGIN (RSA |EC )?PRIVATE KEY-----")
# 零宽/双向控制符（隐写指令）
_RE_STEGO = re.compile("[​‌‍‮⁠﻿]")
_TEXT_EXTS = {".md", ".txt", ".py", ".sh", ".js", ".ts", ".json", ".yaml",
              ".yml", ".toml", ".cfg", ".ini", ".html", ".css"}
_CHUNK = 512 * 1024          # R8：大文件分片读，无跳过豁免
_URL_RE = re.compile(r"https?://([^/\s\"')]+)")
_INSTALLER_RE = re.compile(
    r"(pip3? install|npm install|pnpm add|yarn add)[^\n]{0,120}")


def _iter_texts(root: Path):
    for f in root.rglob("*"):
        if f.is_file() and not f.is_symlink():
            if f.suffix.lower() in _TEXT_EXTS or f.stat().st_size < 64 * 1024:
                yield f


def _read_chunked(f: Path) -> str:
    out = []
    with f.open("rb") as fh:
        while True:
            b = fh.read(_CHUNK)
            if not b:
                break
            out.append(b.decode("utf-8", errors="replace"))
    return "".join(out)


def scan_skill(root: Path) -> dict:
    """返回 {level: red|yellow|green, findings: [{rule, file, detail}]}。"""
    findings: list[dict] = []

    def add(rule: str, level: str, file: str, detail: str):
        findings.append({"rule": rule, "level": level,
                         "file": file, "detail": detail[:160]})

    from ..config import CONFIG
    allow = {h.lower() for h in CONFIG.security.egress_allow}

    for f in _iter_texts(root):
        rel = str(f.relative_to(root))
        try:
            text = _read_chunked(f)
        except OSError:
            continue
        if _RE_CURL_PIPE_SH.search(text):
            add("R5", "red", rel, "下载即执行形态（curl|sh）")
        for line in text.splitlines():
            if _WRITE_VERBS.search(line) and _RE_CTRL_FILES.search(line) \
                    and f.suffix != ".md":       # 代码文件里的写+目标共现
                add("R7", "red", rel, "代码写平台控制文件（settings/.mcp.json/CLAUDE.md）")
                break
        if _RE_EXFIL.search(text):
            add("R5", "red", rel, "外发指令文本（发送到/上传到 + 端点）")
        if _RE_EVAL.search(text) and f.suffix == ".py":
            add("R3", "yellow", rel, "动态执行（eval/exec/shell=True）")
        m = _RE_KEY.search(text)
        if m:
            add("R6", "red", rel, f"疑似打包密钥（{m.group(0)[:8]}…）")
        if _RE_STEGO.search(text):
            add("R4", "red", rel, "隐写控制符（零宽/RTL override）")
        for host_m in _URL_RE.finditer(text):
            host = host_m.group(1).lower()
            if not any(host == a or host.endswith("." + a) for a in allow):
                add("R1", "yellow", rel, f"出网端点 {host}")
                break                                   # 每文件记一条代表
        if _INSTALLER_RE.search(text):
            add("R2", "yellow", rel, "安装器调用（注意 slopsquatting）")

    level = ("red" if any(x["level"] == "red" for x in findings)
             else "yellow" if findings else "green")
    out = {"level": level, "findings": findings}
    audit("skill_scan", {"dir": root.name, "level": level,
                         "n": len(findings),
                         "rules": sorted({x["rule"] for x in findings})})
    return out


CAP_TEMPLATE = """# loadn 能力声明（安装时自动生成草案；人工核对后生效）
net: []          # 允许外联的域清单（R1 扫描结果为空=默认禁网）
fs: [workspace]  # 文件系统范围
exec: []         # 允许的执行器（python3/node/…）
irreversible: [] # 不可逆动作（mail/pay/…需 approvals）
mcp: []          # 挂载的 MCP servers
"""


def ensure_capability(dst: Path) -> Path:
    """无声明 → 生成全禁草案（挂载策略面以此为准）。"""
    cap = dst / ".loadn-capabilities.yaml"
    if not cap.exists():
        cap.write_text(CAP_TEMPLATE)
    return cap
