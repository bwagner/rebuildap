# Exporting label tracks

Why `rebuildap` exports labels the way it does, and what it costs.
Back to the [README](../README.md).

## Why saving touches label mtimes

After a rebuild, `rebuildap` moves the label files' mtimes up to match the newly
saved `.aup3` (forward only, never backwards, and their *content* is untouched).

Without that, `-c` would treat every label file as older than the project and so
re-export and diff it on every run, forever. Check mode only rewrites a label
file when the content actually diverges, so the condition would never clear on
its own. The rebuild is proof that the project and the label files agree — it was
built from those very files — so recording that agreement in the mtimes is
accurate rather than a fudge.

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
