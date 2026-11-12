import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dojo.launcher import RunBusy
from dojo.level import LEVELS, available_levels, load_level
from dojo.runner import HARNESS_VERSION
from dojo.storage import RUN_ID, frames, summaries
from dojo.wire import decode

WEB = Path(__file__).resolve().parent.parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def serve(root, port, launcher=None):
    levels = []
    for name in available_levels():
        level = load_level(name)
        levels.append({
            "id": name, "title": level.TITLE, "version": level.VERSION,
            "demo": hasattr(level, "demo"),
        })

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_json(self, value, status=200):
            self.send(json.dumps(value, separators=(",", ":")).encode(), "application/json", status)

        def valid_host(self):
            return self.headers.get("Host") in (
                f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"
            )

        def do_POST(self):
            if self.path != "/api/launch" or not launcher:
                self.send_error(404)
                return
            if not self.valid_host() or self.headers.get("Origin") != "http://" + self.headers["Host"]:
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < length <= 1024:
                    raise ValueError
                data = decode(self.rfile.read(length))
                if (not isinstance(data, dict) or data.keys() != {"level", "mode"}
                        or not all(isinstance(value, str) for value in data.values())):
                    raise ValueError
            except (ValueError, RecursionError):
                self.send_json({"error": "Expected a puzzle and run mode."}, 400)
                return
            try:
                run_id = launcher.launch(data["level"], data["mode"])
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
            except RunBusy as error:
                self.send_json({"error": str(error)}, 409)
            except OSError:
                self.send_json({"error": "Could not create the run recording."}, 500)
            else:
                self.send_json({"id": run_id}, 202)

        def do_GET(self):
            if not self.valid_host():
                self.send_error(403)
                return
            url = urlsplit(self.path)
            if url.path in STATIC:
                name, content_type = STATIC[url.path]
                self.send((WEB / name).read_bytes(), content_type)
                return
            parts = url.path.strip("/").split("/")
            if url.path == "/api/levels":
                self.send_json({
                    "levels": levels, "harness_version": HARNESS_VERSION,
                    "can_launch": launcher is not None,
                    "model": launcher.model if launcher else None,
                    "active_run": launcher.active_run if launcher else None,
                })
                return
            if url.path == "/api/runs":
                self.send_json(summaries(root))
                return
            if len(parts) == 3 and parts[0] == "levels" and parts[2] == "viewer.js":
                if parts[1].isascii() and parts[1].replace("_", "").isalnum():
                    path = LEVELS / parts[1] / "viewer.js"
                    if path.is_file():
                        self.send(path.read_bytes(), "text/javascript; charset=utf-8")
                        return
            if (len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[-1] == "frames"
                    and RUN_ID.fullmatch(parts[2]) and (root / parts[2]).is_dir()):
                try:
                    offset = int(parse_qs(url.query).get("after", ["0"])[0])
                    if not 0 <= offset < 2**53:
                        raise ValueError
                    batch, next_offset = frames(root / parts[2], offset)
                except (ValueError, OSError):
                    self.send_error(400, "Invalid frame offset")
                    return
                self.send_json({"frames": batch, "next_offset": next_offset})
                return
            self.send_error(404)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
