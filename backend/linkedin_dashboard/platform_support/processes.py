"""Child processes that never outlive the Compass launch that started them.

POSIX puts the child in its own session and signals the whole group.  Windows has
no process groups that survive that pattern, so the child is placed in a Job
Object that is configured to end every process inside it when Compass lets go of
it — including a browser the connector started, and including the case where an
intermediate ``uv`` process exits before its own descendants.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import signal
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import psutil

from linkedin_dashboard.platform_support import IS_WINDOWS

GATE = Path(__file__).with_name("spawn_gate.py")
DEFAULT_GRACE_SECONDS = 8.0

if IS_WINDOWS:  # pragma: no cover - native Windows only
    from importlib import import_module

    win32api = import_module("win32api")
    win32job = import_module("win32job")


class ContainmentError(RuntimeError):
    """Compass could not guarantee ownership of a child process tree."""


class WindowsJob:  # pragma: no cover - native Windows only
    """A non-inherited handle owns every process in this Job Object."""

    def __init__(self) -> None:
        self._handle = win32job.CreateJobObject(None, "")
        try:
            win32api.SetHandleInformation(self._handle, 1, 0)
            limits = win32job.QueryInformationJobObject(
                self._handle,
                win32job.JobObjectExtendedLimitInformation,
            )
            limits["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                self._handle,
                win32job.JobObjectExtendedLimitInformation,
                limits,
            )
        except BaseException:
            self.close()
            raise

    def assign(self, pid: int) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_INFORMATION
        process = win32api.OpenProcess(0x0100 | 0x0001 | 0x0400, False, pid)
        try:
            win32job.AssignProcessToJobObject(self._handle, process)
            if not win32job.IsProcessInJob(process, self._handle):
                raise ContainmentError("Windows did not retain the managed process")
        finally:
            process.Close()

    def active_processes(self) -> int:
        if self._handle is None:
            return 0
        info = win32job.QueryInformationJobObject(
            self._handle,
            win32job.JobObjectBasicAccountingInformation,
        )
        return info["ActiveProcesses"]

    def terminate(self) -> None:
        if self._handle is not None:
            win32job.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.Close()


class ContainedProcess:
    """A spawned process together with the ownership of its descendants."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        job: WindowsJob | None = None,
        release: Path | None = None,
    ) -> None:
        self.process = process
        self._job = job
        self._release = release

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def wait(self) -> int:
        return await self.process.wait()

    async def stop(self, *, grace: float = DEFAULT_GRACE_SECONDS) -> None:
        """End the whole tree, waiting a bounded time for a clean exit."""
        try:
            if self._job is not None:
                if self.process.returncode is None:
                    with contextlib.suppress(ProcessLookupError, OSError):
                        self.process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", 1))
                # uv may exit before its Python/browser descendants. Wait for
                # the entire job, rather than closing it as soon as uv exits.
                deadline = asyncio.get_running_loop().time() + grace
                while self._job.active_processes():
                    if asyncio.get_running_loop().time() >= deadline:
                        self._job.terminate()
                        break
                    await asyncio.sleep(0.05)
                await asyncio.wait_for(self.process.wait(), timeout=max(grace, 2.0))
            elif self.process.returncode is None:
                await self._stop_posix_group(grace)
        finally:
            self.release()

    async def _stop_posix_group(self, grace: float) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self.process.wait(), timeout=grace)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            await self.process.wait()

    def release(self) -> None:
        """Drop the containment handle; on Windows this also ends stragglers."""
        if self._job is not None:  # pragma: no cover - platform specific
            self._job.close()
            self._job = None
        if self._release is not None:
            with contextlib.suppress(OSError):
                self._release.unlink()
            self._release = None


def gate_command(release: Path, command: list[str]) -> list[str]:
    """Wrap *command* in the stdlib-only containment gate."""
    return [sys.executable, "-I", "-u", str(GATE), str(release), "--", *command]


async def spawn_contained(
    command: list[str],
    *,
    cwd: Path,
    output: int,
    containment_dir: Path,
    env: Mapping[str, str] | None = None,
) -> ContainedProcess:
    """Start *command* so that Compass owns every process it goes on to start."""
    if not IS_WINDOWS:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
        return ContainedProcess(process)

    # pragma: no cover - platform specific
    release = containment_dir / f"containment-{secrets.token_hex(16)}"
    job = WindowsJob()
    try:
        process = await asyncio.create_subprocess_exec(
            *gate_command(release, command),
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except BaseException:
        job.close()
        raise
    contained = ContainedProcess(process, job=job, release=release)
    try:
        job.assign(process.pid)
        release.write_bytes(b"go")
    except BaseException:
        # The gate has not been released, so it cannot have started the target.
        # Assignment may have failed: do not rely on the job to own this gate.
        try:
            if process.returncode is None:
                process.kill()
            await process.wait()
        finally:
            contained.release()
        raise
    return contained


def wait_for_exit(process: psutil.Process, timeout: float) -> bool:
    """Wait for one already-identified process to end, without killing it."""
    try:
        process.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        return not process.is_running()
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False
    return True


def running_process(pid: int, create_time: float) -> psutil.Process | None:
    """Return the process with this identity, or None if it is gone.

    The creation time is what makes this safe: an operating system reuses process
    identifiers, and a recycled identifier must never be mistaken for the process
    Compass recorded.
    """
    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - create_time) > 0.001:
            return None
        if not process.is_running():
            return None
    except (psutil.Error, OSError, ValueError):
        return None
    return process


__all__ = [
    "GATE",
    "ContainedProcess",
    "ContainmentError",
    "gate_command",
    "running_process",
    "spawn_contained",
    "wait_for_exit",
]
