# prusa-workbench-mcp

Ask Claude to model something (Onshape) and slice it. Claude drives the PrusaSlicer **command line** through an
MCP server — one run per bed, each bed with its own settings — writes the G-code to a job folder, and a small local
viewer app shows the result so you can check it and send it to the printer (Prusa Connect / PrusaLink).

Built for an Original Prusa MINI, but nothing ties it to one printer.

**Status:** step 2 of [PLAN.md](PLAN.md): CLI slicing works, viewer v1 works, MCP server and sending are next.

## Viewer

```sh
python3 -m prusa_workbench.viewer --open        # http://127.0.0.1:8765, reads ~/print-jobs
```

Python 3.11+, no dependencies (three.js loads from a CDN, so the browser needs internet).
Options: `--jobs DIR`, `--port N`, `--job NAME`.

- Shows the newest job and follows new ones as they appear; pick an older job in the **Job** menu.
- One tab per bed (`*.gcode` in the job folder). Re-sliced beds reload automatically.
- Toolpaths in PrusaSlicer's colors; click legend items to hide features, tick **Travel** to see travel moves.
- Keys: `↑/↓` layer, `←/→` move within the layer (`Shift` = ×10), `S` single layer, `1/2/3` iso/top/front, `Z` zoom to print.
- Side panel: print time, filament, profiles, settings used (● = changed from the profile), objects, warnings.

A job folder is just `~/print-jobs/<job>/bed-N.gcode` (+ optional `bed-N.json` summary). G-code dropped in by hand works too.

## Slicing test

```sh
python3 scripts/cli_test.py     # slices the robocars fit test on 2 beds with different settings
```

Needs PrusaSlicer as a Flatpak (`com.prusa3d.PrusaSlicer`). See "CLI findings" in PLAN.md.

## License

[MIT](LICENSE)
