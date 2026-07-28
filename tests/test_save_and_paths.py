"""Tests for aup3 save placement, label touching, and output-directory choice.

All of these are about *where* files land and *whether* an existing file is
respected, so they run against tmp_path with the Audacity call faked out.
"""

import os

import pytest

from rebuildap import audacity_funcs as af
from rebuildap import audacity_present as ap


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


def test_check_prints_nothing_to_do_without_verbose(tmp_path, capsys):
    """A non-verbose -c that finds nothing must still say so on stdout.

    All label files newer than the .aup3 -> nothing to compare. The message
    used to be gated behind -v, so the run looked like it did nothing at all.
    """
    import rebuildap

    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    os.utime(project, (1, 1))  # old project
    label = tmp_path / "parts_song.txt"
    label.write_text("0.0\t1.0\tintro\n")  # written now, so newer than project

    rebuildap.check_label_age(str(project), verbose=False)

    assert "Nothing to do" in capsys.readouterr().out


def test_check_prints_when_no_label_files(tmp_path, capsys):
    """No matching label files at all is reported, not silent."""
    import rebuildap

    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")

    rebuildap.check_label_age(str(project), verbose=False)

    assert "No label files found" in capsys.readouterr().out


def _check_with_stubbed_audacity(monkeypatch, project, contents, **kwargs):
    """Run check_label_age with the live layer faked out."""
    import rebuildap
    from rebuildap import audacity_present as ap

    monkeypatch.setattr(af, "project_already_open", lambda _f: False)
    monkeypatch.setattr(ap, "assert_audacity", lambda *a, **k: None)
    monkeypatch.setattr(ap, "close_owned_window", lambda *a, **k: None)
    monkeypatch.setattr(af, "open_audio", lambda *a, **k: None)
    monkeypatch.setattr(af, "get_label_tracks_content_via_getinfo", lambda: contents)
    rebuildap.check_label_age(str(project), verbose=False, **kwargs)


def test_a_newer_label_file_is_still_compared_once_audacity_is_open(
    monkeypatch, tmp_path, capsys
):
    """No per-file gate: once the project is open, every label file is compared.

    The gate is a whole-*project* decision (is it worth opening Audacity at all),
    not a per-file filter. Filtering per file saved ~0.2ms each -- the GetInfo
    fetches every track in one call anyway -- while silently hiding tracks: a run
    showing parts/bars/beats gave no clue 'chords' had been skipped for being
    newer, which is exactly what -q and -t make it by rewriting the .txt.
    """
    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    old = tmp_path / "parts_song.txt"
    old.write_text("0.0\t1.0\tintro\n")
    os.utime(old, (1, 1))  # older than the project -> Audacity is worth opening
    newer = tmp_path / "chords_song.txt"
    newer.write_text("0.0\t1.0\tEm\n")  # newer than the project

    _check_with_stubbed_audacity(
        monkeypatch, project, {"parts": "0.0\t1.0\tintro\n", "chords": "0.0\t1.0\tEm\n"}
    )

    out = capsys.readouterr().out
    assert "chords" in out, "a newer label file must still be compared"
    assert "parts" in out
    assert "Not compared" not in out, "nothing is skipped, so nothing to report"


def test_a_newer_label_file_that_diverges_is_reported(monkeypatch, tmp_path, capsys):
    """The point of comparing it: a real divergence in a newer file is surfaced,
    where the per-file gate stayed silent about it."""
    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    old = tmp_path / "parts_song.txt"
    old.write_text("0.0\t1.0\tintro\n")
    os.utime(old, (1, 1))
    newer = tmp_path / "chords_song.txt"
    newer.write_text("0.0\t1.0\tEm\n")  # newer, and differs from the project

    _check_with_stubbed_audacity(
        monkeypatch,
        project,
        {"parts": "0.0\t1.0\tintro\n", "chords": "0.0\t1.0\tAm\n"},
    )

    out = capsys.readouterr().out
    assert "chords" in out
    assert "identical" not in out.split("chords")[-1], "the diff must be reported"


def test_the_versioned_label_file_is_never_overwritten_by_check(
    monkeypatch, tmp_path, capsys
):
    """Comparing newer files must not clobber a .txt that -q or -t just wrote.

    A divergence writes the *export artifact* <short_name>.txt, never the
    versioned <short_name>_<stem>.txt.
    """
    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    old = tmp_path / "parts_song.txt"
    old.write_text("0.0\t1.0\tintro\n")
    os.utime(old, (1, 1))
    versioned = tmp_path / "chords_song.txt"
    versioned.write_text("0.0\t1.0\tEm\n")

    _check_with_stubbed_audacity(
        monkeypatch,
        project,
        {"parts": "0.0\t1.0\tintro\n", "chords": "0.0\t1.0\tAm\n"},
    )

    assert versioned.read_text() == "0.0\t1.0\tEm\n", "source of truth untouched"
    assert (tmp_path / "chords.txt").read_text() == "0.0\t1.0\tAm\n"


