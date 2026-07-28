# Exporting label tracks

Why `rebuildap` exports labels the way it does, and what it costs.
Back to the [README](../README.md).

## Which project the exported files are named after

A label file is named `<track>_<stem>.txt`, and `<stem>` is the open project's
**`.aup3` filename stem** - not the name of its wave track.

The two are usually the same, because a project rebuilt from `song.opus` is
saved as `song.aup3` and its wave track is called `song`. They come apart as
soon as a project is copied: Save-As on `song.aup3` produces `song_G.aup3` whose
wave track is *still* called `song`. This is the normal way to keep a transposed
variant beside its original, and naming by the wave track made the variant write
its labels straight over the original's files.

Audacity has no query for "which file is this project?" - `AXDocument` is
`missing value` and there is no `GetInfo: Type=Project` (see
[Audacity quirks](audacity-quirks.md)). So `open_project_stem` intersects the
two partial routes:

- **window titles** say which projects are *open*, but carry no path, and an
  unsaved project is titled with the stem of the audio it was built from;
- **Open Recent** gives real, full paths, but is a recency list - it holds
  closed projects and can evict open ones.

A name that is both an open window and a file on disk is the project. Two
same-stem projects in different directories collapse to one answer, which is
correct here: the stem is the same either way.

When that yields **several differently-named projects**, the **frontmost** one
wins. That is not a guess: mod-script-pipe acts on the frontmost project window,
measured on 3.7.8 (2026-07-28) with two projects open and a uniquely named label
track in each - the tracks reported by `GetInfo` followed the window focus. So
the project rebuildap names its files after is the same project the commands
themselves touch.

It refuses only when that tiebreaker cannot be applied: something other than a
project is frontmost (a modal dialog, the About box), or the front window cannot
be read at all - `frontmost_audacity_window_name()` returns `None` for an
Accessibility refusal as well as for "no windows", so `None` is never taken as an
answer. Bring the project you mean to the front and run again.

A single candidate is returned without consulting the frontmost window at all,
so a dialog sitting in front of the only open project cannot turn a working
export into a refusal.

One residual: the tie is broken at the moment identity is resolved, and nothing
stops you switching windows mid-run. It is a seconds-wide gap, and every mode
prints the full path it wrote, so a mis-targeted run is visible in its own
output rather than silent.

When it yields **nothing** - a never-saved project, or one evicted from Open
Recent - it falls back to the wave track's name and says so on stderr. A
never-saved project has no `.aup3` stem to find, and refusing to export it would
be worse than naming it after its audio; announcing it is what keeps the
fallback from going unnoticed, as the old unconditional behaviour did.

This applies to every mode that acts on whatever project is open: the no-argument
export, `-q` and `-t`. Passing an `.aup3` explicitly (`-c song_G.aup3`) has never
had the problem - the stem comes from the path you gave.

## Why saving touches label mtimes

After a rebuild, `rebuildap` moves the label files' mtimes up to match the newly
saved `.aup3` (forward only, never backwards, and their *content* is untouched).

Without that, `-c` would treat every label file as older than the project and so
re-export and diff it on every run, forever. Check mode only rewrites a label
file when the content actually diverges, so the condition would never clear on
its own. The rebuild is proof that the project and the label files agree — it was
built from those very files — so recording that agreement in the mtimes is
accurate rather than a fudge.

## What the mtime gate decides — and what it doesn't

