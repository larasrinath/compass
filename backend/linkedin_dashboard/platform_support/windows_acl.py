"""Native Windows permissions, using pywin32's checked API bindings."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

# Imported only on Windows; these extension modules are not installed on POSIX.
security = import_module("win32security")
api = import_module("win32api")
ALLOW_ACE_TYPES = frozenset({0x00, 0x05, 0x09, 0x0B})


def current_user_sid() -> str:
    token = security.OpenProcessToken(api.GetCurrentProcess(), 0x0008)
    try:
        sid, _attributes = security.GetTokenInformation(token, security.TokenUser)
        return security.ConvertSidToStringSid(sid)
    finally:
        token.Close()


def describe_access(path: Path) -> tuple[str, tuple[tuple[int, str], ...] | None]:
    descriptor = security.GetNamedSecurityInfo(
        str(path),
        security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION,
    )
    owner = security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:
        return owner, None
    entries = []
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        ace_type = ace[0][0]
        if ace_type in ALLOW_ACE_TYPES:
            # Basic and object ACEs both expose the trustee as their last item.
            entries.append((ace_type, security.ConvertSidToStringSid(ace[-1])))
    return owner, tuple(entries)


def restrict_to_owner(path: Path) -> None:
    sid = current_user_sid()
    descriptor = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"D:P(A;OICI;FA;;;{sid})",
        security.SDDL_REVISION_1,
    )
    security.SetNamedSecurityInfo(
        str(path),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION
        | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        descriptor.GetSecurityDescriptorDacl(),
        None,
    )
