import json
from threading import Event, Lock, Thread

from dojo.config import BACKEND_KEYS, Settings
from dojo.gemini import Gemini
from dojo.models import OpenRouter
from dojo.runner import Run, RunBusy
from dojo.storage import RUN_ID, Record


class Launcher:
    def __init__(self, settings, root, key, *, interactive=False, allow_demo=False):
        self.settings = settings
        self.root = root
        self.keys = {settings.backend: key}
        self.interactive = interactive
        self.allow_demo = allow_demo
        self.lock = Lock()
        self.closing = False
        self.worker = None
        self.run = None
        self.exit_code = 0

    @property
    def model(self):
        if self.keys.get(self.settings.backend) and self.settings.model != "demo":
            return self.settings.model
        return None

    @property
    def active_run(self):
        return self.run_state()["active_run"]

    def run_state(self):
        with self.lock:
            if self.run and self.worker.is_alive():
                return {"active_run": self.run.record.id, **self.run.control_state()}
            return {"active_run": None, "stopping": False}

    def _active_run(self, run_id):
        if not self.run or not self.worker.is_alive() or self.run.record.id != run_id:
            raise RunBusy("That run is no longer active.")
        return self.run

    def stop_run(self, run_id):
        with self.lock:
            self._active_run(run_id).interrupt()

    def pause_run(self, run_id):
        with self.lock:
            self._active_run(run_id).pause()

    def resume_run(self, run_id, rounds=None):
        with self.lock:
            self._active_run(run_id).resume(rounds)

    def shutdown(self):
        with self.lock:
            self.closing = True
            if self.run:
                self.run.interrupt()

    def launch(self, level, mode, rounds=None):
        if mode == "demo":
            settings = self.settings.for_level(level, model="demo", rounds=rounds)
        elif mode == "model" and self.model:
            settings = self.settings.for_level(level, rounds=self.settings.rounds if rounds is None else rounds)
        else:
            raise ValueError("Choose an available run mode.")
        return self.start(settings)

    def start(self, settings):
        with self.lock:
            self._check_idle()
            stop = Event()
            backend = self._backend(settings, stop)
            return self._start(Run(settings, self.root, stop, backend, interactive=self.interactive))

    def start_saved(self, run_id, rounds=None):
        with self.lock:
            self._check_idle()
            if not self.interactive or not RUN_ID.fullmatch(run_id):
                raise ValueError("Choose a saved run with a checkpoint.")
            directory = self.root / run_id
            try:
                lock = Record.acquire(directory)
            except FileNotFoundError as error:
                raise ValueError("This recording has no saved checkpoint.") from error
            except BlockingIOError as error:
                raise RunBusy(str(error)) from error
            try:
                summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
                checkpoint = json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))
                if not summary.get("resumable"):
                    raise ValueError("This recording has no current checkpoint.")
                options = summary["settings"].copy()
                if rounds is not None:
                    options["rounds"] = rounds
                settings = Settings(**options)
                completed = checkpoint["round"] - int(checkpoint["round_open"])
                if settings.rounds <= completed:
                    raise ValueError(f"Max rounds must be greater than the {completed} completed rounds.")
                stop = Event()
                backend = self._backend(settings, stop)
                run = Run.restore(settings, directory, summary, checkpoint, stop, backend, lock)
            except (FileNotFoundError, KeyError, TypeError, AttributeError) as error:
                lock.close()
                raise ValueError("This recording has no usable checkpoint.") from error
            except BaseException:
                lock.close()
                raise
            return self._start(run)

    def _check_idle(self):
        if self.closing or (self.worker and self.worker.is_alive()):
            raise RunBusy("Wait for the current run to finish.")

    def _backend(self, settings, stop):
        if settings.model == "demo":
            if not self.allow_demo:
                raise ValueError("Start the runner with --demo to enable demos.")
            return None
        key = self.keys.get(settings.backend)
        if not key:
            raise ValueError(f"Set {BACKEND_KEYS[settings.backend]} before starting a model run.")
        if settings.backend == "gemini":
            return Gemini(key, settings, stop=stop)
        return OpenRouter(key, settings)

    def _start(self, run):
        self.run = run
        self.worker = Thread(target=self.execute, args=(run,))
        self.worker.start()
        return run.record.id

    def wait(self, timeout=None):
        if self.worker:
            self.worker.join(timeout)

    def execute(self, run):
        settings = run.settings
        try:
            agent_label = "agent" if settings.agents == 1 else "agents"
            print(f"{run.module.TITLE} · {settings.model} · {settings.agents} {agent_label} · "
                  f"seed {run.record.summary['settings']['seed']}", flush=True)
            result = run.execute()
            print(f"{result['status']} · score {result['score']:.2f} · "
                  f"{result['elapsed_seconds']:.2f}s · {run.record.directory}", flush=True)
            for agent, error in result["errors"].items():
                print(f"Agent {agent}: {error}", flush=True)
            self.exit_code = int(result["status"] == "error")
        except Exception:
            self.exit_code = 1
            raise
        finally:
            with self.lock:
                self.run = None
