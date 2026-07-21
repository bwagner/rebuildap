"""Version info helper for uv-based Python scripts.

Usage:
    from version_info import get_version_info

    __version__ = "0.1.0"

    parser.add_argument("-V", "--version", action="version",
                        version=get_version_info(__version__))

Output example:
    0.1.0 (git:a3f9c2b-dirty, 2026-04-05 14:32:01 +0200) "fix the thing"
"""

import os
import subprocess

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))


def get_version_info(version):
    """Return version string enriched with git commit, dirty flag, and timestamp.

    Queries the git repo that contains *this source file*, not the caller's
    cwd. When installed into site-packages (not a git repo), the git calls
    fail and the plain version is returned.
    """
    try:
        rev = subprocess.check_output(
            ["git", "-C", _SRC_DIR, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", _SRC_DIR, "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # %ai (timestamp) and %s (subject) in one call; %n separates them, and
        # the timestamp has no newline, so the first line is the timestamp and
        # the remainder is the subject.
        ts, _, subject = (
            subprocess.check_output(
                ["git", "-C", _SRC_DIR, "log", "-1", "--format=%ai%n%s"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
            .partition("\n")
        )
        suffix = "-dirty" if dirty else ""
        return f'{version} (git:{rev}{suffix}, {ts}) "{subject}"'
    except Exception:
        return version
