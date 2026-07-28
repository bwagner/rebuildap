--- rebuildap.lua - drive `rebuildap` from Hammerspoon hotkeys.
---
--- `quantize` and `transpose` act on the selected label track of the *frontmost*
--- Audacity project, so they suit a global shortcut: the scripting pipe targets
--- the window already in front of you. See docs/hotkeys.md for the reasoning,
--- including why `export` is deliberately not offered here (it writes into the
--- current directory, which a hotkey does not meaningfully have).
---
--- Usage in ~/.hammerspoon/init.lua:
---
---     package.path = package.path .. ";/path/to/rebuildap/contrib/hammerspoon/?.lua"
---     require("rebuildap"):bindHotkeys({
---       quantize  = {{"cmd", "control", "shift"}, "Q"},
---       transpose = {{"cmd", "control", "shift"}, "T"},
---     })
---
--- Optional configuration, before bindHotkeys:
---
---     require("rebuildap").setup({
---       binary      = "/opt/homebrew/bin/rebuildap",  -- default: auto-detected
---       pathEntries = {os.getenv("HOME") .. "/tools"}, -- prepended to PATH
---       logFile     = false,                           -- disable the run log
---       notify      = false,                           -- alerts only, no notifications
---     })

local M = {}

local HOME = os.getenv("HOME")

-- A GUI-launched process inherits almost no environment, so PATH is built here
-- rather than taken from the shell. It must contain the directory holding
-- `quantize_labels`, which `rebuildap quantize` locates on PATH; ~/bin is the
-- usual home for it. A login shell would supply all this, but it also sources
-- the user's profile, and anything the profile prints would land in the captured
-- output and then in the notification.
local DEFAULT_PATH_ENTRIES = {
  HOME .. "/bin",
  HOME .. "/.local/bin",
  "/usr/local/bin",
  "/opt/homebrew/bin",
}
local SYSTEM_PATH_ENTRIES = {"/usr/bin", "/bin", "/usr/sbin", "/sbin"}

-- hs.task does not search PATH for the executable itself, so this must be absolute.
local BINARY_CANDIDATES = {
  HOME .. "/.local/bin/rebuildap",
  "/opt/homebrew/bin/rebuildap",
  "/usr/local/bin/rebuildap",
}

-- Audacity refocus delay after the chooser closes, before rebuildap runs. The
-- chooser takes keyboard focus, and the pipe acts on the frontmost project window.
local REFOCUS_DELAY = 0.3
local NOTIFY_WITHDRAW_AFTER = 20
local CHOOSER_ROWS = 8

M.binary = nil
M.pathEntries = nil
M.logFile = HOME .. "/.hammerspoon/rebuildap.log"
M.notify = true

--- Override any of `binary`, `pathEntries`, `logFile`, `notify`.
function M.setup(opts)
  for k, v in pairs(opts or {}) do M[k] = v end
  return M
end

--- The rebuildap that will actually be run: `binary` if set, else the first
--- candidate that exists. Public so `hs -c 'print(require("rebuildap").resolveBinary())'`
--- can answer "which copy is this using?" - which matters, because a
--- `uv tool install`ed rebuildap is a separate copy from the working tree and
--- can be silently stale.
function M.resolveBinary()
  if M.binary then return M.binary end
  for _, candidate in ipairs(BINARY_CANDIDATES) do
    if hs.fs.attributes(candidate, "mode") == "file" then return candidate end
  end
  return nil
end

