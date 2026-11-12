#!/usr/bin/env python3
"""Run a benchmark and serve the local spectator view."""
import argparse
import os
from pathlib import Path

from dojo.config import Settings
from dojo.launcher import Launcher
from dojo.level import available_levels
from dojo.viewer import serve

PROJECT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="configuration from ./configure")
    parser.add_argument("--level", choices=available_levels(), help="choose the puzzle for the initial run")
    parser.add_argument("--demo", action="store_true", help="use the offline demo with these settings")
    parser.add_argument("--runs-dir", type=Path, default=PROJECT / "runs")
    parser.add_argument("--port", type=int, default=8765)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true", help="open the viewer without starting a run")
    mode.add_argument("--no-viewer", action="store_true", help="run once and exit")
    args = parser.parse_args()
    default = PROJECT / "bench.json"
    path = args.config or (default if default.is_file() else None)
    server = launcher = None

    try:
        settings = Settings.read(path) if path else Settings()
        launcher = Launcher(settings, args.runs_dir, os.environ.get("OPENROUTER_API_KEY"))
        args.runs_dir.mkdir(parents=True, exist_ok=True)
        if not args.no_viewer:
            server = serve(args.runs_dir, args.port, launcher)
            print(f"Spectator view at http://127.0.0.1:{server.server_port} · Ctrl+C to stop",
                  flush=True)
        if not args.serve:
            overrides = {"model": "demo"} if args.demo else {}
            launcher.start(settings.for_level(args.level or settings.level, **overrides))
        if server:
            server.serve_forever()
        else:
            while launcher.active_run:
                launcher.wait(0.25)
    except (ValueError, OSError, OverflowError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        if launcher:
            launcher.stop.set()
            if launcher.active_run:
                print("\nFinishing any in-flight requests and saving the run…", flush=True)
                launcher.wait()
        return 130
    finally:
        if server:
            server.server_close()
    return launcher.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
