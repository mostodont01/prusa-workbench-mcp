#!/usr/bin/env python3
"""Smoke test without Claude: slice the robocars fit test on two beds with different settings, in parallel.

    .venv/bin/python scripts/cli_test.py      -> a new job in ~/print-jobs, shown by the viewer
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prusa_workbench import config, mesh  # noqa: E402
from prusa_workbench.job import Job  # noqa: E402

MODELS = Path.home() / "Programming/robots/robocars/modelling/fit_test"


def main() -> None:
    sel = config.selected_presets()
    job = Job.create("fit-test", sel["printer"], "0.20mm SPEED @MINIIS 0.4", sel["filament"])
    for name, m in mesh.load(MODELS / "fit_plate.stl", job.dir / "models" / "src"):
        job.add_mesh(name, m, str(MODELS / "fit_plate.stl"), "bed-1")
    bed2 = job.new_bed()
    bed2.print_profile = "0.15mm STRUCTURAL @MINIIS 0.4"
    bed2.settings = {"perimeters": "4", "fill_density": "40%"}
    for d in (3, 5, 8):
        f = MODELS / f"pin_{d}_mm.stl"
        for name, m in mesh.load(f, job.dir / "models" / "src"):
            job.add_mesh(name, m, str(f), bed2.name)
    for b in job.beds:
        job.arrange(b.name)
    job.save()

    t0 = time.monotonic()
    results = job.slice()
    print(f"job: {job.dir}  (wall {time.monotonic() - t0:.1f}s)")
    for r in results:
        if r.get("error"):
            print(f"  {r['bed']}: FAILED  {r['error']}")
        else:
            s = r["settings"]
            print(f"  {r['bed']}: {r['print_time']}, {r['filament_g']} g, {r['layers']} layers | "
                  f"{s.get('print_settings_id')} | layer {s.get('layer_height')} perim {s.get('perimeters')} "
                  f"infill {s.get('fill_density')} | {r['objects']}")


if __name__ == "__main__":
    main()
