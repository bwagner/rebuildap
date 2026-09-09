"""Whether the label files ``check`` just compared are actually safe in git.

``check`` answers "do the label files match the project?". That is only half of
what matters here: the ``.aup3`` is disposable and the label ``.txt`` files are
the source of truth *because they are versioned*, so a file that matches its
track perfectly but exists in no commit is not safe at all. Observed 2026-09-08
on a real project: ``check`` reported four label files identical, and three of
them were untracked.

Shells out to ``git`` rather than taking a library dependency, matching
``version_info.py``, which already asks git questions the same way. Two ways of
talking to git in one codebase would be one too many.

Known gap, deliberate: a label file that is *gitignored* produces no status
entry and therefore reads as safe. Catching that needs ``--ignored`` and a
third category; the two categories here are the ones asked for. An ignored
``.aup3`` is silent for the same reason, which is exactly right -- it is meant
to be unversioned.
"""

import subprocess
from collections import defaultdict
from pathlib import Path

GIT = "git"

# The two ways a label file can fail to be safe. Values are shown to the user.
UNTRACKED = "untracked"
UNCOMMITTED = "uncommitted changes"

_UNTRACKED_CODE = "??"
# In --porcelain -z, a rename/copy record is followed by a second NUL-terminated
# path (the origin). Reading it as an entry of its own would consume the next
# entry's status code.
_TWO_PATH_CODES = ("R", "C")
_STATUS_FIELD_WIDTH = 3  # "XY " precedes the path
_STATUS_CODE_WIDTH = 2


def _run_git(directory, *args):
    """Run a git command in ``directory``. Returns stdout, or None on any failure.

    Failure is not distinguished by kind on purpose: outside a repository, with
    git absent, or with the directory unreadable, the answer this module can
    give is the same one -- none.
    """
    try:
        result = subprocess.run(
            [GIT, "-C", str(directory), *args],
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return None
    return result.stdout if result.returncode == 0 else None


def _repo_root(directory):
    out = _run_git(directory, "rev-parse", "--show-toplevel")
    return Path(out.strip()).resolve() if out else None


def _parse_porcelain_z(out):
    """Yield ``(code, path)`` pairs from ``git status --porcelain -z`` output.

    ``-z`` rather than plain ``--porcelain`` because the latter quotes and
    escapes any path containing a space, a quote or a non-ASCII byte, and
    separates records by newline -- which a filename may contain.
    """
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) <= _STATUS_FIELD_WIDTH:
            continue  # trailing empty field from the final separator
        code = entry[:_STATUS_CODE_WIDTH]
        if any(c in _TWO_PATH_CODES for c in code):
            i += 1  # skip the origin path belonging to this record
        yield code, entry[_STATUS_FIELD_WIDTH:]


def _reason(code):
    """What a status code means for "is this file safe?".

    Anything that is not untracked and appears in `status` at all is a
    difference from HEAD, staged or not. Staged counts: `git add` puts content
    in no commit, so it is not safe either.
    """
    return UNTRACKED if code == _UNTRACKED_CODE else UNCOMMITTED


def unversioned(label_files):
    """Map the given paths to why they are not safely committed.

    Returns ``{}`` when every file is committed and unmodified, and ``None``
    when none of them is in a git repository at all. The distinction matters to
    the caller: rebuildap does not require the label files to be versioned, so
    "no repository" must produce silence rather than a warning.

    Files are grouped by directory so that a set spanning several repositories
    still gets a complete answer.
    """
    by_directory = defaultdict(list)
    for original in label_files:
        by_directory[Path(original).expanduser().resolve().parent].append(original)
    if not by_directory:
        return {}

    found = {}
    in_a_repo = False
    for directory, originals in by_directory.items():
        root = _repo_root(directory)
        if root is None:
            continue
        in_a_repo = True
        # Keep the caller's own Path objects as the keys: they are what the
        # caller will print, and resolve() can rewrite a path (/var ->
        # /private/var on macOS) into something it never asked about.
        resolved = {Path(o).expanduser().resolve(): o for o in originals}
        # No --untracked-files=all: with an explicit pathspec git reports the
        # files themselves, never a collapsed untracked-*directory* entry.
        # Verified by mutation - adding the flag changes no test outcome, and
        # test_a_file_in_a_subdirectory_is_matched covers the case it would
        # have guarded.
        out = _run_git(
            directory,
            "status",
            "--porcelain",
            "-z",
            "--",
            *(str(p) for p in resolved),
        )
        if out is None:
            continue
        for code, relative in _parse_porcelain_z(out):
            # --porcelain paths are relative to the repository root, never to
            # the directory git was run in.
            original = resolved.get((root / relative).resolve())
            if original is not None:
                found[original] = _reason(code)
    return found if in_a_repo else None
