# rebuildap

Rebuilds Audacity `.aup3` projects from an audio file plus versioned plain-text
label files, so the large binary project doesn't have to live in git.

## Invariants

These shape most decisions here and are not obvious from the code.

- **The label `.txt` files are the source of truth.** They are what's versioned.
- **The `.aup3` is disposable** - regenerable from audio + labels at any time.
  But an *existing* one is the user's working copy and may hold edits the label
  files don't have, so it is **never overwritten**. (`pa.save` unlinks its target
  before writing, so an accidental overwrite is unrecoverable.)
- **macOS only.** Audacity is driven partly through GUI keystrokes via
  AppleScript, so it needs a real, unlocked login session with Accessibility
  permission granted to the calling terminal. There is no headless mode.
- **Audacity is a hostile dependency.** It crashes on documented paths, and its
  scripting pipe can silently swallow commands. Much of this codebase is
  workarounds, each one deliberate. Before "simplifying" any of them, read
  `docs/audacity-quirks.md`, `~/.claude/audacity.md` and the project memory -
  several obvious-looking cleanups have been tried and reverted.

## Rules with teeth

- **Never send a bare Cmd-W.** It closes whatever window is *frontmost*, which
  may be the user's own project - discarding unsaved work and raising a
  `Save changes?` dialog that then wedges the scripting pipe. Always go through
  `audacity_present.close_owned_window`, which confirms ownership first and
  refuses when it can't. Refusing to close is always preferable to a wrong close.
- **Never close an unsaved project.** Same `Save changes?` trap. The rebuild path
  closes its window only after a successful save.
- **Never click the `Error Opening Project` alert.** Audacity exited immediately
  the one time "`<name>` is already open in another window." was dismissed with
  an osascript click, no crash report, cause unknown. Unlike the format-upgrade
  dialog this one is preventable - `project_window_open` sees the condition
  before the command is sent - so `open_project` refuses with
  `ProjectAlreadyOpenError` instead. Do not "unify" the dialogs behind one
  watcher; each is matched by its own static text.
- **Never exit the process while a modal is open.** This is the rule the others
  were approximating. Audacity died five times on 2026-07-29 and the cause was
  *not* the dismissal: every death had the client already gone, closing the FIFO
  with the dialog still up. Measured the same day, with the process kept alive,
  both a hand click and a programmatic one leave Audacity running and the pipe
  fully usable. So a bare `except TimeoutError: raise` on a pipe call is itself
  the hazard - clear the dialog first, then return.
- **Exactly one modal is clicked deliberately**: NyquistPrompt's
  `requires one or more tracks to be selected` refusal, via
  `audacity_present.dismiss_no_region_dialog`, because that refusal is the only
  way to learn there is no time region (every read channel was measured and is
  closed - see `docs/audacity-quirks.md`). It is matched on its own static text,
  never "whatever is frontmost". The `already open in another window` alert stays
  on the never-click list: its client state was never measured.
- **No liveness pre-check on the scripting pipe.** Opening the write end and
  closing it reads to Audacity as a client hanging up: it tears down the session
  and reopens the FIFOs, breaking the very round-trip it was meant to protect.
  This was tried (`_is_script_pipe_listening`) and removed.
- **Order matters at startup**: process -> project window -> pipe readiness. The
  pipe cannot answer before a project window exists, so probing earlier deadlocks.

## Working here

- `uv run pytest` - the offline suite. Tests marked `audacity` drive a real
  Audacity and are deselected by default; `uv run pytest -m audacity` runs them
  and needs a GUI login session plus Accessibility. They had never run before
  2026-07-18, so treat failures there as un-vetted, not as regressions.
  `uv run ruff check --fix . && uv run ruff format .` before committing;
  pre-commit runs both anyway.
- `uv run rebuildap ...` runs the **working tree**. The `rebuildap` on PATH is a
  separate installed copy and can be silently stale - see `~/.claude/uv.md`.
- Timing constants in `audacity_present.py` were measured, not guessed (window at
  ~2.7s after Cmd-N, pipe answering at ~3.4s on 3.7.8). **Measure before changing
  any timeout or retry ladder** - a previous recovery path was designed against
  assumed timings and closed healthy windows.
- Verify against copies, never the user's `cover-notes-gitlab/batch01` projects:
  a test that drives Audacity can edit, save, or crash a project. Note it is
  *editing* an aup3 - even an edit you then undo - that bumps its mtime, because
  Audacity writes edits to the file before you Save (Audacity issue #9161).
  Merely *opening and closing* one does not, and neither does a read-only
  `GetInfo` (the deep-check path, `check -f`): confirmed 2026-07-21, both an
  open+close and an open -> `GetInfo` -> close left the mtime byte-identical.

## Project memory

Backlog and design decisions with rationale live at the top level of
`$(~/.claude/bin/claudemeta-dir)`; retrospectives are in its `memory/`
subdirectory. Resolve that path by running the script - never build it by hand.
`decisions.md` explains *why* several non-obvious choices were made - read it
before reversing one.
