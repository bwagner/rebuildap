"""Tests for the git-tracking report `check` adds to its label comparison.

Motivating case, 2026-09-08: `rebuildap check` on a real project reported all
four label files identical to their tracks -- a clean bill of health -- while
three of those four were untracked. Since the label `.txt` files are the source
of truth and the `.aup3` is disposable, "matches the project" and "is actually
safe" are different questions, and `check` was only answering the first.

These tests drive a **real** `git` against a tmp_path repo rather than faking
subprocess output. The module exists to parse another tool's output, so a fake
would only assert that our idea of `--porcelain -z` matches itself; the format
details worth pinning here (NUL separation, no quoting of odd filenames,
repo-root-relative paths, the two-path rename record) are exactly the ones a
fake would get wrong in the same direction as the code.
"""

import subprocess

import pytest

from rebuildap import git_tracking as gt

# Committing needs an identity, and the test must not depend on -- or touch --
# the developer's global git config.
_GIT_IDENTITY = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    """An initialized repo with one committed label file."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "chords_song.txt").write_text("0.0\t0.0\tEm\n")
    _git(tmp_path, "add", "chords_song.txt")
    _git(tmp_path, *_GIT_IDENTITY, "commit", "-q", "-m", "initial")
    return tmp_path


def _write(path, text="0.0\t0.0\tx\n"):
    path.write_text(text)
    return path


# --- the three states a label file can be in ------------------------------


def test_a_committed_unmodified_file_is_not_reported(repo):
    assert gt.unversioned([repo / "chords_song.txt"]) == {}


def test_an_untracked_file_is_reported(repo):
    parts = _write(repo / "parts_song.txt")
    assert gt.unversioned([parts]) == {parts: gt.UNTRACKED}


def test_a_tracked_file_with_uncommitted_edits_is_reported(repo):
    chords = repo / "chords_song.txt"
    chords.write_text("0.0\t0.0\tAm\n")
    assert gt.unversioned([chords]) == {chords: gt.UNCOMMITTED}


def test_staged_but_not_committed_still_counts_as_uncommitted(repo):
    """`git add` is not safety: the content is in no commit yet."""
    parts = _write(repo / "parts_song.txt")
    _git(repo, "add", "parts_song.txt")
    assert gt.unversioned([parts]) == {parts: gt.UNCOMMITTED}


# --- what must NOT be reported --------------------------------------------


def test_a_gitignored_aup3_is_not_reported(repo):
    """The real batch01 layout: `.gitignore` has `*.aup3`, and the project file
    is *meant* to be unversioned. It must not be flagged."""
    (repo / ".gitignore").write_text("*.aup3\n")
    aup3 = _write(repo / "song.aup3", "not really a project")
    assert gt.unversioned([aup3]) == {}


def test_outside_a_repository_there_is_no_answer(tmp_path):
    """rebuildap does not require the labels to live in git, so 'no repo' is
    None -- distinct from {} -- and the caller stays silent."""
    assert gt.unversioned([_write(tmp_path / "parts_song.txt")]) is None


def test_no_files_means_no_git_call(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("ran git for nothing")
    )
    assert gt.unversioned([]) == {}


# --- the shape the reported project actually had ---------------------------


def test_the_reported_mix_of_tracked_and_untracked(repo):
    """One tracked label file, three untracked, plus an ignored .aup3."""
    (repo / ".gitignore").write_text("*.aup3\n")
    _git(repo, "add", ".gitignore")
    _git(repo, *_GIT_IDENTITY, "commit", "-q", "-m", "ignore projects")
    untracked = [
        _write(repo / f"{name}_song.txt") for name in ("bars", "beats", "parts")
    ]
    aup3 = _write(repo / "song.aup3", "x")
    tracked = repo / "chords_song.txt"

    found = gt.unversioned([tracked, *untracked, aup3])
    assert found == dict.fromkeys(untracked, gt.UNTRACKED)


# --- output-format details a fake would have got wrong ---------------------


def test_a_filename_with_a_space_is_parsed(repo):
    """--porcelain quotes such paths unless -z is used."""
    odd = _write(repo / "parts my song.txt")
    assert gt.unversioned([odd]) == {odd: gt.UNTRACKED}


def test_a_filename_with_a_quote_is_parsed(repo):
    odd = _write(repo / 'parts_"quoted".txt')
    assert gt.unversioned([odd]) == {odd: gt.UNTRACKED}


def test_a_rename_does_not_desync_the_parser(repo):
    """A staged rename is a two-path record in -z output. Reading it as one
    would consume the following entry's path as a status code."""
    _git(repo, "mv", "chords_song.txt", "chords_renamed_song.txt")
    renamed = repo / "chords_renamed_song.txt"
    parts = _write(repo / "parts_song.txt")

    found = gt.unversioned([renamed, parts])
    assert found[parts] == gt.UNTRACKED, "the entry after the rename was misread"
    assert renamed in found


