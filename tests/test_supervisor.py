"""ProcessSupervisor 测试：登记表 / 输出 tee / kill / 双信号判死 / shutdown。

判死与收割用压缩秒数加速（interval=0.05s、io_silence≈0.3s）；进程死亡以
/proc/<pid> 消失为准（不是只看状态字段）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hahaness.supervisor.process import ProcessSupervisor, ProcInfo


@pytest.fixture
async def sup():
    s = ProcessSupervisor()
    yield s
    await s.shutdown()


async def _wait_status(sup: ProcessSupervisor, task_id: str, want: str,
                       timeout_s: float = 3.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        info = sup.poll(task_id)
        if info["status"] == want:
            return info
        await asyncio.sleep(0.05)
    return sup.poll(task_id)


async def test_spawn_bg_registers_and_tees_output(sup: ProcessSupervisor,
                                                  tmp_path: Path):
    info = await sup.spawn_bg(
        ["bash", "-c", "echo tee-one; sleep 0.15; echo tee-two"],
        cwd=tmp_path)
    assert isinstance(info, ProcInfo)
    assert info.status == "running"
    assert info.pgid == info.pid                      # start_new_session 独立进程组
    out_file = Path(info.output_path)
    assert out_file == tmp_path / "logs" / f"task_{info.id}.out"
    final = await _wait_status(sup, info.id, "done")
    assert final["status"] == "done"
    assert "tee-one" in final["output_tail"]
    assert "tee-two" in final["output_tail"]
    assert out_file.exists() and "tee-two" in out_file.read_text()


async def test_failed_task_marks_failed(sup: ProcessSupervisor,
                                        tmp_path: Path):
    info = await sup.spawn_bg(["bash", "-c", "exit 7"], cwd=tmp_path)
    final = await _wait_status(sup, info.id, "failed")
    assert final["status"] == "failed"


async def test_list_and_poll_unknown(sup: ProcessSupervisor, tmp_path: Path):
    await sup.spawn_bg(["bash", "-c", "sleep 0.1"], cwd=tmp_path)
    entries = sup.list()
    assert len(entries) == 1
    assert entries[0]["cmd"][0] == "bash"
    with pytest.raises(KeyError):
        sup.poll("nope")


async def test_kill_single_task(sup: ProcessSupervisor, tmp_path: Path):
    info = await sup.spawn_bg(["bash", "-c", "sleep 30"], cwd=tmp_path)
    await asyncio.sleep(0.15)
    assert sup.poll(info.id)["status"] == "running"
    result = await sup.kill(info.id)
    assert result["status"] == "killed"
    assert not Path(f"/proc/{info.pid}").exists()     # 真死，不是只改状态


async def test_watchdog_stdout_silence_marks_stalled(sup: ProcessSupervisor,
                                                     tmp_path: Path):
    info = await sup.spawn_bg(["bash", "-c", "sleep 8"], cwd=tmp_path)
    sup.start_watchdog(io_silence_s=0.3, artifact_paths=(), interval=0.05)
    await asyncio.sleep(0.6)
    assert info.id in sup.stalled_ids
    assert sup.poll(info.id)["status"] == "running"   # 只标记不杀


async def test_watchdog_fresh_artifact_not_stalled(sup: ProcessSupervisor,
                                                   tmp_path: Path):
    """双信号：产物路径持续新鲜 → stdout 静默也不判死。"""
    await sup.spawn_bg(["bash", "-c", "sleep 2"], cwd=tmp_path)  # 不出声
    marker = tmp_path / "alive.marker"
    marker.write_text("x")                            # t0 写入，新鲜
    sup.start_watchdog(io_silence_s=1.5, artifact_paths=(str(marker),),
                       interval=0.05)
    await asyncio.sleep(0.5)
    assert sup.stalled_ids == []


async def test_watchdog_output_file_counts_as_liveness(
        sup: ProcessSupervisor, tmp_path: Path):
    """持续写输出的任务在静默阈值内不判死。"""
    info = await sup.spawn_bg(
        ["bash", "-c", "for i in 1 2 3 4; do echo tick; sleep 0.1; done"],
        cwd=tmp_path)
    sup.start_watchdog(io_silence_s=0.5, artifact_paths=(), interval=0.05)
    await asyncio.sleep(0.3)                          # 输出仍在持续
    assert info.id not in sup.stalled_ids


async def test_shutdown_kills_all_registered(sup: ProcessSupervisor,
                                             tmp_path: Path):
    a = await sup.spawn_bg(["bash", "-c", "sleep 30"], cwd=tmp_path)
    b = await sup.spawn_bg(["bash", "-c", "sleep 30"], cwd=tmp_path)
    await asyncio.sleep(0.15)
    await sup.shutdown()
    for info in (a, b):
        assert sup.poll(info.id)["status"] == "killed"
        assert not Path(f"/proc/{info.pid}").exists()
    await sup.shutdown()                              # 幂等
    assert sup.list() and len(sup.list()) == 2


async def test_shutdown_stops_watchdog(sup: ProcessSupervisor,
                                        tmp_path: Path):
    await sup.spawn_bg(["bash", "-c", "sleep 0.1"], cwd=tmp_path)
    sup.start_watchdog(io_silence_s=0.1, interval=0.05)
    await asyncio.sleep(0.05)
    await sup.shutdown()
    await asyncio.sleep(0.3)
    frozen = list(sup.stalled_ids)                    # 协程已取消，标记冻结
    await asyncio.sleep(0.2)
    assert sup.stalled_ids == frozen
