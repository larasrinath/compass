"""One Compass instance per repository, and a safe handover to the next launch.

Two launches from the same folder must never both serve, and the second one must
never stop something it has not identified.  The running instance publishes who
it is next to its lock; a later launch reads that, proves the process is still
that process, and asks it to stop through a request file it polls.  Only after a
cooperative stop fails does POSIX fall back to ``SIGTERM``.  Windows has no
graceful signal for another console's process, so there it reports the problem
instead of killing the process behind the user's back.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psutil

from linkedin_dashboard.platform_support import BOOTSTRAP_COMMAND, IS_WINDOWS
from linkedin_dashboard.platform_support.locks import LockUnavailable, StateLock
from linkedin_dashboard.platform_support.processes import running_process, wait_for_exit

RECORD_VERSION = 1
PREPARING = "preparing"
RUNNING = "running"
LOCK_NAME = "launcher.lock"
TAKEOVER_LOCK_NAME = "launcher-restart.lock"
SHUTDOWN_REQUEST_NAME = "launcher-shutdown.json"
LAUNCHER_MODULE = "linkedin_dashboard.launcher"

#: How long a launch waits for the previous instance to stop cooperatively.
COOPERATIVE_SECONDS = 25.0 if IS_WINDOWS else 10.0
#: How long POSIX then waits after the graceful signal it also has available.
SIGNAL_SECONDS = 15.0
REQUEST_POLL_SECONDS = 0.25

UNVERIFIED = "Cannot verify the previous Compass process. No process was stopped."


@dataclass(frozen=True)
class OwnerRecord:
    """What a running launch publishes about itself beside its lock."""

    state: str
    pid: int
    create_time: float
    root: str

    def serialize(self) -> str:
        return json.dumps(
            {
                "version": RECORD_VERSION,
                "state": self.state,
                "pid": self.pid,
                "create_time": self.create_time,
                "root": self.root,
            }
        )

    @classmethod
    def parse(cls, text: str) -> OwnerRecord | None:
        """Read a published record, or None when there is nothing to trust."""
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != RECORD_VERSION:
            return None
        try:
            state = str(payload["state"])
            pid = int(payload["pid"])
            create_time = float(payload["create_time"])
            root = str(payload["root"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            state not in {PREPARING, RUNNING}
            or pid <= 0
            or not math.isfinite(create_time)
            or create_time <= 0
        ):
            return None
        return cls(state=state, pid=pid, create_time=create_time, root=root)


def own_record(state: str, root: Path) -> OwnerRecord:
    """Describe this process for the launch that may take over from it."""
    return OwnerRecord(
        state=state,
        pid=os.getpid(),
        create_time=psutil.Process().create_time(),
        root=str(root.resolve()),
    )


def verify_owner(record: OwnerRecord, root: Path) -> psutil.Process | None:
    """Return the recorded process only if it is still that exact process."""
    try:
        if Path(record.root) != root.resolve():
            return None
    except OSError:
        return None
    process = running_process(record.pid, record.create_time)
    if process is None:
        return None
    try:
        arguments = list(process.cmdline() or [])
        if Path(process.cwd()).resolve() != root.resolve():
            return None
    except (psutil.Error, OSError):
        return None
    if not any(
        arguments[index : index + 2] == ["-m", LAUNCHER_MODULE]
        for index in range(len(arguments) - 1)
    ):
        return None
    return process


def request_shutdown(cache: Path, record: OwnerRecord) -> Path:
    """Ask exactly the recorded instance to stop serving and exit."""
    request = cache / SHUTDOWN_REQUEST_NAME
    request.write_text(
        json.dumps(
            {
                "version": RECORD_VERSION,
                "pid": record.pid,
                "create_time": record.create_time,
                "requested_by": os.getpid(),
                "requested_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    return request


def clear_shutdown_request(cache: Path) -> None:
    with contextlib.suppress(OSError):
        (cache / SHUTDOWN_REQUEST_NAME).unlink()


def take_shutdown_request(cache: Path, record: OwnerRecord) -> bool:
    """Whether a launch has asked *this* instance to stop.

    A request addressed to an earlier instance is removed rather than obeyed: a
    stale file must not be able to close the app a user just opened.
    """
    request = cache / SHUTDOWN_REQUEST_NAME
    try:
        payload = json.loads(request.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        clear_shutdown_request(cache)
        return False
    try:
        matches = (
            payload.get("version") == RECORD_VERSION
            and payload.get("pid") == record.pid
            and (
                abs(float(payload.get("create_time") or 0.0) - record.create_time)
                <= 0.001
            )
        )
    except (TypeError, ValueError, OverflowError):
        matches = False
    clear_shutdown_request(cache)
    return matches


def stop_previous_instance(
    cache: Path, record: OwnerRecord, owner: psutil.Process
) -> None:
    """Stop a verified previous instance without harming anything else."""
    request_shutdown(cache, record)
    try:
        if wait_for_exit(owner, COOPERATIVE_SECONDS):
            return
        if IS_WINDOWS:
            # Windows has no delivered SIGTERM: the alternative to waiting is
            # TerminateProcess, which would cut the connector's browser and any
            # in-flight write off mid-operation while claiming to be graceful.
            raise RuntimeError(
                "The previous Compass instance did not stop when asked. "
                "Select its terminal window and press Ctrl+C, then run "
                f"{BOOTSTRAP_COMMAND} again. No process was stopped by force."
            )
        try:
            owner.terminate()
        except psutil.NoSuchProcess:
            return
        except psutil.Error as error:
            raise RuntimeError(
                "Could not stop the previous Compass process."
            ) from error
        if not wait_for_exit(owner, SIGNAL_SECONDS):
            raise RuntimeError(
                "The previous Compass instance is still shutting down. "
                "Try again shortly."
            )
    finally:
        clear_shutdown_request(cache)


@contextlib.contextmanager
def launcher_lock(
    cache: Path, root: Path, *, setup_only: bool = False
) -> Iterator[StateLock]:
    """Own this repository's single Compass instance for the duration."""
    lock = _own_instance(cache, root, setup_only=setup_only)
    try:
        yield lock
    finally:
        lock.close()


