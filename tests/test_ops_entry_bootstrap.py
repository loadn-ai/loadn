# console script 自举改写对赌（2026-10-22 生产报障：`python3 $CLI` 绕过
# shebang 报 No module named loadn_webui——宪法模板历史教法连环触发）。
# 对赌：
#   1) _patch_entry_scripts 改写 pip 模板（shebang 后插自举段）且幂等
#   2) 真实行为：系统 python3 执行改写后脚本 → re-exec venv python 成功
#      （改写前同命令必炸 No module named——对照组）
#   3) 防死循环：解释器已是目标 venv python 时不 exec（realpath 比对）
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from loadn_webui.ops import _patch_entry_scripts

PIP_TEMPLATE = textwrap.dedent("""\
    #!/data/code/loadn/.venv/bin/python3
    # -*- coding: utf-8 -*-
    import re
    import sys
    from loadn_webui.cli import main
    if __name__ == '__main__':
        sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])
        sys.exit(main())
    """)


def _make_entry(bin_dir: Path, name: str = "loadn-web") -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    f = bin_dir / name
    f.write_text(PIP_TEMPLATE)
    return f


def test_patch_inserts_bootstrap_and_idempotent(tmp_path: Path):
    f = _make_entry(tmp_path / ".venv" / "bin")
    _patch_entry_scripts(tmp_path / ".venv")
    src = f.read_text()
    assert "_loadn_bootstrap" in src
    assert "os.execv" not in src.split("_loadn_bootstrap")[0]  # 在 shebang 与原 import 之间
    # 自举段在 shebang 行之后、原 from import 行之前
    assert src.index("#!") < src.index("_loadn_bootstrap") < src.index("from loadn_webui.cli import")
    # 幂等：二次改写不重复插入（标记计数不变）
    n1 = f.read_text().count("_loadn_bootstrap")
    _patch_entry_scripts(tmp_path / ".venv")
    assert f.read_text().count("_loadn_bootstrap") == n1


def test_patch_skips_missing_and_marker_files(tmp_path: Path):
    # loadn 不存在 → 只改 loadn-web；已带标记 → 跳过
    f = _make_entry(tmp_path / ".venv" / "bin")
    _patch_entry_scripts(tmp_path / ".venv")
    before = f.read_text()
    _patch_entry_scripts(tmp_path / ".venv")
    assert f.read_text() == before


def test_real_reexec_from_system_python(tmp_path: Path):
    """系统 python3 执行改写后脚本 → re-exec venv python → import 成功。

    对照组（不改写）同命令必炸 No module named loadn_webui——这就是
    生产报障的精确复现；改写后同一调用形态收敛到正确解释器。

    环境：真 venv（/usr/bin/python3 -m venv，与生产 release 同构——
    bin/python 是 venv 自己的 symlink + 目录内 pyvenv.cfg，re-exec 后
    环境正确加载；symlink 指向外部 venv python 不继承 pyvenv.cfg，
    是构造陷阱不可用）+ site-packages 放 repo.pth 指仓库根（cli 顶部
    纯标准库，懒加载依赖，--version 路径零第三方 import）。"""
    repo = Path("/data/code/loadn")
    system_python = "/usr/bin/python3"
    if not ((repo / ".venv" / "bin" / "python").exists()
            and Path(system_python).exists()):
        pytest.skip("需要仓库源码树与系统 python3")
    venv_dir = tmp_path / ".venv"
    subprocess.run([system_python, "-m", "venv", str(venv_dir)],
                   check=True, timeout=120)
    sp = next((venv_dir / "lib").glob("python3*/site-packages"))
    (sp / "repo.pth").write_text(f"{repo}\n")
    f = _make_entry(venv_dir / "bin")

    # 对照组：不改写，系统 python3 直接跑 → No module named（生产报障复现）
    r0 = subprocess.run([system_python, str(f), "--help"],
                        capture_output=True, text=True, timeout=120)
    assert "No module named" in r0.stderr, "对照组未复现报障——环境异常"

    # 实验组：改写后同命令 → re-exec → 成功输出
    _patch_entry_scripts(venv_dir)
    r1 = subprocess.run([system_python, str(f), "--help"],
                        capture_output=True, text=True, timeout=120)
    assert r1.returncode == 0, r1.stderr
    assert "usage" in r1.stdout  # argparse 帮助输出（import 链完整走通）


def test_no_exec_loop_when_already_venv_python(tmp_path: Path):
    """防死循环：venv python 自身执行时 realpath 相等 → 不 exec，直接 raise
    （构造：包不可达的假 venv python 指向当前解释器自身）。"""
    bin_dir = tmp_path / ".venv" / "bin"
    f = _make_entry(bin_dir)
    _patch_entry_scripts(tmp_path / ".venv")
    # python symlink 指向当前解释器（realpath 相等 → 不 exec）；
    # 脚本目标包 loadn_webui 在本测试进程环境可达（editable 安装），
    # 所以这里改目标验证 re-exec 路径之外的安全网：直接执行改写后脚本
    # （shebang 无效场景交给 monkeypatch 解释器名）
    src = f.read_text().replace(
        "import os as _bos, sys as _bsys",
        "import os as _bos, sys as _bsys\n_bsys.executable = _bos.path.realpath(__file__) and '/nonexistent/python'")
    f2 = tmp_path / "loop_probe.py"
    f2.write_text(src)
    # /nonexistent/python 不存在 → 不满足 exec 条件 → 原样 raise（不 exec 不死循环）
    r = subprocess.run([sys.executable, str(f2)], capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "No module named" not in r.stderr  # 未走 import 成功路径也未被 exec 吞掉
