"""Portable primitives Compass needs on Windows, macOS and Linux.

Every platform difference Compass depends on lives here so the launcher and the
database keep one behaviour to reason about: exclusive locks that stay readable,
private directories, and child processes that never outlive their owner.
"""

from __future__ import annotations

import os

IS_WINDOWS = os.name == "nt"

#: How a user starts Compass on this platform, for messages and documentation.
BOOTSTRAP_COMMAND = ".\\compass" if IS_WINDOWS else "./compass"

__all__ = ["BOOTSTRAP_COMMAND", "IS_WINDOWS"]