def test_every_label_file_newer_skips_opening_audacity(monkeypatch, tmp_path, capsys):
    """The gate's whole job: when no label file predates the .aup3, nothing an
    open could reveal, so Audacity is never started. This is the 2-3s (plus a GUI
    window, plus a crash-prone interaction) the gate exists to save - and the only
    saving it ever made, which is why the per-file filter was dropped."""
    import rebuildap
    from rebuildap import audacity_present as ap

    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    os.utime(project, (1, 1))
    (tmp_path / "parts_song.txt").write_text("0.0\t1.0\tintro\n")

    monkeypatch.setattr(
        ap, "assert_audacity", lambda *a, **k: pytest.fail("must not start Audacity")
    )
    monkeypatch.setattr(
        af, "open_audio", lambda *a, **k: pytest.fail("must not open the project")
    )

    rebuildap.check_label_age(str(project), verbose=False)

    assert "Nothing to do" in capsys.readouterr().out


def test_one_older_label_file_is_enough_to_open_audacity(monkeypatch, tmp_path):
    """The gate is a whole-project decision: a single older file means the open is
    worth it, and then everything is compared."""
    import rebuildap
    from rebuildap import audacity_present as ap

    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    old = tmp_path / "parts_song.txt"
    old.write_text("0.0\t1.0\tintro\n")
    os.utime(old, (1, 1))
    (tmp_path / "chords_song.txt").write_text("0.0\t1.0\tEm\n")  # newer

    opened = []
    monkeypatch.setattr(af, "project_already_open", lambda _f: False)
    monkeypatch.setattr(ap, "assert_audacity", lambda *a, **k: None)
    monkeypatch.setattr(ap, "close_owned_window", lambda *a, **k: None)
    monkeypatch.setattr(af, "open_audio", lambda *a, **k: opened.append(True))
    monkeypatch.setattr(
        af,
        "get_label_tracks_content_via_getinfo",
        lambda: {"parts": "0.0\t1.0\tintro\n", "chords": "0.0\t1.0\tEm\n"},
    )

    rebuildap.check_label_age(str(project), verbose=False)

    assert opened == [True]


def test_check_reports_label_track_present_only_in_audacity(
    monkeypatch, tmp_path, capsys
):
    """A label track in Audacity with no .txt on disk must be reported.

    The comparison loop is file-driven, so a track that exists only in the
    project (never exported) was silently dropped. It should be named, and the
    user pointed at how to export it — without ever auto-writing the file.
    """
    import rebuildap
    from rebuildap import audacity_present as ap

    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    os.utime(project, (1, 1))  # old project; deep bypasses the mtime gate anyway
    label = tmp_path / "parts_song.txt"
    label.write_text("0.0\t1.0\tintro\n")  # matches the 'parts' track exactly

    monkeypatch.setattr(af, "project_already_open", lambda _f: False)
    monkeypatch.setattr(ap, "assert_audacity", lambda *a, **k: None)
    monkeypatch.setattr(ap, "close_owned_window", lambda *a, **k: None)
    monkeypatch.setattr(af, "open_audio", lambda *a, **k: None)
    monkeypatch.setattr(
        af,
        "get_label_tracks_content_via_getinfo",
        lambda: {
            "parts": "0.0\t1.0\tintro\n",  # backed by parts_song.txt
            "chords": "0.0\t1.0\tEm\n",  # only in Audacity, no file
        },
    )

    rebuildap.check_label_age(str(project), verbose=False, deep=True)

    out = capsys.readouterr().out
    assert "chords" in out, "the Audacity-only track must be named"
    assert "no label file" in out
    assert "rebuildap" in out, "the export suggestion must be shown"
    assert not (tmp_path / "chords_song.txt").exists(), (
        "an Audacity-only track must never be auto-written"
    )