def test_a_file_in_a_subdirectory_is_matched(repo):
    """--porcelain paths are relative to the repo root, not to the files."""
    sub = repo / "batch01" / "song"
    sub.mkdir(parents=True)
    parts = _write(sub / "parts_song.txt")
    assert gt.unversioned([parts]) == {parts: gt.UNTRACKED}


def test_files_in_two_different_repositories_are_both_answered(tmp_path):
    """Label files are grouped by directory, so a set spanning repos still gets
    a complete answer rather than whichever repo happened to be asked first."""
    answers = {}
    for name in ("one", "two"):
        r = tmp_path / name
        r.mkdir()
        _git(r, "init", "-q", "-b", "main")
        answers[_write(r / "parts_song.txt")] = gt.UNTRACKED
    assert gt.unversioned(list(answers)) == answers


# --- what `check` prints ---------------------------------------------------
#
# The git state rides on the comparison line rather than in a list of its own:
# each label file gets one line in this output, and both facts about it belong
# there.


def _diff_line(label_file, reason, differs=False):
    """Run _report_diff for one file and return what it printed."""
    import rebuildap

    from_file = ["0.0\t0.0\tEm\n"]
    from_proj = ["0.0\t0.0\tAm\n"] if differs else list(from_file)
    rebuildap._report_diff(label_file, "chords", from_file, from_proj, reason)


def test_a_safe_file_gets_no_marker(capsys, tmp_path):
    _diff_line(tmp_path / "chords_song.txt", None)
    out = capsys.readouterr().out
    assert "are identical." in out
    assert "[git:" not in out


def test_an_untracked_file_is_marked_on_its_own_line(capsys, tmp_path):
    _diff_line(tmp_path / "parts_song.txt", gt.UNTRACKED)
    out = capsys.readouterr().out
    assert "parts_song.txt" in out
    assert "are identical [git: untracked]." in out


def test_an_uncommitted_file_is_marked_too(capsys, tmp_path):
    _diff_line(tmp_path / "chords_song.txt", gt.UNCOMMITTED)
    assert "are identical [git: uncommitted changes]." in capsys.readouterr().out


def test_a_differing_file_is_marked_before_the_colon(capsys, tmp_path):
    """The diff follows the ':', so the marker cannot go after it."""
    _diff_line(tmp_path / "parts_song.txt", gt.UNTRACKED, differs=True)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("[git: untracked]:")
    assert lines[0].startswith("Label file parts_song.txt differs from")


def test_a_differing_safe_file_keeps_the_bare_colon(capsys, tmp_path):
    _diff_line(tmp_path / "parts_song.txt", None, differs=True)
    assert capsys.readouterr().out.splitlines()[0].endswith("track chords:")


def test_the_marker_shares_the_comparison_line_stream(capsys, tmp_path):
    """Not a separate stderr report - it is part of the comparison output."""
    _diff_line(tmp_path / "parts_song.txt", gt.UNTRACKED)
    captured = capsys.readouterr()
    assert "[git: untracked]" in captured.out
    assert captured.err == ""


# --- the whole comparison loop --------------------------------------------


def test_each_file_carries_its_own_state_and_git_is_asked_once(
    monkeypatch, capsys, tmp_path
):
    import rebuildap
    from rebuildap import audacity_funcs as af

    parts = tmp_path / "parts_song.txt"
    chords = tmp_path / "chords_song.txt"
    for f in (parts, chords):
        f.write_text("0.0\t0.0\tEm\n")
    (tmp_path / "song.aup3").write_text("x")

    calls = []

    def fake_unversioned(files):
        calls.append(list(files))
        return {parts: gt.UNTRACKED}

    monkeypatch.setattr(rebuildap.git_tracking, "unversioned", fake_unversioned)
    monkeypatch.setattr(
        af,
        "get_label_tracks_content_via_getinfo",
        lambda: {"parts": "0.0\t0.0\tEm\n", "chords": "0.0\t0.0\tEm\n"},
    )

    rebuildap._check_label_age_via_getinfo(tmp_path / "song.aup3", [chords, parts])

    out = capsys.readouterr().out
    assert "chords_song.txt and exported label track chords are identical." in out
    assert (
        "parts_song.txt and exported label track parts are identical [git: untracked]."
        in out
    )
    assert len(calls) == 1, "git should be asked once for the whole set, not per file"


def test_outside_a_repository_no_line_is_marked(monkeypatch, capsys, tmp_path):
    import rebuildap
    from rebuildap import audacity_funcs as af

    chords = tmp_path / "chords_song.txt"
    chords.write_text("0.0\t0.0\tEm\n")
    (tmp_path / "song.aup3").write_text("x")
    monkeypatch.setattr(rebuildap.git_tracking, "unversioned", lambda _f: None)
    monkeypatch.setattr(
        af, "get_label_tracks_content_via_getinfo", lambda: {"chords": "0.0\t0.0\tEm\n"}
    )

    rebuildap._check_label_age_via_getinfo(tmp_path / "song.aup3", [chords])
    assert "[git:" not in capsys.readouterr().out
