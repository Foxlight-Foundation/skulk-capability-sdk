"""Actual shared process fences, independent of journal initialization/recovery."""

import asyncio
import os
import sys
from pathlib import Path

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest

from skulk_capability_sdk.runtime import finish_owned
from skulk_capability_sdk.storage import HostLock


async def test_native_fence_uses_the_actual_existing_python_host_lock(
    tmp_path: Path,
) -> None:
    """A busy early fence leaves binding/caller state untouched and health live."""
    directory = tmp_path / "custody"
    original = HostLock(directory)
    marker = tmp_path / "native-jobs.json"
    marker.write_bytes(b"protected original binding")
    ticks = 0
    stopped = asyncio.Event()

    async def health() -> None:
        nonlocal ticks
        while not stopped.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    pulse = asyncio.create_task(health())
    try:
        with pytest.raises(ValueError, match="busy") as refused:
            await native.open_process_fence(str(directory))
        assert str(tmp_path) not in str(refused.value)
        assert ticks > 4
        assert marker.read_bytes() == b"protected original binding"
        assert not (tmp_path / "operations.sqlite").exists()
        assert not (tmp_path / "origins.sqlite").exists()
    finally:
        original.close()
        stopped.set()
        await pulse
    fence = await native.open_process_fence(str(directory))
    inode = (directory / "supervisor.lock").stat().st_ino
    try:
        with pytest.raises(OSError):
            HostLock(directory)
    finally:
        fence.close()
        fence.close()
    old_reader = HostLock(directory)
    old_reader.close()
    assert (directory / "supervisor.lock").stat().st_ino == inode


async def test_repeated_cancellation_retains_late_acquisition_until_cleanup(
    tmp_path: Path,
) -> None:
    """The existing SDK owner drains a late native acquisition before releasing."""
    directory = tmp_path / "custody"
    original = HostLock(directory)
    acquired: native.NativeProcessFence | None = None

    async def acquire() -> None:
        nonlocal acquired
        acquired = await native.open_process_fence(str(directory))

    async def owner() -> None:
        try:
            await finish_owned(asyncio.create_task(acquire()))
        finally:
            if acquired is not None:
                acquired.close()

    task = asyncio.create_task(owner())
    try:
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        original.close()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert acquired is not None
        later = HostLock(directory)
        later.close()
    finally:
        original.close()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_real_process_death_releases_fence_without_deleting_state(
    tmp_path: Path,
) -> None:
    """A genuinely killed native owner releases OS custody, preserving metadata."""
    directory = tmp_path / "custody"
    marker = tmp_path / "native-jobs.json"
    marker.write_bytes(b"accepted job identity survives")
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; import _skulk_capability_kernel as k; "
        "held=k.NativeProcessFence(sys.argv[1]); print('held',flush=True); "
        "sys.stdin.buffer.read()",
        str(directory),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert child.stdout is not None
        assert await asyncio.wait_for(child.stdout.readline(), 5) == b"held\n"
        with pytest.raises(OSError):
            HostLock(directory)
        child.kill()
        await asyncio.wait_for(child.wait(), 5)
        assert child.returncode is not None and child.returncode < 0
        later = HostLock(directory)
        later.close()
        assert marker.read_bytes() == b"accepted job identity survives"
        assert sorted(p.name for p in directory.iterdir()) == ["supervisor.lock"]
    finally:
        if child.returncode is None:
            child.kill()
        await child.communicate()


@pytest.mark.parametrize("fault", ["symlink", "fifo", "mode", "oversized"])
async def test_unsafe_lock_files_refuse_without_journal_side_effects(
    tmp_path: Path, fault: str
) -> None:
    """A damaged lock never blocks in open or creates another ownership domain."""
    directory = tmp_path / "custody"
    directory.mkdir(mode=0o700)
    lock = directory / "supervisor.lock"
    target = tmp_path / "untouched"
    target.write_bytes(b"untouched")
    if fault == "symlink":
        lock.symlink_to(target)
    elif fault == "fifo":
        os.mkfifo(lock, 0o600)
    else:
        lock.touch(mode=0o600)
        if fault == "mode":
            lock.chmod(0o640)
        else:
            with lock.open("wb") as output:
                output.truncate(129 * 1024 * 1024)
    with pytest.raises(ValueError):
        await asyncio.wait_for(native.open_process_fence(str(directory)), 3)
    assert target.read_bytes() == b"untouched"
    assert not (directory / "operations.sqlite").exists()