def test_deep_check_opens_even_when_label_is_newer(monkeypatch, tmp_path):
    """-c -f bypasses the mtime gate: a newer label file is still compared.

    The non-deep path returns before touching Audacity here; deep must instead
    reach open_audio and the getinfo comparison.
    """
    import rebuildap
    from rebuildap import audacity_funcs as af
    from rebuildap import audacity_present as ap

    project = tmp_path / "song.aup3"
    project.write_bytes(b"aup3")
    os.utime(project, (1, 1))  # old project
    label = tmp_path / "parts_song.txt"
    label.write_text("0.0\t1.0\tintro\n")  # newer than project

    opened = []
    monkeypatch.setattr(af, "project_already_open", lambda _f: False)
    monkeypatch.setattr(ap, "assert_audacity", lambda *a, **k: None)
    monkeypatch.setattr(ap, "close_owned_window", lambda *a, **k: None)
    monkeypatch.setattr(af, "open_audio", lambda *a, **k: opened.append(a) or None)
    monkeypatch.setattr(
        af,
        "get_label_tracks_content_via_getinfo",
        lambda: {"parts": "0.0\t1.0\tintro\n"},
    )

    rebuildap.check_label_age(str(project), verbose=False, deep=True)

    assert opened, "deep check must open the project despite the newer label file"


# --- not rebuilding into a window that will be thrown away -----------------
#
# `rebuildap song.opus` when song.aup3 already exists used to import the audio
# and every label file, then discover the .aup3 at save time and decline to
# overwrite — leaving the rebuilt project open and *unsaved*, which nothing can
# safely close (Cmd-W on an unsaved project raises "Save changes?", the dialog
# that wedges the pipe). The existing file is knowable up front, so none of that
# work should start.


def _forbid_audacity(monkeypatch):
    import rebuildap
    from rebuildap import audacity_present as ap

    monkeypatch.setattr(
        ap, "assert_audacity", lambda *a, **k: pytest.fail("Audacity must not start")
    )
    monkeypatch.setattr(
        af, "open_audio", lambda *a, **k: pytest.fail("nothing must be imported")
    )
    return rebuildap


