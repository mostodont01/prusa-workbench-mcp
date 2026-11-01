# prusa-workbench-mcp

Ask Claude to model something (Onshape) and slice it. Claude drives the PrusaSlicer **command line** through an
MCP server — one run per bed, each bed with its own settings — writes the G-code to a job folder, and a small local
viewer app shows the result so you can check it and send it to the printer (Prusa Connect / PrusaLink).

Built for an Original Prusa MINI, but nothing ties it to one printer.

**Status:** steps 1–3 of [PLAN.md](PLAN.md) done (CLI slicing, viewer, MCP server); sending through Prusa Connect
is built and being tested, PrusaLink comes after.

## Setup

Linux, Python 3.11+, PrusaSlicer as a Flatpak (`com.prusa3d.PrusaSlicer`) with your printer set up in it.

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
claude mcp add -s user prusa-workbench -- "$PWD/.venv/bin/prusa-workbench-mcp"
```

## MCP server

Then just ask Claude, e.g. *"slice the fit test on two beds, the pins at 0.15 mm with 4 perimeters"*.
It never starts a print: the result opens in the viewer and you send it from there.

| Tool | What it does |
|---|---|
| `new_job` / `open_job` / `list_jobs` / `get_job` | jobs live in `~/print-jobs/<date>-<name>/` (`job.json` + models), so work can be resumed |
| `delete_jobs` | delete whole jobs (to the desktop trash) |
| `list_profiles` / `find_setting` | print profiles + filaments; search PrusaSlicer setting names with their current values |
| `add_model` | STL / 3MF / OBJ / STEP (STEP through PrusaSlicer), with copies |
| `add_from_onshape` | parts straight from an Onshape part studio (API keys from `~/onshape-mcp/.env`) |
| `transform` | rotate, scale, lay flat (largest stable face), move |
| `arrange` / `move_to_bed` / `copy_objects` / `remove_objects` | packing per bed; what doesn't fit goes to a new bed |
| `set_bed` / `remove_bed` | per bed: print profile, filament, any PrusaSlicer setting, supports |
| `set_object_settings` | per object: supports (off / auto / build plate only), angle, infill, perimeters, brim… |
| `paint_supports` | paint support blockers/enforcers like PrusaSlicer's brush: inside a box, or automatically in small gaps (screw holes, nut traps) |
| `preview` | picture of a bed (top + iso, labeled) so Claude can check the layout before slicing |
| `slice` | all beds in parallel, each with its own settings → G-code with thumbnails → viewer |
| `open_viewer` | start / open the viewer |

Each bed is sliced from `bed-N.3mf` (placed objects + per-object settings) and `bed-N.ini` (bed overrides on top
of the profiles); both stay in the job folder next to `bed-N.gcode` and `bed-N.json`.

## Viewer

```sh
python3 -m prusa_workbench.viewer --open        # http://127.0.0.1:8765, reads ~/print-jobs
```

**As an app** (its own window and app-menu entry; Firefox on Linux can't install PWAs, so this uses GTK + WebKit):

```sh
PYTHONPATH=$PWD /usr/bin/python3 -m prusa_workbench.app_window --install     # once; --uninstall removes it
```

Then start **Prusa Workbench** from the app menu. It starts the viewer server if needed. Every time Claude slices,
this window opens (or comes to the front) and shows the sliced job; `PRUSA_WORKBENCH_OPEN=browser` uses the
browser instead.

The window has two tabs, **Workbench** and **Prusa Connect** (Ctrl+1 / Ctrl+2; back, reload and "open in browser"
in the title bar). Log in to Connect there once; the login is kept in `~/.local/share/prusa-workbench/`. After a
successful send it switches to the printer's page in Connect, and when Claude slices it switches back. Needs the system `python3` with
`gir1.2-webkit2-4.1` (same as the Prusa login window).

Stdlib only (three.js loads from a CDN, so the browser needs internet). The MCP server starts it by itself.
Options: `--jobs DIR`, `--port N`, `--job NAME`.

- Shows the newest job and follows new ones as they appear; pick an older job in the **Job** menu.
- **Delete…** next to it cleans up old or bad jobs (several at once). They go to the desktop trash, so they can
  be restored.
- One tab per bed (`*.gcode` in the job folder). Re-sliced beds reload automatically.
- Toolpaths in PrusaSlicer's colors; click legend items to hide features, tick **Travel** to see travel moves.
- Keys: `↑/↓` layer, `←/→` move within the layer (`Shift` = ×10), `S` single layer, `1/2/3` iso/top/front, `Z` zoom to print.
- **Send to printer** uploads through Prusa Connect (see below): all beds of the job in order (or just the shown
  bed), then just upload, add to the queue, or print now (first in the queue, bed-1 then bed-2…; printing starts
  once the printer is set READY, i.e. someone confirmed the bed is clear).
- Side panel: thumbnail, print time, filament, profiles, settings used (● = changed from the profile), objects with
  their own settings, warnings.

A job folder is just `~/print-jobs/<job>/bed-N.gcode` (+ optional `bed-N.json` summary). G-code dropped in by hand works too.

## Prusa Connect

Prusa Connect has no official upload API, so this does what PrusaSlicer does: log in to your Prusa Account with
PrusaSlicer's public OAuth client, then register the upload and PUT the file. It may break if Prusa changes things.

- **Log in** from the Send dialog: a small sign-in window opens (GTK + WebKit, run with the system `python3`;
  needs `gir1.2-webkit2-4.1`). It catches the `prusaslicer://login` redirect itself, so PrusaSlicer's own login is
  not touched.
- It sends **binary G-code** (`.bgcode`, about a third of the size) when the printer takes it: PrusaSlicer makes
  it from the bed's saved inputs at send time, with our thumbnails added. The MINI's Wi-Fi is the slow part.
- Tokens are kept in `~/.config/prusa-workbench/connect-tokens.json` (mode 600). **Log out** in the dialog deletes them.
- The viewer only accepts these requests from `localhost` pages (custom header + Host check).

## Tests

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                    # ~20 s; includes real PrusaSlicer runs
.venv/bin/pytest -m "not slicer"    # ~2 s, no PrusaSlicer
```

The `slicer` tests check the PrusaSlicer behavior everything relies on (bed `.ini` overrides, per-object settings,
painted supports, "no G-code but exit 0", the `.bgcode` layout), so run them after a PrusaSlicer update.

## Smoke test without Claude

```sh
.venv/bin/python scripts/cli_test.py     # the robocars fit test on 2 beds with different settings
```

## License

[MIT](LICENSE)
