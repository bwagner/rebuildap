"""Offline tests for how much of each difference ``check`` prints.

After a project was re-gridded, every label in its bars/beats files differed and
``check`` printed 8089 lines of unified diff - all of it already on disk in the
inspection copies ``<track>.txt``. It now prints a summary line per differing
file and the first :data:`rebuildap.DIFF_PREVIEW_LINES` lines of the diff, then
says how to see the rest; ``check -d`` prints every diff in full.
"""

from pathlib import Path

from test_save_and_paths import _check_with_stubbed_audacity, _dispatched

import rebuildap


def _labels(count, offset=0.0):
    return [f"{i + offset}\t{i + offset}\tL{i}\n" for i in range(count)]


def _diff_body(out):
    """The unified-diff lines of ``out`` (its +/- and context lines, no headers)."""
    return [
        line
        for line in out.splitlines()
        if line and line[0] in "+- " and not line.startswith(("+++", "---"))
    ]


def _report(capsys, file_labels, proj_labels, full_diff=False):
    rebuildap._report_diff(
        Path("beats_song.txt"),
        "beats",
        file_labels,
        proj_labels,
        full_diff=full_diff,
        inspection=Path("/proj/beats.txt"),
    )
    return capsys.readouterr().out


def test_a_short_diff_is_shown_whole(capsys):
    file_labels = _labels(2)
    proj_labels = [file_labels[0], "1.5\t1.5\tL1\n"]

    out = _report(capsys, file_labels, proj_labels)

    assert "1 of 2 labels differ" in out
    assert "-1.0\t1.0\tL1" in out and "+1.5\t1.5\tL1" in out
    assert "-d" not in out


def test_a_long_diff_shows_its_first_lines_and_says_how_to_see_the_rest(capsys):
    count = rebuildap.DIFF_PREVIEW_LINES * 5

    out = _report(capsys, _labels(count), _labels(count, offset=0.5))

    assert f"{count} of {count} labels differ" in out
    assert len(_diff_body(out)) == rebuildap.DIFF_PREVIEW_LINES
    last = out.rstrip().splitlines()[-1]
    assert "-d" in last
    assert "/proj/beats.txt" in last
    hidden = 2 * count - rebuildap.DIFF_PREVIEW_LINES  # every label twice: - and +
    assert f"{hidden} more" in last


def test_the_full_diff_is_shown_on_request(capsys):
    count = rebuildap.DIFF_PREVIEW_LINES * 5

    out = _report(capsys, _labels(count), _labels(count, offset=0.5), full_diff=True)

    assert len(_diff_body(out)) == 2 * count
    assert "more" not in out


def test_identical_files_say_so_in_one_line(capsys):
    out = _report(capsys, _labels(3), _labels(3))

    assert out.count("\n") == 1
    assert "are identical" in out


def test_check_dash_d_asks_for_full_diffs(monkeypatch):
    captured = _dispatched(monkeypatch, ["rebuildap", "check", "-d"], "check_label_age")
    assert captured["kwargs"]["full_diff"] is True


def test_check_shows_diff_previews_by_default(monkeypatch):
    captured = _dispatched(monkeypatch, ["rebuildap", "check"], "check_label_age")
    assert captured["kwargs"]["full_diff"] is False


def test_full_diff_reaches_the_comparison(monkeypatch, tmp_path, capsys):
    """End to end through check_label_age: -d's flag is not dropped on the way."""
    count = rebuildap.DIFF_PREVIEW_LINES * 5
    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    (tmp_path / "beats_song.txt").write_text("".join(_labels(count)))

    _check_with_stubbed_audacity(
        monkeypatch,
        project,
        {"beats": "".join(_labels(count, offset=0.5))},
        deep=True,
        full_diff=True,
    )

    assert len(_diff_body(capsys.readouterr().out)) == 2 * count
