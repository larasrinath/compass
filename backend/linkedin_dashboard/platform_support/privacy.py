"""Private storage for saved profiles, on every supported platform.

POSIX expresses privacy as owner-only mode bits on the directory and the file.
Windows has no mode bits: NTFS files inherit their directory's access-control
list, so the boundary Compass proves there is the *directory*, and files created
inside it inherit it.  Both platforms refuse links that could redirect the
database somewhere else between the check and the open.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterable
from errno import ELOOP
from pathlib import Path

from linkedin_dashboard.platform_support import IS_WINDOWS

if IS_WINDOWS:  # pragma: no cover - import cover depends on the platform
    from linkedin_dashboard.platform_support import windows_acl

_BINARY = getattr(os, "O_BINARY", 0)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

#: Windows accounts that already administer the machine.  They can take
#: ownership of anything, so their presence on an access list is not a
#: confidentiality change; any *other* trustee is.
WINDOWS_SYSTEM_TRUSTEES = frozenset(
    {
        "S-1-5-18",  # LocalSystem
        "S-1-5-32-544",  # BUILTIN\Administrators
    }
)


def windows_unauthorized_trustees(
    entries: Iterable[tuple[int, str]] | None, user_sid: str
) -> tuple[str, ...]:
    """Return trustees that may read a directory besides its owner.

    ``None`` means the object has no access list at all, which Windows treats as
    "everyone, full control".
    """
    if entries is None:
        return ("S-1-1-0",)
    allowed = WINDOWS_SYSTEM_TRUSTEES | {user_sid}
    return tuple(
        sorted(
            {
                sid
                for ace_type, sid in entries
                if ace_type in windows_acl_allow_types() and sid not in allowed
            }
        )
    )


def windows_acl_allow_types() -> frozenset[int]:
    """ACE types that grant access rather than deny it."""
    if IS_WINDOWS:  # pragma: no cover - platform specific
        return windows_acl.ALLOW_ACE_TYPES
    return frozenset({0x00, 0x05, 0x09, 0x0B})


def windows_owner_is_acceptable(owner_sid: str, user_sid: str) -> bool:
    """Whether *owner_sid* is the current user or a machine administrator."""
    return owner_sid == user_sid or owner_sid in WINDOWS_SYSTEM_TRUSTEES


def create_private_directories(directory: Path) -> None:
    """Create missing parents privately without changing existing parents."""
    missing: list[Path] = []
    cursor = directory
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent

    if not cursor.is_dir():
        raise NotADirectoryError(cursor)

    for path in reversed(missing):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            if not path.is_dir():
                raise
        else:
            if IS_WINDOWS:  # pragma: no cover - platform specific
                windows_acl.restrict_to_owner(path)
            else:
                os.chmod(path, 0o700)


def require_private_directory(directory: Path) -> None:
    """Require a private, current-user-owned directory before database writes."""
    directory_stat = directory.lstat()
    if stat.S_ISLNK(directory_stat.st_mode):
        raise PermissionError(
            f"database parent must not be a symbolic link: {directory}"
        )
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise NotADirectoryError(directory)

    if IS_WINDOWS:  # pragma: no cover - platform specific
        _require_private_windows_directory(directory, directory_stat)
        return

    current_uid = getattr(os, "geteuid", os.getuid)()
    if directory_stat.st_uid != current_uid:
        raise PermissionError(
            f"database parent must be owned by the current user: {directory}"
        )

    mode = stat.S_IMODE(directory_stat.st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(
            "database parent must grant no group or world permissions "
            f"(expected mode 0700 or stricter): {directory}"
        )


def _require_private_windows_directory(
    directory: Path, directory_stat: os.stat_result
) -> None:  # pragma: no cover - platform specific
    if getattr(directory_stat, "st_reparse_tag", 0):
        raise PermissionError(
            f"database parent must not be a junction or reparse point: {directory}"
        )
    user_sid = windows_acl.current_user_sid()
    owner_sid, entries = windows_acl.describe_access(directory)
    if not windows_owner_is_acceptable(owner_sid, user_sid):
        raise PermissionError(
            f"database parent must be owned by the current user: {directory}"
        )
    unauthorized = windows_unauthorized_trustees(entries, user_sid)
    if unauthorized:
        raise PermissionError(
            "database parent must grant no access to other accounts "
            f"(unexpected: {', '.join(unauthorized)}): {directory}"
        )


def open_owner_only_file(path: Path, *, create: bool) -> int:
    """Open a regular file without following its final path component."""
    flags = (
        os.O_RDWR
        | _BINARY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if create:
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = open_existing_file(path, flags)
    else:
        descriptor = open_existing_file(path, flags)

    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"database path is not a regular file: {path}")
        require_same_file(path, descriptor)
        if IS_WINDOWS:
            restore_owner_only_mode(path, descriptor)
        else:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            require_same_file(path, descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def open_existing_file(path: Path, flags: int) -> int:
    try:
        return os.open(path, flags)
    except OSError as error:
        if error.errno == ELOOP:
            raise ValueError(
                f"database path must not be a symbolic link: {path}"
            ) from error
        raise


def restore_owner_only_mode(path: Path, descriptor: int) -> None:
    """Repair privacy through the held, proven inode and verify the result."""
    require_same_file(path, descriptor)
    if IS_WINDOWS:  # pragma: no cover - platform specific
        attributes = getattr(os.fstat(descriptor), "st_file_attributes", 0)
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise PermissionError(f"database file must not be a reparse point: {path}")
        # Existing files can carry explicit permissions wider than their parent.
        user_sid = windows_acl.current_user_sid()
        owner, entries = windows_acl.describe_access(path)
        if not windows_owner_is_acceptable(owner, user_sid):
            raise PermissionError(
                f"database file must be owned by the current user: {path}"
            )
        if windows_unauthorized_trustees(entries, user_sid):
            windows_acl.restrict_to_owner(path)
            _, entries = windows_acl.describe_access(path)
            if windows_unauthorized_trustees(entries, user_sid):
                raise PermissionError(
                    f"database file permissions could not be repaired: {path}"
                )
        require_same_file(path, descriptor)
        return
    os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
    file_stat = os.fstat(descriptor)
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise PermissionError(f"database mode could not be restored to 0600: {path}")
    require_same_file(path, descriptor)


def require_same_file(path: Path, descriptor: int) -> None:
    path_stat = path.lstat()
    file_stat = os.fstat(descriptor)
    require_single_link(path_stat, path)
    require_single_link(file_stat, path)
    if stat.S_ISLNK(path_stat.st_mode) or (
        path_stat.st_dev,
        path_stat.st_ino,
    ) != (file_stat.st_dev, file_stat.st_ino):
        raise ValueError(f"database path changed while opening it: {path}")


def require_single_link(file_stat: os.stat_result, path: Path) -> None:
    if file_stat.st_nlink != 1:
        raise ValueError(f"database file must have exactly one hard link: {path}")


def secure_existing_sidecars(path: Path) -> None:
    """Re-prove the privacy of SQLite's journal files beside the database."""
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar_stat = sidecar.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(sidecar_stat.st_mode):
            raise ValueError(f"database sidecar must not be a symbolic link: {sidecar}")
        if not stat.S_ISREG(sidecar_stat.st_mode):
            raise ValueError(f"database sidecar is not a regular file: {sidecar}")
        require_single_link(sidecar_stat, sidecar)
        try:
            descriptor = open_owner_only_file(sidecar, create=False)
        except FileNotFoundError:
            continue
        else:
            os.close(descriptor)


def remove_tree(path: Path) -> None:
    """Delete a directory tree, including Windows read-only files.

    Git marks everything under ``.git/objects`` read-only, and Windows refuses to
    delete a read-only file, so a plain ``rmtree`` cannot remove a checkout there.
    """

    def clear_readonly(function, target, _exception):  # type: ignore[no-untyped-def]
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=clear_readonly)
