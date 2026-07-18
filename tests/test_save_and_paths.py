"""Tests for aup3 save placement, label touching, and output-directory choice.

All of these are about *where* files land and *whether* an existing file is
respected, so they run against tmp_path with the Audacity call faked out.
"""

import os

import pytest

from rebuildap import audacity_funcs as af


@pytest.fixture
def project(tmp_path):
    """A project dir: audio file plus two versioned label files."""
    audio = tmp_path / "song.opus"
    audio.write_bytes(b"not really audio")
    (tmp_path / "parts_song.txt").write_text("0.0\t0.0\tintro\n")
    (tmp_path / "chords_song.txt").write_text("0.0\t0.0\tEm\n")
    return audio


def _fake_save(monkeypatch, record):
    def fake(filename, **kwargs):
        record.append((filename, kwargs))
        # Audacity would create the file; emulate that so callers can stat it.
        with open(filename, "wb") as fh:
            fh.write(b"aup3")
        return ""

    monkeypatch.setattr(af.pa, "save", fake)


# --- placement ------------------------------------------------------------


def test_aup3_goes_beside_the_audio_not_the_cwd(project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)  # run from somewhere else entirely
    assert af.aup3_path_for(project) == tmp_path / "song.aup3"


def test_aup3_keeps_the_audio_stem(tmp_path):
    audio = tmp_path / "weather_with_you.opus"
    assert af.aup3_path_for(audio).name == "weather_with_you.aup3"


def test_save_writes_beside_the_audio_when_run_from_elsewhere(
    project, tmp_path, monkeypatch
):
    calls = []
    _fake_save(monkeypatch, calls)
    monkeypatch.chdir(tmp_path.parent)

    saved = af.save_project_if_absent(project)

    assert saved == tmp_path / "song.aup3"
    assert calls[0][0] == str(tmp_path / "song.aup3")


# --- never overwrite ------------------------------------------------------


def test_existing_aup3_is_left_alone(project, tmp_path, monkeypatch):
    existing = tmp_path / "song.aup3"
    existing.write_bytes(b"the user's working copy")
    calls = []
    _fake_save(monkeypatch, calls)

    assert af.save_project_if_absent(project) is None
    assert calls == [], "pa.save must not be called for an existing project"
    assert existing.read_bytes() == b"the user's working copy"


def test_save_does_not_request_overwrite(project, monkeypatch):
    """pa.save's overwrite path unlinks the target first, so never ask for it."""
    calls = []
    _fake_save(monkeypatch, calls)
    af.save_project_if_absent(project)
    assert calls[0][1]["allow_overwrite"] is False


# --- label touching -------------------------------------------------------


def test_labels_older_than_the_aup3_are_brought_forward(project, tmp_path):
    aup3 = tmp_path / "song.aup3"
    aup3.write_bytes(b"aup3")
    stamp = aup3.stat().st_mtime
    for name in ("parts_song.txt", "chords_song.txt"):
        os.utime(tmp_path / name, (stamp - 500, stamp - 500))

    af.touch_label_files(project, aup3)

    for name in ("parts_song.txt", "chords_song.txt"):
        assert (tmp_path / name).stat().st_mtime >= stamp


def test_labels_newer_than_the_aup3_are_not_moved_backwards(project, tmp_path):
    aup3 = tmp_path / "song.aup3"
    aup3.write_bytes(b"aup3")
    stamp = aup3.stat().st_mtime
    newer = stamp + 500
    parts = tmp_path / "parts_song.txt"
    os.utime(parts, (newer, newer))

    af.touch_label_files(project, aup3)

    assert parts.stat().st_mtime == pytest.approx(newer)


def test_touching_leaves_label_content_untouched(project, tmp_path):
    aup3 = tmp_path / "song.aup3"
    aup3.write_bytes(b"aup3")
    before = (tmp_path / "parts_song.txt").read_text()

    af.touch_label_files(project, aup3)

    assert (tmp_path / "parts_song.txt").read_text() == before


def test_unrelated_label_files_are_not_touched(project, tmp_path):
    """The glob is stem-scoped: another song's labels must not be affected."""
    other = tmp_path / "parts_othersong.txt"
    other.write_text("0.0\t0.0\tx\n")
    old = other.stat().st_mtime - 500
    os.utime(other, (old, old))

    aup3 = tmp_path / "song.aup3"
    aup3.write_bytes(b"aup3")
    af.touch_label_files(project, aup3)

    assert other.stat().st_mtime == pytest.approx(old)


# --- the combination that -c actually depends on --------------------------


def test_after_save_and_touch_no_label_reads_as_outdated(
    project, tmp_path, monkeypatch
):
    """This is the whole point: -c must not see a fresh rebuild as stale.

    -c flags every label file whose mtime is older than the .aup3.
    """
    calls = []
    _fake_save(monkeypatch, calls)

    saved = af.save_project_if_absent(project)
    af.touch_label_files(project, saved)

    aup3_mtime = saved.stat().st_mtime
    outdated = [
        p for p in af.create_labels_glob(project) if p.stat().st_mtime < aup3_mtime
    ]
    assert outdated == []


# --- check mode skips before touching Audacity -----------------------------


def test_check_mode_skips_an_open_project_without_starting_audacity(
    monkeypatch, tmp_path, capsys
):
    """No Cmd-N, no launch, no pipe: a skipped project must cost nothing.

    Regression guard for the ordering — the check has to happen before
    assert_audacity, which would otherwise leave a stray empty window behind
    for every project a sweep skips.
    """
    import rebuildap
    from rebuildap import audacity_funcs as af
    from rebuildap import audacity_present as ap

    project = tmp_path / "angie.aup3"
    label = tmp_path / "parts_angie.txt"
    label.write_text("0.0\t1.0\tintro\n")
    project.write_text("not really a project")
    os.utime(label, (1, 1))  # older than the project, so -c would want to open

    monkeypatch.setattr(af, "project_already_open", lambda _f: True)
    for name in ("assert_audacity",):
        monkeypatch.setattr(
            ap, name, lambda *a, **k: pytest.fail(f"{name} must not run")
        )
    monkeypatch.setattr(
        af, "open_audio", lambda *a, **k: pytest.fail("open_audio must not run")
    )

    rebuildap.check_label_age(str(project), verbose=False)

    assert "angie.aup3" in capsys.readouterr().err
