from threading import Event, Lock, Thread

from dojo.models import OpenRouter
from dojo.runner import Run


class RunBusy(Exception):
    pass


class Launcher:
    def __init__(self, settings, root, key):
        self.settings = settings
        self.root = root
        self.key = key
        self.stop = Event()
        self.lock = Lock()
        self.worker = None
        self.run_id = None
        self.exit_code = 0

    @property
    def model(self):
        if self.key and self.settings.model != "demo":
            return self.settings.model
        return None

    @property
    def active_run(self):
        with self.lock:
            return self.run_id if self.worker and self.worker.is_alive() else None

    def launch(self, level, mode):
        if mode == "demo":
            settings = self.settings.for_level(level, model="demo", rounds=None)
        elif mode == "model" and self.model:
            settings = self.settings.for_level(level)
        else:
            raise ValueError("Choose an available run mode.")
        return self.start(settings)

    def start(self, settings):
        with self.lock:
            if self.stop.is_set() or (self.worker and self.worker.is_alive()):
                raise RunBusy("Wait for the current run to finish.")
            backend = None
            if settings.model != "demo":
                if not self.key:
                    raise ValueError("Set OPENROUTER_API_KEY before starting a model run.")
                backend = OpenRouter(self.key, settings)
            run = Run(settings, self.root, self.stop, backend)
            self.run_id = run.record.id
            self.worker = Thread(target=self.execute, args=(run,))
            self.worker.start()
            return self.run_id

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
