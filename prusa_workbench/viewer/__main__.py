"""python -m prusa_workbench.viewer [--jobs ~/print-jobs] [--port 8765] [--open] [--job NAME]"""
from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path
from urllib.parse import quote

from .server import serve


def main() -> None:
    ap = argparse.ArgumentParser(prog="prusa_workbench.viewer", description="G-code viewer for print-job folders")
    ap.add_argument("--jobs", type=Path, default=Path.home() / "print-jobs", help="jobs folder (default ~/print-jobs)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the viewer in the browser")
    ap.add_argument("--job", help="job to show when opening (default: follow the newest job)")
    args = ap.parse_args()

    httpd = serve(args.jobs.expanduser(), args.host, args.port)
    url = f"http://{args.host}:{args.port}/" + (f"?job={quote(args.job)}" if args.job else "")
    print(f"viewer: {url}  (jobs: {args.jobs})")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