def test_rebuild_refuses_when_the_aup3_already_exists(project, tmp_path, monkeypatch):
    (tmp_path / "song.aup3").write_bytes(b"the user's working copy")
    rebuildap = _forbid_audacity(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        rebuildap.rebuild(str(project))

    assert "song.aup3" in str(excinfo.value)


def test_the_refusal_points_at_the_way_forward(project, tmp_path, monkeypatch):
    (tmp_path / "song.aup3").write_bytes(b"the user's working copy")
    rebuildap = _forbid_audacity(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        rebuildap.rebuild(str(project))

    assert "-n" in str(excinfo.value)


def test_no_save_still_rebuilds_into_a_window(project, tmp_path, monkeypatch):
    """With -n the user asked for a throwaway window on purpose."""
    (tmp_path / "song.aup3").write_bytes(b"the user's working copy")
    import rebuildap
    from rebuildap import audacity_present as ap

    ran = []
    monkeypatch.setattr(ap, "assert_audacity", lambda *a, **k: None)
    monkeypatch.setattr(af, "assert_not_already_open", lambda _f: None)
    monkeypatch.setattr(af, "open_audio", lambda *a, **k: ran.append("imported"))

    rebuildap.rebuild(str(project), save=False)

    assert ran == ["imported"]


def test_an_existing_aup3_does_not_block_exporting_from_an_aup3(tmp_path, monkeypatch):
    """`rebuildap song.aup3` reads an existing project — that is the whole point."""
    aup3 = tmp_path / "song.aup3"
    aup3.write_bytes(b"a project")
    import rebuildap
    from rebuildap import audacity_present as ap

    ran = []
    monkeypatch.setattr(ap, "assert_audacity", lambda *a, **k: None)
    monkeypatch.setattr(af, "assert_not_already_open", lambda _f: None)
    monkeypatch.setattr(af, "open_audio", lambda *a, **k: ran.append("opened"))
    monkeypatch.setattr(
        af, "export_label_tracks_via_getinfo", lambda *a, **k: ran.append("exported")
    )

    rebuildap.rebuild(str(aup3))

    assert ran == ["opened", "exported"]


# --- no-arg export: guard on the current directory -------------------------
#
# The no-arg export writes the source-of-truth label files, and only ever into
# the *current* directory. When cwd is the project's own dir it proceeds; when
# it is not, it points at where Open Recent says the project lives and refuses;
# only when it cannot suggest anywhere better does it export into cwd with a
# heads-up. So it never silently scatters labels into a wrong-but-plausible dir.


def _menus_with(paths):
    """A minimal GetInfo: Type=Menus response whose Open Recent lists `paths`."""
    entries = ['{ "depth":1, "flags":0, "label":"Open Recent", "accel":"" }']
    entries += [f'{{ "depth":2, "flags":0, "label":"{p}", "accel":"" }}' for p in paths]
    entries.append('{ "depth":1, "flags":0, "label":"Close", "accel":"Ctrl+W" }')
    return "\n[ " + ",\n".join(entries) + " ]\nBatchCommand finished: OK\n"


def _stub_open_project(monkeypatch, stem, selected):
    """Make the no-arg branch reachable: prerequisites pass and the open project
    reports `stem` with `selected` label-track indices."""
    import rebuildap

    monkeypatch.setattr(rebuildap, "prerequisites_met", lambda: True)
    monkeypatch.setattr(af, "open_project_stem", lambda: stem)
    monkeypatch.setattr(af, "get_selected_label_track_indices", lambda: selected)


def _fail_if_exported(monkeypatch):
    """Trip the test if either export function is called."""
    for name in (
        "export_selected_label_tracks_via_getinfo",
        "export_label_tracks_via_getinfo",
    ):
        monkeypatch.setattr(
            af, name, lambda *a, **k: pytest.fail(f"{name} must not run")
        )


# --- dir_holds_project ----------------------------------------------------


def test_dir_holds_project_true_on_aup3(tmp_path):
    (tmp_path / "song.aup3").write_bytes(b"x")
    assert af.dir_holds_project(tmp_path, "song")


def test_dir_holds_project_true_on_label_file(tmp_path):
    (tmp_path / "chords_song.txt").write_text("x")
    assert af.dir_holds_project(tmp_path, "song")


def test_dir_holds_project_false_when_absent(tmp_path):
    assert not af.dir_holds_project(tmp_path, "song")


# --- find_recent_project_dirs (advisory-only candidate list) --------------


def test_find_recent_project_dirs_unique_existing(tmp_path, monkeypatch):
    proj = tmp_path / "a" / "song.aup3"
    proj.parent.mkdir()
    proj.write_bytes(b"x")
    monkeypatch.setattr(af.pa, "do", lambda cmd: _menus_with([str(proj)]))
    assert af.find_recent_project_dirs("song") == [proj.parent]


def test_find_recent_project_dirs_lists_all_same_stem_candidates(tmp_path, monkeypatch):
    a = tmp_path / "a" / "song.aup3"
    b = tmp_path / "b" / "song.aup3"
    for p in (a, b):
        p.parent.mkdir()
        p.write_bytes(b"x")
    monkeypatch.setattr(af.pa, "do", lambda cmd: _menus_with([str(a), str(b)]))
    assert af.find_recent_project_dirs("song") == sorted([a.parent, b.parent])


def test_find_recent_project_dirs_skips_nonexistent_file(tmp_path, monkeypatch):
    ghost = tmp_path / "gone" / "song.aup3"  # never created on disk
    monkeypatch.setattr(af.pa, "do", lambda cmd: _menus_with([str(ghost)]))
    assert af.find_recent_project_dirs("song") == []


def test_find_recent_project_dirs_no_stem_match(tmp_path, monkeypatch):
    other = tmp_path / "a" / "other.aup3"
    other.parent.mkdir()
    other.write_bytes(b"x")
    monkeypatch.setattr(af.pa, "do", lambda cmd: _menus_with([str(other)]))
    assert af.find_recent_project_dirs("song") == []


# --- open_project_stem (the open project's identity) ----------------------
#
# The stem that names the versioned .txt files is the open project's *.aup3*
# stem, not its wave track's name. The two differ whenever a project was made by
# Save-As from another one -- a transposed variant `<stem>_G.aup3` keeps the
# original's wave track name, so naming by the wave track wrote the variant's
# labels over the original's files.

_UNSET = object()  # "the test did not say", distinct from an explicit None


def _stub_identity(
    monkeypatch, titles, recent_paths, wave_stem="song", frontmost=_UNSET
):
    """Fake the routes open_project_stem uses: the two it intersects, the
    frontmost-window tiebreaker, and the wave-stem fallback.

    ``frontmost`` defaults to the first title, since the common case is one
    project window that is also the front one."""
    monkeypatch.setattr(ap, "audacity_window_names", lambda: list(titles))
    monkeypatch.setattr(af.pa, "do", lambda cmd: _menus_with(recent_paths))
    monkeypatch.setattr(af, "open_project_wave_stem", lambda *a, **k: wave_stem)
    if frontmost is _UNSET:
        frontmost = titles[0] if titles else None
    monkeypatch.setattr(ap, "frontmost_audacity_window_name", lambda: frontmost)


def _make_project(tmp_path, name, subdir="a"):
    proj = tmp_path / subdir / f"{name}.aup3"
    proj.parent.mkdir(parents=True, exist_ok=True)
    proj.write_bytes(b"x")
    return proj


def test_open_project_stem_uses_the_open_windows_aup3_stem(tmp_path, monkeypatch):
    proj = _make_project(tmp_path, "song")
    _stub_identity(monkeypatch, ["song"], [str(proj)])
    assert af.open_project_stem() == "song"


def test_open_project_stem_prefers_aup3_stem_over_wave_track_name(
    tmp_path, monkeypatch
):
    """The regression this whole change is about: a `_G` variant must not be
    named after the wave track it inherited from the project it was copied from."""
    variant = _make_project(tmp_path, "song_G")
    _stub_identity(monkeypatch, ["song_G"], [str(variant)], wave_stem="song")
    assert af.open_project_stem() == "song_G"


def _two_projects(tmp_path):
    return _make_project(tmp_path, "song"), _make_project(tmp_path, "song_G")


@pytest.mark.parametrize("front", ["song", "song_G"])
def test_open_project_stem_follows_the_frontmost_window(tmp_path, monkeypatch, front):
    """Measured on 3.7.8 (2026-07-28): mod-script-pipe acts on the frontmost
    project window, so with several open the front one *is* the answer."""
    original, variant = _two_projects(tmp_path)
    _stub_identity(
        monkeypatch,
        ["song", "song_G"],
        [str(original), str(variant)],
        frontmost=front,
    )
    assert af.open_project_stem() == front


def test_open_project_stem_refuses_when_the_frontmost_window_is_not_a_project(
    tmp_path, monkeypatch
):
    """A modal dialog or About box can hold front position; its title is not a
    project, so there is nothing to break the tie with."""
    original, variant = _two_projects(tmp_path)
    _stub_identity(
        monkeypatch,
        ["song", "song_G"],
        [str(original), str(variant)],
        frontmost="About Audacity",
    )
    with pytest.raises(af.ProjectIdentityError) as excinfo:
        af.open_project_stem()
    lines = str(excinfo.value).splitlines()
    # Each candidate on its own line: a comma-joined run of long project stems is
    # unreadable, and these are exactly the names the user has to tell apart.
    assert f"{af.LIST_BULLET}song" in lines
    assert f"{af.LIST_BULLET}song_G" in lines


def test_open_project_stem_refuses_when_the_frontmost_window_is_unknown(
    tmp_path, monkeypatch
):
    """frontmost_audacity_window_name() returns None for an Accessibility refusal
    as well as for "no windows", so None must never be read as an answer."""
    original, variant = _two_projects(tmp_path)
    _stub_identity(
        monkeypatch, ["song", "song_G"], [str(original), str(variant)], frontmost=None
    )
    with pytest.raises(af.ProjectIdentityError):
        af.open_project_stem()


def test_open_project_stem_does_not_need_the_frontmost_window_when_unambiguous(
    tmp_path, monkeypatch
):
    """One candidate is already the answer -- a dialog sitting in front of the
    only open project must not turn a working export into a refusal."""
    proj = _make_project(tmp_path, "song_G")
    _stub_identity(
        monkeypatch, ["song_G"], [str(proj)], frontmost="Preferences: Devices"
    )
    assert af.open_project_stem() == "song_G"


def test_open_project_stem_same_stem_in_two_dirs_is_not_ambiguous(
    tmp_path, monkeypatch
):
    """Two same-stem projects give one answer, so there is nothing to refuse --
    only *differing* stems are ambiguous."""
    a = _make_project(tmp_path, "song", subdir="a")
    b = _make_project(tmp_path, "song", subdir="b")
    _stub_identity(monkeypatch, ["song"], [str(a), str(b)])
    assert af.open_project_stem() == "song"


def test_open_project_stem_ignores_recent_projects_that_are_not_open(
    tmp_path, monkeypatch
):
    open_one = _make_project(tmp_path, "song_G")
    closed = _make_project(tmp_path, "other", subdir="b")
    _stub_identity(monkeypatch, ["song_G"], [str(closed), str(open_one)])
    assert af.open_project_stem() == "song_G"


def test_open_project_stem_skips_recent_entries_gone_from_disk(tmp_path, monkeypatch):
    ghost = tmp_path / "gone" / "song_G.aup3"  # never created
    _stub_identity(monkeypatch, ["song_G"], [str(ghost)], wave_stem="song")
    assert af.open_project_stem() == "song"


def test_open_project_stem_falls_back_to_wave_stem_and_says_so(
    tmp_path, monkeypatch, capsys
):
    """An unsaved project has no .aup3 stem to find; keep exporting rather than
    blocking, but never do it silently -- the announced name is the whole point."""
    _stub_identity(monkeypatch, ["song"], [], wave_stem="song")
    assert af.open_project_stem() == "song"
    err = capsys.readouterr().err
    assert "song" in err


def test_open_project_stem_falls_back_when_the_menu_query_fails(monkeypatch, capsys):
    def boom(cmd):
        raise RuntimeError("pipe went away")

    monkeypatch.setattr(ap, "audacity_window_names", lambda: ["song"])
    monkeypatch.setattr(af.pa, "do", boom)
    monkeypatch.setattr(af, "open_project_wave_stem", lambda *a, **k: "song")
    assert af.open_project_stem() == "song"
    assert capsys.readouterr().err


def test_open_project_stem_falls_back_when_windows_cannot_be_listed(
    tmp_path, monkeypatch
):
    """audacity_window_names() returns [] for an Accessibility refusal as well as
    for "no windows", so an empty title list must not resolve to anything."""
    proj = _make_project(tmp_path, "song_G")
    _stub_identity(monkeypatch, [], [str(proj)], wave_stem="song")
    assert af.open_project_stem() == "song"


# --- _resolve_output_context stays cwd-only for the no-arg case ------------


def test_resolve_output_context_is_cwd_plus_the_projects_aup3_stem(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(af, "open_project_stem", lambda: "song")
    out_dir, stem = af._resolve_output_context(None)
    assert out_dir == tmp_path
    assert stem == "song"


def test_resolve_output_context_takes_an_already_resolved_stem(tmp_path, monkeypatch):
    """One command must not identify the project twice -- the caller resolved it,
    so nothing here may go asking again."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        af, "open_project_stem", lambda: pytest.fail("must not re-resolve")
    )
    assert af._resolve_output_context(None, "song_G") == (tmp_path, "song_G")


def test_exported_filenames_carry_the_aup3_stem_not_the_wave_track_name(
    tmp_path, monkeypatch
):
    """End to end over the naming path: the `_G` variant writes
    `chords_song_G.txt`, not over the original's `chords_song.txt`."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(af, "open_project_stem", lambda: "song_G")
    monkeypatch.setattr(af, "open_project_wave_stem", lambda *a, **k: "song")
    labels = '\n[ [1, [[1.35, 2.83, "C7"]]] ]\nBatchCommand finished: OK\n'
    tracks = (
        '\n[ { "name":"song", "kind":"wave" },\n'
        '  { "name":"chords", "kind":"label" } ]\nBatchCommand finished: OK\n'
    )
    monkeypatch.setattr(af, "get_label_track_indices", lambda: [1])
    monkeypatch.setattr(af.pa, "do", lambda cmd: labels if "Labels" in cmd else tracks)

    written = af.export_label_tracks_via_getinfo()

    assert [p.name for _n, p in written] == ["chords_song_G.txt"]
    assert not (tmp_path / "chords_song.txt").exists()


# --- no-arg CLI behaviour --------------------------------------------------


def test_no_arg_exports_and_reports_when_cwd_is_the_project(
    tmp_path, monkeypatch, capsys
):
    """cwd holds the project -> export into cwd and report unconditionally
    (not only under -v; the old silent run looked like nothing happened)."""
    import rebuildap

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_bytes(b"x")  # cwd IS the project dir
    _stub_open_project(monkeypatch, stem="song", selected=[1])
    monkeypatch.setattr(
        af,
        "export_selected_label_tracks_via_getinfo",
        lambda **k: [("chords", tmp_path / "chords_song.txt")],
    )

    rebuildap.rebuild(verbose=False)

    out = capsys.readouterr().out
    assert "chords" in out
    assert str(tmp_path / "chords_song.txt") in out


def test_no_arg_all_tracks_lists_every_track_when_cwd_is_the_project(
    tmp_path, monkeypatch, capsys
):
    """Nothing selected -> all label tracks export, each listed like a single one."""
    import rebuildap

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_bytes(b"x")
    _stub_open_project(monkeypatch, stem="song", selected=[])
    monkeypatch.setattr(
        af,
        "export_label_tracks_via_getinfo",
        lambda **k: [
            ("parts", tmp_path / "parts_song.txt"),
            ("chords", tmp_path / "chords_song.txt"),
        ],
    )

    rebuildap.rebuild(verbose=False)

    out = capsys.readouterr().out
    assert "parts" in out and "chords" in out
    assert str(tmp_path / "parts_song.txt") in out
    assert str(tmp_path / "chords_song.txt") in out


def test_no_arg_points_at_candidates_and_refuses_when_cwd_is_wrong(
    tmp_path, monkeypatch, capsys
):
    """cwd is not the project dir but Open Recent knows where it is -> list the
    candidate(s), tell the user to cd there, and export NOTHING."""
    import rebuildap

    monkeypatch.chdir(tmp_path)  # empty dir, no project markers
    candidate = tmp_path / "elsewhere" / "song"
    candidate.mkdir(parents=True)
    _stub_open_project(monkeypatch, stem="song", selected=[1])
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [candidate])
    _fail_if_exported(monkeypatch)

    rebuildap.rebuild(verbose=False)

    err = capsys.readouterr().err
    assert str(candidate) in err
    assert "cd" in err.lower()
    assert "-f" in err  # the refusal advertises the force escape hatch


def test_no_arg_refusal_lists_each_candidate_on_its_own_line(
    tmp_path, monkeypatch, capsys
):
    """Multiple same-stem candidates are each printed on their own indented line,
    not run together, so the user can read and pick one."""
    import rebuildap

    monkeypatch.chdir(tmp_path)  # empty dir, no project markers
    a = tmp_path / "here" / "song"
    b = tmp_path / "there" / "song"
    for d in (a, b):
        d.mkdir(parents=True)
    _stub_open_project(monkeypatch, stem="song", selected=[1])
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [a, b])
    _fail_if_exported(monkeypatch)

    rebuildap.rebuild(verbose=False)

    err = capsys.readouterr().err
    assert f"\n  {a}\n" in err
    assert f"\n  {b}\n" in err


def test_no_arg_force_exports_to_cwd_despite_candidates(tmp_path, monkeypatch, capsys):
    """-f overrides the refusal: export into cwd even though the project appears
    to live elsewhere."""
    import rebuildap

    monkeypatch.chdir(tmp_path)  # not the project dir
    candidate = tmp_path / "elsewhere" / "song"
    candidate.mkdir(parents=True)
    _stub_open_project(monkeypatch, stem="song", selected=[1])
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [candidate])
    exported = []
    monkeypatch.setattr(
        af,
        "export_selected_label_tracks_via_getinfo",
        lambda **k: exported.append("ran")
        or [("chords", tmp_path / "chords_song.txt")],
    )

    rebuildap.rebuild(verbose=False, force=True)

    captured = capsys.readouterr()
    assert exported == ["ran"], "must export into cwd under -f"
    assert "chords" in captured.out
    assert str(candidate) in captured.err  # the notice names where it lives


