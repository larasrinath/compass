"""Hold a child until its owner has contained it, then become its supervisor.

Windows can only put a process into a Job Object once ``CreateProcess`` has
returned, so a child that spawns its own children immediately can leave
descendants outside the Job and outlive its owner.  This gate starts nothing
until the owner writes the release file, which the owner writes only after it
has verified the Job assignment.  The gate then runs the real command and
forwards its exit status.

Started as an isolated stdlib-only script (``python -I -u spawn_gate.py``), never
imported.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

RELEASE_TOKEN = b"go"
RELEASE_TIMEOUT_SECONDS = 60.0
POLL_SECONDS = 0.02
NOT_RELEASED_STATUS = 3
USAGE_STATUS = 2


def wait_for_release(
    release: Path, *, timeout: float = RELEASE_TIMEOUT_SECONDS
) -> bool:
    """Wait until the owner publishes the release token, or give up."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if release.read_bytes()[: len(RELEASE_TOKEN)] == RELEASE_TOKEN:
                return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print("usage: spawn_gate.py RELEASE_FILE -- COMMAND...", file=sys.stderr)
        return USAGE_STATUS
    release, command = Path(argv[0]), argv[2:]
    if not wait_for_release(release):
        print(
            "Compass never released this process; nothing was started.",
            file=sys.stderr,
        )
        return NOT_RELEASED_STATUS
    # Let the child handle CTRL_BREAK; keep the gate alive until the child has
    # finished. Otherwise the launcher could close its job during cleanup.
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: None)
    process = subprocess.Popen(command)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # The console interrupt already reached the child; wait it out so the
            # owner still sees one exit status for the whole tree.
            continue


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
