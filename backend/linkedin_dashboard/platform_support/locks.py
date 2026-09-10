"""Portable exclusive locks. Windows state is stored beside its locked file."""

from __future__ import annotations

import errno
import os
from pathlib import Path

from linkedin_dashboard.platform_support import IS_WINDOWS

if IS_WINDOWS:  # pragma: no cover - import cover depends on the platform
    import msvcrt
else:  # pragma: no cover - import cover depends on the platform
    import fcntl

#: Windows locks one byte, even when the lock file is empty. State lives
#: in a separate file because Windows locks prohibit reads of locked bytes.
LOCK_BYTE_OFFSET = 0

#: Nothing may be written at or beyond the lock byte.
MAX_STATE_BYTES = 64 * 1024

_UNAVAILABLE_ERRNOS = frozenset(
    value
    for value in (
        errno.EACCES,
        errno.EAGAIN,
        errno.EWOULDBLOCK,
        getattr(errno, "EDEADLK", None),
        getattr(errno, "EDEADLOCK", None),
    )
    if value is not None
)

_BINARY = getattr(os, "O_BINARY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class LockUnavailable(BlockingIOError):
    """Another process (or handle) already holds the exclusive lock."""


def lock_exclusive(descriptor: int) -> None:
    """Take the exclusive lock on *descriptor* without waiting."""
    if not IS_WINDOWS:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LockUnavailable(error.errno, "lock is held") from error
        return
    position = os.lseek(descriptor, 0, os.SEEK_CUR)
    os.lseek(descriptor, LOCK_BYTE_OFFSET, os.SEEK_SET)
    try:
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    except OSError as error:
        if error.errno in _UNAVAILABLE_ERRNOS:
            raise LockUnavailable(error.errno, "lock is held") from error
        raise
    finally:
        os.lseek(descriptor, position, os.SEEK_SET)


def unlock(descriptor: int) -> None:
    """Release the exclusive lock held on *descriptor*."""
    if not IS_WINDOWS:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    position = os.lseek(descriptor, 0, os.SEEK_CUR)
    os.lseek(descriptor, LOCK_BYTE_OFFSET, os.SEEK_SET)
    try:
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.lseek(descriptor, position, os.SEEK_SET)


class StateLock:
    """An exclusive lock whose holder publishes readable UTF-8 state.

    The state is deliberately separate from the lock: a launch that cannot take
    the lock still has to read what the holder is doing before deciding whether
    to wait, refuse, or ask it to stop.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        flags = os.O_RDWR | os.O_CREAT | _BINARY | _CLOEXEC
        self._descriptor = os.open(path, flags, 0o600)
        self._locked = False
        self._state_path = Path(str(path) + ".state") if IS_WINDOWS else None

    @property
    def descriptor(self) -> int:
        return self._descriptor

    @property
    def locked(self) -> bool:
        return self._locked

    def acquire(self) -> None:
        """Take the lock, raising :class:`LockUnavailable` when it is held."""
        lock_exclusive(self._descriptor)
        self._locked = True

    def release(self) -> None:
        if self._locked:
            unlock(self._descriptor)
            self._locked = False

    def read_state(self) -> str:
        """Read the holder's published state without disturbing the lock."""
        if self._state_path is not None:
            return read_state_file(self._state_path)
        os.lseek(self._descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = MAX_STATE_BYTES
        while remaining > 0:
            chunk = os.read(self._descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks).decode("utf-8", "replace").strip("\x00").strip()

    def write_state(self, state: str) -> None:
        """Publish *state* at offset 0, never touching the locked byte."""
        payload = state.encode("utf-8")
        if len(payload) >= MAX_STATE_BYTES:
            raise ValueError("launcher state is too large to publish")
        if self._state_path is not None:
            temporary = self._state_path.with_name(self._state_path.name + ".tmp")
            temporary.write_bytes(payload)
            temporary.replace(self._state_path)
            return
        os.lseek(self._descriptor, 0, os.SEEK_SET)
        os.write(self._descriptor, payload)
        # POSIX flock is advisory and allows the owner to resize its state.
        os.ftruncate(self._descriptor, len(payload))
        os.fsync(self._descriptor)

    def close(self) -> None:
        if self._descriptor >= 0:
            try:
                self.release()
            finally:
                os.close(self._descriptor)
                self._descriptor = -1

    def __enter__(self) -> StateLock:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()


def read_state_file(path: Path) -> str:
    """Read a state file that another process may be holding a lock on."""
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_STATE_BYTES).decode("utf-8", "replace").strip()
    except FileNotFoundError:
        return ""