def _run_main(monkeypatch, argv):
    import sys as _sys

    import rebuildap

    captured = {}
    monkeypatch.setattr(
        rebuildap, "rebuild", lambda *a, **k: captured.update(args=a, kwargs=k)
    )
    monkeypatch.setattr(_sys, "argv", argv)
    rebuildap.main()
    return captured


@pytest.mark.parametrize(
    "argv",
    [
        ["rebuildap", "song.opus", "--force"],  # a rebuild has nothing to force
        ["rebuildap", "-l", "--force"],  # nor does a label import
    ],
)
def test_force_is_rejected_where_it_means_nothing(monkeypatch, argv):
    import sys as _sys

    import rebuildap

    monkeypatch.setattr(_sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        rebuildap.main()
    assert excinfo.value.code != 0


def test_force_with_check_requests_the_deep_comparison(monkeypatch):
    """-c -f replaced -c -d: it skips the mtime gate and compares every file.

    Previously `-c -f` was a usage error, so this combination flipping from
    rejected to meaningful is the whole point of the rename.
    """
    captured = _run_main(monkeypatch, ["rebuildap", "-c", "-f"])
    assert captured["kwargs"]["deep"] is True
    assert captured["args"][3] is True, "check is passed positionally"


def test_force_with_check_and_a_filename_is_allowed(monkeypatch):
    """-c takes a filename, so a filename must not disqualify -f the way it does
    for a rebuild."""
    captured = _run_main(monkeypatch, ["rebuildap", "-c", "song.aup3", "-f"])
    assert captured["kwargs"]["deep"] is True
    assert captured["args"][0] == "song.aup3"


def test_check_without_force_is_not_deep(monkeypatch):
    captured = _run_main(monkeypatch, ["rebuildap", "-c"])
    assert captured["kwargs"]["deep"] is False


def test_every_mode_that_accepts_force_documents_its_own_meaning(monkeypatch, capsys):
    """-f's help is an index, not an enumeration, so each mode must say what -f
    does *there*.

    The enumeration went stale the moment -t was added -- it kept listing only
    the export and -q senses. With the index form the risk inverts: a new mode
    could accept -f and document it nowhere. This pins the convention for the
    three that exist. See decisions.md 2026-07-25.
    """
    import sys as _sys

    import rebuildap

    monkeypatch.setattr(_sys, "argv", ["rebuildap", "--help"])
    with pytest.raises(SystemExit):
        rebuildap.main()
    help_text = capsys.readouterr().out

    force_entry = help_text.split("-f, --force")[1].split("-q, --quantize")[0]
    assert "depends on the mode" in force_entry, "-f must point, not enumerate"

    check_entry = help_text.split("-c, --check")[1].split("-n, --no-save")[0]
    quantize_entry = help_text.split("-q, --quantize")[1].split("-t, --transpose")[0]
    transpose_entry = help_text.split("-t, --transpose")[1].split("-s, --sharps")[0]
    epilog = help_text.split("Input modes:")[1]
    for name, entry in [
        ("-c", check_entry),
        ("-q", quantize_entry),
        ("-t", transpose_entry),
        ("the no-argument export (epilog)", epilog),
    ]:
        assert "-f" in entry, f"{name} accepts -f but does not document it"


def test_no_arg_exports_to_cwd_with_notice_when_no_candidates(
    tmp_path, monkeypatch, capsys
):
    """cwd is not the project dir and Open Recent has nothing to suggest -> say
    so, but export into cwd anyway (do not block the user)."""
    import rebuildap

    monkeypatch.chdir(tmp_path)  # empty dir, no markers
    _stub_open_project(monkeypatch, stem="song", selected=[1])
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [])
    exported = []
    monkeypatch.setattr(
        af,
        "export_selected_label_tracks_via_getinfo",
        lambda **k: exported.append("ran")
        or [("chords", tmp_path / "chords_song.txt")],
    )

    rebuildap.rebuild(verbose=False)

    captured = capsys.readouterr()
    assert exported == ["ran"], "must still export into cwd"
    assert "chords" in captured.out  # export was reported
    assert str(tmp_path).lower() in captured.err.lower()  # notice names cwd


