import json
from secrets import randbits
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from dojo.catalog import OpenRouterCatalog
from dojo.config import DEFAULT_DEMO_ROUNDS, MAX_ROUNDS, Settings, agent_limits
from dojo.launcher import RunBusy
from dojo.level import LEVELS, available_levels, load_level
from dojo.publication import MAX_COMMENT_LENGTH, delete_recording, publish, recording_directory, recording_lists, save_comment
from dojo.runner import Agent, HARNESS_VERSION
from dojo.storage import RUN_ID, frames
from dojo.wire import decode

WEB = Path(__file__).resolve().parent.parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/agent-sprites.js": ("agent-sprites.js", "text/javascript; charset=utf-8"),
    "/teams.js": ("teams.js", "text/javascript; charset=utf-8"),
    "/squad.js": ("squad.js", "text/javascript; charset=utf-8"),
}


def serve(root, port, launcher=None, *, autoplay_latest=False, public_root=None):
    public_root = public_root if public_root is not None else WEB.parent / "recordings"
    if root.resolve() == public_root.resolve():
        raise ValueError("Use a separate runs directory for private recordings.")
    settings = launcher.settings if launcher else Settings()
    preview_seed = settings.seed if settings.seed is not None else randbits(48)
    recording_updates = Lock()
    model_catalog = OpenRouterCatalog()
    levels = []
    for name in available_levels():
        level = load_level(name)
        minimum, maximum = agent_limits(level)
        levels.append({
            "id": name, "title": level.TITLE, "version": level.VERSION,
            "demo": hasattr(level, "demo"),
            "model_rounds": getattr(level, "DEFAULT_MODEL_ROUNDS", 60),
            "min_agents": minimum, "max_agents": maximum, "default_agents": level.DEFAULT_AGENTS,
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
            publishing = self.path == "/api/publish"
            deleting = self.path == "/api/delete-recording"
            commenting = self.path == "/api/comment"
            updating_recording = publishing or deleting or commenting
            collection, _, action = self.path.removeprefix("/api/").partition("/")
            updating_profile = self.path.startswith("/api/") and collection in ("profiles", "teams") and action in ("save", "delete")
            if not launcher or (not (updating_recording or updating_profile) and self.path not in ("/api/launch", "/api/stop", "/api/pause", "/api/resume", "/api/continue")):
                self.send_error(404)
                return
            if not self.valid_host() or self.headers.get("Origin") != "http://" + self.headers["Host"]:
                self.send_error(403)
                return
            if updating_profile:
                self.update_profile(getattr(launcher, collection), action,
                                    "model" if collection == "profiles" else "team")
                return
            launching = self.path == "/api/launch"
            fields = {"level", "mode"} if launching else {"id", "destination"} if publishing else {"id"}
            if commenting:
                fields.add("comment")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                max_length = 12 * MAX_COMMENT_LENGTH + 256 if commenting else 1024
                if self.headers.get_content_type() != "application/json" or not 0 < length <= max_length:
                    raise ValueError
                data = decode(self.rfile.read(length))
                optional = {"rounds"} if launching or self.path in ("/api/resume", "/api/continue") else set()
                if launching:
                    optional.update(("profile_id", "team_id", "squad", "team_revision"))
                if publishing:
                    optional = {"enabled"}
                if (not isinstance(data, dict) or not fields <= data.keys() <= (fields | optional)
                        or not all(isinstance(data[key], str) for key in fields)):
                    raise ValueError
                if not launching and not RUN_ID.fullmatch(data["id"]):
                    raise ValueError
                if any(key in data and not isinstance(data[key], str) for key in ("profile_id", "team_id", "team_revision")):
                    raise ValueError
                if "squad" in data and not isinstance(data["squad"], list):
                    raise ValueError
                if publishing and type(data.get("enabled")) is not bool:
                    raise ValueError
            except (ValueError, RecursionError):
                error = "Expected a puzzle and run mode." if launching else "Expected a run ID."
                self.send_json({"error": error}, 400)
                return
            try:
                if publishing:
                    run_id = data["id"]
                    with recording_updates:
                        publish(root, public_root, run_id, data["destination"], data["enabled"])
                elif deleting:
                    run_id = data["id"]
                    with recording_updates:
                        delete_recording(root, public_root, run_id)
                elif commenting:
                    run_id = data["id"]
                    with recording_updates:
                        comment = save_comment(root, public_root, run_id, data["comment"])
                elif launching:
                    run_id = launcher.launch(data["level"], data["mode"], rounds=data.get("rounds"),
                                             profile_id=data.get("profile_id"), team_id=data.get("team_id"),
                                             squad=data.get("squad"), team_revision=data.get("team_revision"))
                else:
                    run_id = data["id"]
                    if self.path == "/api/pause":
                        launcher.pause_run(run_id)
                    elif self.path == "/api/resume":
                        launcher.resume_run(run_id, rounds=data.get("rounds"))
                    elif self.path == "/api/continue":
                        launcher.start_saved(run_id, rounds=data.get("rounds"))
                    else:
                        launcher.stop_run(run_id)
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
            except (RunBusy, BlockingIOError) as error:
                self.send_json({"error": str(error)}, 409)
            except OSError:
                error = "Could not update the recording." if updating_recording else "Could not create the run recording."
                self.send_json({"error": error}, 500)
            else:
                response = {"id": run_id}
                if commenting:
                    response["comment"] = comment
                if not launching and not updating_recording:
                    response["run_state"] = launcher.run_state()
                self.send_json(response, 202)

        def update_profile(self, store, action, kind):
            if store is None:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < length <= 8192:
                    raise ValueError(f"Expected {kind} profile settings.")
                try:
                    data = decode(self.rfile.read(length))
                except (ValueError, RecursionError) as error:
                    raise ValueError(f"Expected {kind} profile settings.") from error
                if action == "save":
                    result = store.save(data)
                else:
                    if not isinstance(data, dict) or data.keys() != {"id"}:
                        raise ValueError(f"Choose a {kind} profile.")
                    store.delete(data["id"])
                    result = {"id": data["id"]}
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
            except BlockingIOError as error:
                self.send_json({"error": str(error)}, 409)
            except OSError:
                self.send_json({"error": f"Could not save {kind} profiles."}, 500)
            else:
                self.send_json(result)

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
                state = launcher.run_state() if launcher else {"active_run": None, "stopping": False}
                model = launcher.model if launcher else None
                self.send_json({
                    "levels": levels, "harness_version": HARNESS_VERSION,
                    "can_launch": launcher is not None,
                    "can_demo": bool(launcher and launcher.allow_demo),
                    "max_comment_length": MAX_COMMENT_LENGTH,
                    "autoplay_latest": autoplay_latest,
                    "default_level": settings.level,
                    "can_pause": bool(launcher and launcher.interactive),
                    "can_manage_profiles": bool(launcher and launcher.profiles is not None),
                    "can_manage_teams": bool(launcher and launcher.teams is not None),
                    "max_rounds": MAX_ROUNDS,
                    "model": model,
                    "round_defaults": {"demo": DEFAULT_DEMO_ROUNDS,
                                       "model": launcher.settings.rounds if model else None},
                    **state,
                })
                return
            if url.path == "/api/profiles" and launcher and launcher.profiles is not None:
                try:
                    self.send_json(launcher.profiles.list())
                except OSError:
                    self.send_json({"error": "Could not load model profiles."}, 500)
                return
            if url.path == "/api/openrouter-models" and launcher and launcher.profiles is not None:
                try:
                    self.send_json(model_catalog.list(refresh=url.query == "refresh=1"))
                except OSError:
                    self.send_json({"error": "Could not load the OpenRouter catalog."}, 502)
                return
            if url.path == "/api/teams" and launcher and launcher.teams is not None:
                try:
                    self.send_json(launcher.teams.list())
                except OSError:
                    self.send_json({"error": "Could not load team profiles."}, 500)
                return
            if url.path in ("/api/runs", "/api/recordings"):
                lists = recording_lists(root, public_root)
                self.send_json(lists if url.path == "/api/recordings" else lists["local"])
                return
            if url.path == "/api/preview":
                name = parse_qs(url.query).get("level", [""])[0]
                try:
                    level = load_level(name)
                    setup = settings.for_level(name)
                except ValueError as error:
                    self.send_json({"error": str(error)}, 400)
                    return
                agents = [str(number) for number in range(1, setup.agents + 1)]
                puzzle = level.Puzzle(preview_seed, agents)
                won, score = puzzle.evaluate()
                self.send_json({"seed": preview_seed, "round": 0, "world": puzzle.snapshot(),
                                "score": score, "won": won, "elapsed_seconds": 0,
                                "agents": [Agent(id, []).snapshot() for id in agents],
                                "event": {"kind": "preview"}})
                return
            if len(parts) == 3 and parts[0] == "levels" and parts[2] == "viewer.js":
                if parts[1].isascii() and parts[1].replace("_", "").isalnum():
                    path = LEVELS / parts[1] / "viewer.js"
                    if path.is_file():
                        self.send(path.read_bytes(), "text/javascript; charset=utf-8")
                        return
            if (len(parts) == 4 and parts[0] == "api" and parts[1] in ("runs", "public") and parts[-1] == "frames"):
                try:
                    directory = recording_directory(public_root if parts[1] == "public" else root, parts[2])
                    if (directory / "frames.jsonl").is_symlink():
                        raise ValueError
                    offset = int(parse_qs(url.query).get("after", ["0"])[0])
                    if not 0 <= offset < 2**53:
                        raise ValueError
                    batch, next_offset = frames(directory, offset)
                except (ValueError, OSError):
                    self.send_error(400, "Invalid frame offset")
                    return
                self.send_json({"frames": batch, "next_offset": next_offset})
                return
            self.send_error(404)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
