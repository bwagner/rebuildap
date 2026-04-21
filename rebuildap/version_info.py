"""Version info helper for uv-based Python scripts.

Usage:
    from version_info import get_version_info

    __version__ = "0.1.0"

    parser.add_argument("-V", "--version", action="version",
                        version=get_version_info(__version__))

Output example:
    0.1.0 (git:a3f9c2b-dirty, 2026-04-05 14:32:01 +0200)
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
        ts = subprocess.check_output(
            ["git", "-C", _SRC_DIR, "log", "-1", "--format=%ai"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        suffix = "-dirty" if dirty else ""
        return f"{version} (git:{rev}{suffix}, {ts})"
    except Exception:
        return version
