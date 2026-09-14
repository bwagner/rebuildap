# Changing label tracks in place: quantize and transpose

`quantize` and `transpose` edit a label track inside the open Audacity project and
update its versioned `.txt` in the same run, so the project and the source of truth
never drift apart. This page covers what they act on, how they decide, and when they
refuse. Back to the [README](../README.md).

## What both commands have in common

- **They act on the frontmost project.** The scripting pipe targets the project window
  in front, so with several projects open, bring the one you mean to the front first.
  How the project is identified is in
  [Exporting label tracks](label-export.md#which-project-the-exported-files-are-named-after).
- **Exactly one label track must be selected.** It is the track that gets rewritten.
  None or several selected is a refusal, because the swap removes whatever is selected.
- **The track keeps its place.** The new content is imported as a fresh track, the old
  one removed, and the new one moved back to the row the old one held and reselected.
- **File and track come from the same content.** The result is written to
  `<track>_<stem>.txt` and imported from those same labels, so the two are identical
  by construction; nothing is read back from Audacity.
- **Nothing changes, nothing is touched.** If the result equals the track's current
  content, the project is left byte-identical (no mtime bump, no undo entry), and the
  `.txt` is rewritten only when it differs from what the file already holds.
- **The `.txt` is written immediately.** Undoing in Audacity afterwards reverts the
  track, not the file. That is what git is for, and `check` names label files that are
  not committed.

## The time selection decides the scope

Both commands work on the **whole track** unless a time region is selected:

| Selection | Scope |
|---|---|
| a time region | only the part of the track inside it |
| no region, or a bare cursor | the whole track |
| any, with `-f` | the whole track, and the selection is not even read |

Clicking a label track's header selects exactly the span from its first label's start
to its last label's end. Clicking inside the track past its last label selects the
track with no region, which means the whole track.

The two commands draw the line differently, because they change different things:

- **`quantize` scopes per boundary.** A label start or end is snapped when its
  original position lies inside the region. A label straddling the region's edge gets
  only its inside boundary snapped.
- **`transpose` scopes per label, by its start.** A chord belongs to its onset, so a
  label whose start lies inside the region is transposed whole, and one starting
  before it is left alone even if it reaches into it.

Labels sitting exactly on the region's edges count as inside. Audacity rounds label
times and the selection independently, so an exact comparison would drop them; see
[Two roundings of the same instant never quite agree](audacity-quirks.md#two-roundings-of-the-same-instant-never-quite-agree).
Why "no region" needs no special gesture - Audacity refuses to report a selection
that does not exist, and rebuildap takes the refusal as the answer - is in
[No time region: the refusal is the answer](audacity-quirks.md#no-time-region-the-refusal-is-the-answer).

## quantize: which beats track is the grid

`rebuildap quantize [BEATS_TRACK]` snaps the selected track onto the grid of a beats
label track in the same project. The snapping itself is done by
[quantize_labels](https://github.com/bwagner/quantize_labels), which must be on your
`$PATH`.

The reference track is chosen in this order:

1. **A name on the command line** wins: `rebuildap quantize beats_half`. It must match a
   label track's name exactly.
2. Otherwise, the label tracks whose name starts with `beat` (case-insensitive) are
   candidates. **A sole candidate** is used wherever it sits.
3. **Several candidates:** the **nearest one below the selected track** is used, and
   rebuildap says so on stderr:

   ```
   Using beats track 'beats', the nearest below 'chords' (passed over: beats_half).
   ```

4. Several candidates and **none below** the selected track, or no candidate at all, or
   the selected track being the only candidate: it refuses.

The idea behind rule 3: beats tracks are a quantizing technicality, so they live under
the tracks you actually practice with. Stacked at the bottom, their order is the
precedence - drag the one you want to the top of that stack. This is also the only way
to choose from a keyboard shortcut, which cannot pass a name. Because the quantized
track is put back in its own row, repeated runs keep choosing the same reference.

Every outcome names the grid it used, chosen or not:

```
Quantized label track 'chords' to 'beats':
  /path/to/project/chords_<stem>.txt
```

## transpose: spelling and text that is not a chord

`rebuildap transpose SEMITONES` transposes the chords in the selected track by
`SEMITONES` half steps; a negative number goes down. Label times are never touched.

- **Flats by default**, `-s` / `--sharps` for sharps. Every outcome names the spelling
  it used. So `transpose 0` is not a no-op on a sharp-spelled track: it respells it
  with flats.
- **Text that is not a chord is left exactly as it is, and reported** - section markers
  like `Guitar Solo`, `N.C.`, lyric cues. Up to eight are named, then a count of the
  rest. Empty labels are left alone silently.
- **Hyphen-joined sequences** such as `A-B-C#` are left alone and reported too: a hyphen
  cannot separate chords without breaking minor notation like `F#-7`, which *is*
  transposed.
- It is **cumulative**: running it twice transposes twice. `quantize` running twice
  changes nothing the second time.

## Where the .txt is written

The label file goes into **the project's own directory**, wherever you run the command
from:

1. **The current directory**, if it holds the project - its `<stem>.aup3` or any
   `*_<stem>.txt`. It is checked first, so running from a directory that holds a
   same-named project writes there, even when the project open in Audacity is a copy
   somewhere else.
2. Otherwise, the directory of the `<stem>.aup3` in Audacity's **Open Recent** menu.
3. When Open Recent lists **several** directories with that project name - a copy on
   another disk, say - the one whose `.aup3` Audacity **currently has open** is used.
   Audacity keeps an open project's database file open, which names its exact path
   ([how that was measured](audacity-quirks.md#what-window-titles-can-and-cannot-tell-you)).

The directory is settled **before** the project is touched, so a location that cannot
be determined is a clean refusal rather than a changed track with no file to show for
it.

## When they refuse

A refusal changes nothing, explains itself, and **exits with status 1** - so a keyboard
shortcut reports it as failed, not "ok". They refuse when:

- **Several projects are open** and the frontmost window is not one of them (a dialog in
  front, say).
- **Not exactly one label track** is selected.
- **`quantize` cannot settle on a beats track** (see the rules above).
- **The project's directory is unknown** (not in Open Recent) or **still ambiguous**:
  several copies with the same name in Open Recent and none, or more than one, of them
  open.
- **The selection cannot be read.** The message points at `-f`, which skips the read.

One exception exits with status 0: when there is nothing to work on at all - Audacity
not running, no window, an empty project, or a project without label tracks. That is
reported on stderr as "nothing to export".