local function environment()
  local entries = {}
  for _, dir in ipairs(M.pathEntries or DEFAULT_PATH_ENTRIES) do
    entries[#entries + 1] = dir
  end
  for _, dir in ipairs(SYSTEM_PATH_ENTRIES) do entries[#entries + 1] = dir end
  return {PATH = table.concat(entries, ":"), HOME = HOME}
end

local function report(title, text)
  text = (text or ""):gsub("^%s+", ""):gsub("%s+$", "")
  if text == "" then text = "(no output)" end
  if M.notify then
    hs.notify.new({title = title, informativeText = text,
                   withdrawAfter = NOTIFY_WITHDRAW_AFTER}):send()
  end
  print("[rebuildap] " .. title .. "\n" .. text)
  if M.logFile then
    local fh = io.open(M.logFile, "a")
    if fh then
      fh:write(os.date("%Y-%m-%d %H:%M:%S") .. "  " .. title .. "\n" .. text .. "\n\n")
      fh:close()
    end
  end
end

--- Which app is in front? Deliberately NOT hs.application.frontmostApplication():
--- measured 2026-07-28 on macOS 26.3.1, it returned "loginwindow" on an unlocked,
--- on-console session with Accessibility granted, while :isFrontmost(),
--- hs.window.focusedWindow() and System Events all named the real frontmost app.
local function audacityFrontmost()
  local app = hs.application.get("Audacity")
  if not app then return false, "Audacity is not running" end
  if app:isFrontmost() then return true end
  local w = hs.window.focusedWindow()
  local other = (w and w:application() and w:application():name()) or "(unknown)"
  return false, "frontmost is " .. other
end

--- Run rebuildap with NO frontmost check. For callers that already checked, or
--- that lost focus to a chooser in between.
function M.exec(args)
  local binary = M.resolveBinary()
  if not binary then
    hs.alert.show("rebuildap: executable not found - set binary via setup()")
    return false
  end
  local label = table.concat(args, " ")
  hs.alert.show("rebuildap " .. label .. " ...")
  local task = hs.task.new(binary, function(rc, stdout, stderr)
    local body = (stdout or "") .. (stderr or "")
    if rc == 0 then
      report("rebuildap " .. label .. " - ok", body)
    else
      report("rebuildap " .. label .. " - failed (exit " .. tostring(rc) .. ")", body)
    end
  end, args)
  task:setEnvironment(environment())
  task:start()
  return true
end

--- Run rebuildap only when Audacity is frontmost.
function M.run(args)
  local ok, why = audacityFrontmost()
  if not ok then
    hs.alert.show("rebuildap: " .. why)
    return false
  end
  return M.exec(args)
end

-- --- transpose: pick the interval -------------------------------------------
-- transpose carries two knobs (SEMITONES and -s/--sharps against a flats
-- default), so a key-per-interval scheme would need twice the bindings. It is
-- also cumulative: firing it twice transposes twice, with no error and an
-- identical-looking notification. The chooser's selection step is the
-- confirmation that non-idempotence deserves.

local INTERVALS = {
  "minor 2nd", "major 2nd", "minor 3rd", "major 3rd", "perfect 4th", "tritone",
  "perfect 5th", "minor 6th", "major 6th", "minor 7th", "major 7th", "octave",
}

--- The argv a chooser row becomes. Separate so it can be unit-checked.
function M.transposeArgs(choice)
  local args = {"transpose", tostring(choice.semitones)}
  if choice.sharps then table.insert(args, "-s") end
  return args
end

function M.transposeChoices()
  local rows = {}
  for n = 1, #INTERVALS do
    for _, dir in ipairs({1, -1}) do
      for _, spelling in ipairs({{"flats", "Bb", false}, {"sharps", "A#", true}}) do
        local name = INTERVALS[n]
        rows[#rows + 1] = {
          text = string.format("%+d  %s %s %s", n * dir,
                               dir > 0 and "up" or "down",
                               name:match("^[aeiou]") and "an" or "a", name),
          subText = "spell with " .. spelling[1] .. " (" .. spelling[2] .. ")",
          semitones = n * dir,
          sharps = spelling[3],
        }
      end
    end
  end
  return rows
end

local transposeChooser = nil

local function showTransposeChooser()
  if not transposeChooser then
    transposeChooser = hs.chooser.new(function(choice)
      if not choice then return end  -- dismissed with Esc: nothing runs
      local app = hs.application.get("Audacity")
      if not app then
        hs.alert.show("rebuildap: Audacity is not running")
        return
      end
      app:activate()
      hs.timer.doAfter(REFOCUS_DELAY, function()
        M.exec(M.transposeArgs(choice))
      end)
    end)
    transposeChooser:choices(M.transposeChoices())
    transposeChooser:searchSubText(true)
    transposeChooser:rows(CHOOSER_ROWS)
  end
  transposeChooser:query(nil)
  transposeChooser:show()
end

--- mapping: {quantize = {mods, key}, transpose = {mods, key}}; both optional.
function M:bindHotkeys(mapping)
  if mapping.quantize then
    hs.hotkey.bind(mapping.quantize[1], mapping.quantize[2], function()
      M.run({"quantize"})
    end)
  end
  if mapping.transpose then
    hs.hotkey.bind(mapping.transpose[1], mapping.transpose[2], function()
      -- Check BEFORE showing the chooser: once it is up, Hammerspoon is
      -- frontmost and the check would always fail.
      local ok, why = audacityFrontmost()
      if not ok then
        hs.alert.show("rebuildap: " .. why)
        return
      end
      showTransposeChooser()
    end)
  end
  return self
end

return M