# --- prerequisites_met -----------------------------------------------------


def _all_prerequisites(monkeypatch, running):
    """Make every prerequisite pass except is_audacity_running, set to `running`."""
    from rebuildap import audacity_present as ap

    monkeypatch.setattr(ap, "is_audacity_running", lambda: running)
    monkeypatch.setattr(ap, "is_audacity_window_open", lambda: True)
    monkeypatch.setattr(af, "is_project_empty", lambda: False)
    monkeypatch.setattr(af, "get_label_tracks", lambda: [{"kind": "label"}])


def test_prerequisites_not_met_when_audacity_not_running(monkeypatch):
    """Regression: is_audacity_running was called without (), so `not <function>`
    was always False and this guard never fired."""
    import rebuildap

    _all_prerequisites(monkeypatch, running=False)
    assert rebuildap.prerequisites_met() is False


def test_prerequisites_met_when_everything_present(monkeypatch):
    import rebuildap

    _all_prerequisites(monkeypatch, running=True)
    assert rebuildap.prerequisites_met() is True


# Every one of these used to be gated behind -v, so the whole command returned
# silently and looked broken -- the third time this project has hit that bug
# (see -c's "nothing to do", and the no-arg export's silent success).


@pytest.mark.parametrize(
    "failing, expected",
    [
        ("is_audacity_running", "not running"),
        ("is_audacity_window_open", "No Audacity window"),
        ("is_project_empty", "empty"),
        ("get_label_tracks", "no label tracks"),
    ],
)
def test_every_unmet_prerequisite_says_so_without_verbose(
    monkeypatch, capsys, failing, expected
):
    import rebuildap
    from rebuildap import audacity_present as ap

    _all_prerequisites(monkeypatch, running=True)
    monkeypatch.setattr(af, "_open_project_aup3_stems", lambda: [])
    # is_project_empty is the one guard that fails by returning True.
    target = ap if failing.startswith("is_audacity") else af
    monkeypatch.setattr(target, failing, lambda: failing == "is_project_empty")

    assert rebuildap.prerequisites_met() is False
    assert expected in capsys.readouterr().err


def test_an_empty_frontmost_project_points_at_the_other_open_projects(
    monkeypatch, capsys
):
    """The case that surfaced this: two real projects open behind a scratch empty
    one. Saying "project empty" without naming what it looked at is a riddle."""
    import rebuildap

    _all_prerequisites(monkeypatch, running=True)
    monkeypatch.setattr(af, "is_project_empty", lambda: True)
    monkeypatch.setattr(af, "_open_project_aup3_stems", lambda: ["song", "song_G"])

    assert rebuildap.prerequisites_met() is False

    err = capsys.readouterr().err
    assert "frontmost" in err
    assert f"{af.LIST_BULLET}song" in err.splitlines()
    assert f"{af.LIST_BULLET}song_G" in err.splitlines()


def test_no_other_projects_means_no_pointless_hint(monkeypatch, capsys):
    """One empty window and nothing else open -- there is nowhere to point."""
    import rebuildap

    _all_prerequisites(monkeypatch, running=True)
    monkeypatch.setattr(af, "is_project_empty", lambda: True)
    monkeypatch.setattr(af, "_open_project_aup3_stems", lambda: [])

    assert rebuildap.prerequisites_met() is False
    assert "bring the one you mean" not in capsys.readouterr().err.lower()
