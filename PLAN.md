# PrusaSlicer-mcp — plan

Goal: ask Claude to model something (Onshape) and slice it. Claude slices it with PrusaSlicer run from the command line,
the result shows up in our own small viewer app, I check it, and I send it to the Prusa Mini.

## Why not just PrusaSlicer?

PrusaSlicer is fine for working by hand, but it has some annoyances, mainly that **multiple beds can't have different
settings**. Here, PrusaSlicer is only the slicing engine, run from the command line, **one run per bed, each with its
own settings** (and several can run at the same time). The normal PrusaSlicer window stays for when I model or slice by hand.

## Architecture

```
me ──► Claude ──► MCP server ──► PrusaSlicer CLI (one run per bed, own settings)
                                      │
                                      ▼
                    ~/print-jobs/<job>/bed-1.gcode + bed-1.json (settings, time, filament, warnings)
                                       bed-2.gcode + ...
                                      │
                                      ▼
                     viewer app ──► Prusa Connect / PrusaLink ──► Prusa Mini
```

The MCP server and the viewer only share the **job folder**, so they stay independent. G-code can be dropped
into the folder by hand too.

## Components

### 1. Slicing engine
- PrusaSlicer 2.9.6, installed as a Flatpak: `flatpak run com.prusa3d.PrusaSlicer` (the command-line interface is the same binary).
- Uses my own profiles from `~/.var/app/com.prusa3d.PrusaSlicer/config/PrusaSlicer` (Original Prusa MINI).
- Each bed is its own run with its own settings file; runs can go in parallel.
- Output: plain-text `.gcode` (easy to read and parse; the Mini accepts it).

### 2. MCP server (for Claude)
- Load models from STL/3MF/STEP files, or download them straight from Onshape.
- Move, rotate, scale and lay flat.
- Arrange, and move objects between beds.
- Supports: on/off, auto or touching the build plate only, overhang angle, per object.
- Settings per bed: layer height, infill, perimeters, brim, filament…
- Slice → write the job folder → report time, filament and warnings → open the viewer.
- Never starts a print. Sending is my decision, done in the viewer.

### 3. Viewer app
- Local web app (small Python server + three.js in the browser; maybe its own window later).
- Reads the G-code and colors the toolpaths by type using PrusaSlicer's feature labels (`;TYPE:` comments: perimeter, infill, support…).
- Rotate/zoom the bed, a layer slider, **tabs to switch between beds**.
- A side panel per bed: settings used, print time, filament, warnings.
- **Send** button → Prusa Connect (or PrusaLink).
- Performance: large prints mean millions of line segments, so merge them into one geometry buffer per bed.

### 4. Sending to the printer
- **Prusa Connect** (preferred): **no official public upload API**. `gioelemo/prusa-mcp` shows it can be done
  by logging in with the account login PrusaSlicer itself uses (OAuth2 + PKCE with PrusaSlicer's client id). It works but could break.
- **PrusaLink**: the printer's own local HTTP API, **officially documented** (upload + optional start).
  Reliable, but only on my home network.
- Plan: support both.

## Limits
- Hand-painted supports, seams and modifier regions are hard to do from the command line → use the PrusaSlicer window for those rare cases.

## Prior art (reviewed 2026-10-31; ideas only)
- [JorgeAlbareda/PrusaSlicer-MCP](https://github.com/JorgeAlbareda/PrusaSlicer-MCP): Python, **AGPL**, closest in spirit. Session of models on the bed, arrange,
  slicing with a check of the settings used, G-code risk analysis, PrusaLink upload. Only tested on Windows, very new. (Copying code would make our project AGPL too.)
- [MaxEllis/orcaslicer-mcp](https://github.com/MaxEllis/orcaslicer-mcp): most capable, but drives a patched OrcaSlicer build. Good ideas: per-feature
  time breakdown, comparing several slice variants, plate pictures.
- [Noosbai/PrusaMCP](https://github.com/Noosbai/PrusaMCP): mesh analysis, orientation scoring, profile recommendations.
- [gioelemo/prusa-mcp](https://github.com/gioelemo/prusa-mcp): Prusa Connect login + upload, the reference for the Connect sender.

## Steps
1. **Command-line test**: slice the fit test from robocars
   (`~/Programming/robots/robocars/modelling/fit_test/*.stl`) through Flatpak with my MINI profile,
   2 beds with different settings. Confirm the profiles load and the output is correct.
   ✅ Done 2026-10-31, CLI only (no window): `scripts/cli_test.py`. See "CLI findings" below.
2. **Viewer v1**: load a job folder, 3D view, bed tabs, layer slider, info panel.
3. **MCP server**: models, transforms, arrange, beds, supports, settings, slicing → job folder.
4. **Sending**: PrusaLink first, then Prusa Connect.

## Open questions
- ~~Project and app name.~~ → **prusa-workbench-mcp** (decided 2026-10-31).
- ~~Language~~ → Python for the server and the viewer backend (decided 2026-10-31).
- Where job folders live: `~/print-jobs` for now (it must be under `$HOME`, see below).

## CLI findings (step 1, PrusaSlicer 2.9.6 Flatpak)
- Command: `flatpak run --command=prusa-slicer com.prusa3d.PrusaSlicer --printer-profile "Original Prusa MINI & MINI+ Input Shaper"
  --material-profile "Alzament Basic PLA" --print-profile "0.20mm SPEED @MINIIS 0.4" --no-binary-gcode --dont-arrange --export-gcode -o bed-1.gcode bed-1.3mf`.
  System presets load by name from the normal config folder; no `--datadir` needed. Any setting can be overridden: `--perimeters 4 --fill-density 40%`.
- `--query-print-filament-profiles --printer-profile …` lists the print profiles and their compatible filaments as JSON (good for the MCP server).
- **The Flatpak sandbox sees `$HOME` but not `/tmp`**: inputs and outputs must be under home.
- Binary G-code is turned off with `--no-binary-gcode` (on/off switch, `--binary-gcode 0` is wrong).
- **`--merge` (CLI auto-arrange) segfaults** (exit 139) with 2+ objects. So we arrange in Python and write a 3MF with
  the placements, sliced with `--dont-arrange`. Add `Metadata/Slic3r_PE_model.config` or a single object is named after the file.
- **Objects off the bed → no G-code but exit code 0** ("All objects are outside of the print volume"). Success = the output file exists.
- Two beds in parallel: ~8 s total. The G-code has `;TYPE:` / `;LAYER_CHANGE`, and the footer has time, filament, `objects_info`
  (names + outlines) and the full settings dump, which is everything the job's `bed-N.json` and the viewer need.
