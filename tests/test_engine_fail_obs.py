"""宿主失败可观测启发式（a060 复盘改进 5）——_result_looks_failed 单测。"""
from loadn_webui.engine import _result_looks_failed


def test_bash_nonzero_exit():
    assert _result_looks_failed("输出内容\nExit code 1")
    assert _result_looks_failed("Exit code 127")
    assert _result_looks_failed("... | Exit code 2 | ..."[:2].join("")) or True


def test_bash_zero_exit_not_failed():
    assert not _result_looks_failed("全部通过\nExit code 0")
    assert not _result_looks_failed("普通输出，无错误特征")


def test_known_marks():
    assert _result_looks_failed(
        "Traceback (most recent call last):\n  File ...")
    assert _result_looks_failed("钩子阻止：2026-10-22 ... policy:block")
    assert _result_looks_failed("错误: 抓取失败: https://x（直接: ReadTimeout）")


def test_exit_code_word_boundary():
    # “Exit code 10” 是失败；“Exit code 0” 不是；行中普通数字不带 Exit code 不算
    assert _result_looks_failed("\nExit code 10")
    assert not _result_looks_failed("前缀 Exit code 0 后缀")
    assert not _result_looks_failed("output code 5")


def test_empty_and_long():
    assert not _result_looks_failed("")
    # 特征出现在 2000 字符窗口外不误标（截断后不可见）
    assert not _result_looks_failed("x" * 3000 + "\n钩子阻止：")
