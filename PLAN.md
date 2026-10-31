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
   ✅ Done 2026-10-31: `python3 -m prusa_workbench.viewer`. See "Viewer v1 notes" below.
3. **MCP server**: models, transforms, arrange, beds, supports, settings, slicing → job folder.
   ✅ Done 2026-10-31: `prusa_workbench/mcp_server.py`, 19 tools. See "MCP server notes" below.
4. **Sending**: ~~PrusaLink first, then Prusa Connect~~ → Prusa Connect first (the user prefers it; it's on Wi-Fi anyway).
   Built 2026-11-01: `connect.py`, `connect_login.py`, Send dialog in the viewer. Login + upload verified by the user.
   Then: binary G-code for sending, "add to queue" / "print now", thumbnails with the bed under the objects.

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

## Viewer v1 notes
- Python stdlib server (`prusa_workbench/viewer/server.py`) + one static page with three.js 0.170 from jsDelivr.
  API: `/api/ping`, `/api/jobs`, `/api/jobs/<job>`, `/files/<job>/<bed>.gcode`. The page polls every 2 s.
- G-code is parsed in a Web Worker (G1 + G2/G3 arcs, `;TYPE:`, `;WIDTH:`, `;HEIGHT:`, `;LAYER_CHANGE`/`;Z:`).
  Every extrusion is one instance of a unit box stretched in the vertex shader, so one bed = one draw call.
  Layers/types/move position are uniforms; draw count stops at the slider, so the layers above it cost nothing.
- Measured: 13.6 MB / 480k moves (2 plates, 0.10 mm, 100 % infill) loads in ~1.9 s in headless Chromium.
- Failed beds (only `bed-N.json` with `"error"`) appear as a "failed" tab with the error.
- Later: hover a toolpath for info, per-object highlight (`M486` labels are in the G-code).
  (Offline three.js not needed: Prusa Connect needs the network anyway.)

## MCP server notes
- Python, official `mcp` SDK 2.x (`MCPServer`, formerly FastMCP), trimesh/numpy/scipy for meshes, Pillow for pictures.
- A job = folder with `job.json` (beds, objects, 3x3 rotation/scale + footprint position, settings) and `models/<id>.stl`.
- Per-bed settings: `bed-N.ini` passed with `--load` after the profiles (verified: overrides them).
  Per-object settings: `<metadata type="object" key=… value=…/>` in the 3MF's `Slic3r_PE_model.config`
  (verified: supports only on the chosen object, brim only around it).
- Setting names are checked against `--help-fff` (411 options); typos get suggestions.
- Bed size, height and thumbnail sizes come from `--save` of the profile combination (MINI: 180x180x180,
  `16x16/QOI, 220x124/QOI, 200x240/QOI, 380x285/PNG`).
- **Thumbnails**: rendered by our own small renderer (Pillow, painter's algorithm), objects standing on a cropped
  piece of the bed like PrusaSlicer's, inserted into the G-code header the way PrusaSlicer does; the biggest one is
  also saved as `bed-N.png` for the viewer.
- Arrange: MaxRects on footprint rectangles, 90° turns allowed, group centered; overflow → new bed with the same settings.
  Issues reported per bed: off the bed, too tall, overlapping footprints (convex hulls).
- Meshes must be re-merged after reading STL (`merge_vertices`), otherwise PrusaSlicer gets a triangle soup
  and slices differently / fails ("no extrusions in the first layer").
- Tool errors are re-raised as `ToolError` so Claude sees the message (the SDK hides other exceptions' text).
- Onshape: verified with the fit test document (4 parts, mm). STEP loading goes through `prusa-slicer --export-stl`
  but is **untested** (no STEP file at hand).

## Prusa Connect notes
- Login: Prusa Account OAuth2 + PKCE, PrusaSlicer's public client id `oamhmhZez7opFosnwzElIgE2oGgI2iJORSkw587O`,
  redirect `prusaslicer://login`, scope `basic_info` (from gioelemo/prusa-mcp, GPL: facts only, no code copied).
  `prusaslicer://` is registered to the PrusaSlicer Flatpak here, so a normal browser would hand the code to
  PrusaSlicer. Our window (GTK3 + WebKit2 4.1 from the system python; the venv has no `gi`) ignores that navigation
  in `decide-policy` and exchanges the code itself. Ephemeral WebKit context (no cookies kept).
- Upload (from PrusaSlicer's `PrintHostPrusaConnect.cpp`): `POST /app/users/teams/<team>/uploads`
  `{filename, size, printer_uuid}` → `{id, path, state}`, then `PUT /app/teams/<team>/files/raw?upload_id=<id>`
  with `Content-Type: text/x.gcode`. Team id from the printer list / `/app/printers/<uuid>`.
- Queue / print now (from BogdanStamenovic/prusa-connect, MIT): `POST /app/printers/<uuid>/queue`
  `{hash, team_id, position}` with the hash from the upload. "Print now" = position 0: Connect starts it once the
  file is on the printer **and the printer is READY** (a person pressed "Set ready" = bed clear). We never set READY.
  "Add to queue" = position = current queue length (`GET /app/printers/<uuid>/queue` → `pager.total`).
- **Upload speed**: the Connect API answers in ~0.1 s; the slow part is Connect copying the file to the MINI over
  its Wi-Fi. So we send **binary G-code** (what PrusaSlicer sends to a MINI: the profile has `binary_gcode = 1`),
  about 1/3 of the text size (3.9 MB → 1.3 MB). Made at send time by slicing `bed-N.3mf` + `bed-N.ini` again with
  `--binary-gcode` (~3–6 s, cached as `bed-N.bgcode`), then our thumbnails are added as thumbnail blocks
  (`bgcode.py`; block layout and CRC32-over-header+params+data checked on PrusaSlicer's own output, 17/17).
  Prusa's converter (pybgcode) isn't on PyPI, and writing the firmware-facing compression ourselves is riskier.
- The printer record also contains its PrusaLink/Connect API keys: `connect.printers()` keeps only the fields we use.
- Python's default User-Agent got a 403 from account.prusa3d.com; requests send an honest
  `prusa-workbench/0.1 (+repo URL)`. Whether that is accepted: see the first real login.
- The viewer's POST endpoints need `X-Workbench: 1` and a local Host header (CSRF / DNS rebinding).