The same mtimes drive `-c`, in the other direction. An Audacity edit always bumps
the `.aup3` to newest (see
[When an .aup3 changes on disk](audacity-quirks.md#when-an-aup3-changes-on-disk)),
so if *every* label file is newer than the project, no edit can have outrun them
and there is nothing an open could reveal:

```console
All label files are newer than song.aup3. Nothing to do.
```

That is the gate's whole job, and it is worth having: it skips a 2-3 second
project open, a GUI window, and one more interaction with a crash-prone
dependency — per project, across a sweep of many.

**The gate is a whole-project decision, not a per-file filter.** Once the project
is open, *every* label file is compared, newer ones included. It used to compare
only the older ones, which was close to free to change and quietly wrong:

- `GetInfo` fetches every label track in a single call, so a track is already in
  memory whether or not its file passes any filter.
- Comparing one more file costs about **0.2 ms** (measured over 654 real label
  files), against 2-3 s for the open it cannot avoid. The per-file filter saved
  nothing measurable.
- It hid tracks. `-q` and `-t` rewrite a label file *after* reading the project,
  so the file they just wrote is newer than the `.aup3` — and a `-c` run would
  list three tracks while silently omitting the fourth, indistinguishable from a
  project that only has three.

`-c -f` skips the gate: open and compare even when every label file is newer.
That is for when mtimes lie across the board — a `git checkout`, a `touch`, a
restore — leaving files newer than the project while their content has diverged.

Comparing a newer file cannot damage it. A divergence writes the *export
artifact* `<track>.txt`, never the versioned `<track>_<stem>.txt`, so a `.txt`
that `-q` or `-t` just wrote is never overwritten by a check.

(`-c -f` was spelled `-c -d` until 2026-07-25. It became `-f` to join the other
modes' "ignore the narrowing rule, do the whole thing" flag — the same `-f` that
exports into the current directory anyway, or acts on the whole track rather than
the time selection.)

## Two ways to export label tracks

Audacity doesn't support exporting label tracks selectively: when exporting via
File > Export Other > Export Labels…, all label tracks get concatenated into a
single file. There's
a [workaround](https://forum.audacityteam.org/t/export-individual-label-when-multiple-labels-in-project/58799/32)
— temporarily remove all but one label track, export, then undo.

The downside of the workaround is that it requires the user to click through
the save dialog once per label track. Audacity's scripting pipe (`GetInfo:
Type=Labels`) offers a non-interactive alternative, at the cost of some
[precision](https://github.com/audacity/audacity/issues/4220).

Empirical comparison (on a project with beat-quantized labels plus one
manually-placed sub-beat label `C7#9`):

| source                                  | start       | end         |
|-----------------------------------------|-------------|-------------|
| interactive `ExportLabels:` (dialog)    | `125.437458`| `126.671804`|
| `GetInfo: Type=Labels` (non-interactive)| `125.437`   | `126.672`   |

`GetInfo` truncates to **3 decimals** while the interactive export preserves
**6 decimals**. Maximum rounding error: ~0.5 ms, i.e. ~22 samples @ 44.1 kHz —
inaudible but not sample-accurate.

For beat-quantized labels (the common case, e.g. output of
[`DBNDownBeatTracker`](https://github.com/CPJKU/madmom/blob/main/bin/DBNDownBeatTracker)
rounded to 2 decimals) both paths yield identical files.

`rebuildap` uses the non-interactive `GetInfo` path exclusively. The interactive
dialog path was offered behind `-p` / `--precise` until 2026-07-18 and has been
retired: `ExportLabels:` takes **no parameters** (see the
[scripting reference](https://manual.audacityteam.org/man/scripting_reference.html#:~:text=Description-,ExportLabels%3A,-Export%20Labels)),
so the save dialog alone decided where each artifact landed and the caller had
to guess that location afterwards — it read them back from the current working
directory, which was wrong whenever `rebuildap` was invoked from elsewhere.
Combined with one dialog click per label track, the ~0.5 ms precision edge did
not justify keeping a second export path alive.

A [Nyquist](https://manual.audacityteam.org/man/nyquist.html) plug-in route
was investigated and rejected: Nyquist-side label access also goes through
`aud-get-info` (see Steve Daulton's `ExportAllLabelTracks1.ny` for reference),
so a custom Nyquist plug-in would have the **same 3-decimal precision floor**
as our direct `GetInfo` path — no advantage.

## Importing labels: normalizing the input format

Import runs through the Nyquist plug-in
[`ImportLabels.ny`](https://audionyq.com/wp-content/uploads/2022/09/ImportLabels.ny),
which requires each line to have **exactly three tab-separated fields** (two
tabs), `start<TAB>end<TAB>text`, with the text possibly empty. It builds a Lisp
expression per line and only opens the string quote after the *second* tab, so a
line with fewer tabs produces malformed Lisp and the whole import fails with a
bare `BatchCommand finished: Failed!` — no indication of which file or line.

Real label files in the corpus come in three shapes, and two of them are not
directly importable:

| source                                | shape                     | example        |
|---------------------------------------|---------------------------|----------------|
| raw beat times                        | 1 column                  | `0.470`        |
| `DBNDownBeatTracker single`           | 2 columns, `time<TAB>num` | `0.470  4`     |
| Audacity's own export                 | 3 columns                 | `0.47  0.47  ` |

`rebuildap` normalizes every line to the three-field canonical form in a
throwaway temp file and imports **that**; the versioned source is never touched.
The same normalizer (`rebuildap.utils.normalize_label_line`) is what check mode
compares both sides through, so a 1-column source and its 3-column re-export
compare equal — the round trip is a fixed point, not a permanent divergence.

**A 2-column line is always a point label whose text is field 2 — rule A —**
even when field 2 is numeric (`0.470  4` -> `0.47  0.47  4`). The only 2-column
source here is `DBNDownBeatTracker`, whose second column is a **downbeat number**
(1..4), i.e. label text, not an end time. Reading it as `start<TAB>end` yields
backwards regions (`1.12  1` -> a region ending *before* it starts). No genuine
`start<TAB>end` region source exists — the exporter always writes three fields —
so nothing is lost. See `decisions.md` (2026-07-21).

Two subtleties that the normalizer must respect:

- **Only the line terminator is stripped, never the field tabs.** Audacity
  writes an empty-text label as `start<TAB>end<TAB>` (trailing tab); collapsing
  that to two fields would make rule A misread the end time as text and break the
  check-mode round trip for every 1-column project.
- **Only times are trailing-zero-trimmed** (`0.470` -> `0.47`); the text field is
  taken verbatim (`3.500` stays `3.500`).

A line that matches none of the three shapes (a stray header, a blank-but-junk
line) is rejected **before Audacity is started**, with a `LabelFormatError`
naming the file and line number — the same fail-before-launch stance as the
already-open and aup3-exists guards, so a bad file costs ~0.07 s and leaks no
window.