def _own_instance(cache: Path, root: Path, *, setup_only: bool) -> StateLock:
    # Serialize takeover so simultaneous launches cannot stop the same instance.
    with StateLock(cache / TAKEOVER_LOCK_NAME) as takeover:
        try:
            takeover.acquire()
        except LockUnavailable:
            raise RuntimeError(
                "Another Compass launch is restarting. Wait for it to finish."
            ) from None
        lock = StateLock(cache / LOCK_NAME)
        try:
            try:
                lock.acquire()
            except LockUnavailable:
                _take_over(cache, root, lock, setup_only=setup_only)
            lock.write_state(own_record(PREPARING, root).serialize())
        except BaseException:
            lock.close()
            raise
        return lock


def _take_over(cache: Path, root: Path, lock: StateLock, *, setup_only: bool) -> None:
    if setup_only:
        raise RuntimeError("Compass is running. Stop it before setup-only maintenance.")
    state = lock.read_state()
    record = OwnerRecord.parse(state)
    if record is None:
        # Existing macOS/Linux releases predate the cooperative restart record.
        # Keep their proven module/cwd/open-lock identity check for upgrades.
        if IS_WINDOWS or state not in {"", PREPARING, RUNNING}:
            raise RuntimeError(UNVERIFIED)
        if state == PREPARING:
            raise RuntimeError(
                "Compass is still preparing its installation. Wait for it to finish."
            )
        owners = []
        for process in psutil.process_iter():
            try:
                candidate = OwnerRecord(
                    RUNNING, process.pid, process.create_time(), str(root.resolve())
                )
                if (
                    process.pid != os.getpid()
                    and verify_owner(candidate, root)
                    and any(
                        Path(item.path).resolve() == lock.path.resolve()
                        for item in process.open_files()
                    )
                ):
                    owners.append(process)
            except (psutil.Error, OSError):
                continue
        if len(owners) != 1:
            raise RuntimeError(UNVERIFIED)
        try:
            owners[0].terminate()
            if not wait_for_exit(owners[0], COOPERATIVE_SECONDS + SIGNAL_SECONDS):
                raise RuntimeError(
                    "The previous Compass instance is still shutting down."
                )
        except psutil.NoSuchProcess:
            pass
        lock.acquire()
        return
    if record.state == PREPARING:
        raise RuntimeError(
            "Compass is still preparing its installation. "
            "Wait for the existing launch to finish."
        )
    owner = verify_owner(record, root)
    if owner is None:
        raise RuntimeError(UNVERIFIED)
    print("Restarting the previous Compass instance…", flush=True)
    stop_previous_instance(cache, record, owner)
    try:
        lock.acquire()
    except LockUnavailable:
        raise RuntimeError(
            "The previous Compass instance released its lock too late. "
            f"Run {BOOTSTRAP_COMMAND} again."
        ) from None
