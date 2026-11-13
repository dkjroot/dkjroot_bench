import json
from threading import Event, Lock, Thread

from dojo.anthropic import Anthropic
from dojo.config import BACKEND_KEYS, TEAM, Settings
from dojo.gemini import Gemini
from dojo.level import load_level
from dojo.models import DirectChat, OpenRouter
from dojo.runner import Run, RunBusy
from dojo.storage import RUN_ID, Record


class Launcher:
    def __init__(self, settings, root, key, *, interactive=False, allow_demo=False, profiles=None, teams=None):
        self.settings = settings
        self.root = root
        self.keys = {settings.backend: key}
        self.profiles = profiles
        self.teams = teams
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

    def launch(self, level, mode, rounds=None, profile_id=None, team_id=None, squad=None, team_revision=None):
        if team_id is None and (squad is not None or team_revision is not None):
            raise ValueError("Choose a team before picking a squad.")
        if team_id is not None:
            if mode != "team" or profile_id is not None or self.teams is None:
                raise ValueError("Choose an available team profile.")
            with self.lock:
                self._check_idle()
                team = self.teams.select(team_id, load_level(level), squad, team_revision)
                settings = Settings(level=level, backend=TEAM, model=TEAM, seed=self.settings.seed,
                                    agents=len(team["agents"]), rounds=team["rounds"] if rounds is None else rounds)
                profile_ids = {str(index): agent["profile_id"] for index, agent in enumerate(team["agents"], 1)}
                profiles = self.profiles.get_many(profile_ids.values())
                roster = {"name": team["name"], "agents": {
                    id: {"name": agent["name"], "badge": agent["badge"], "sprite": agent["sprite"],
                         "settings": {key: value for key, value in profiles[profile_ids[id]][0].items() if key != "rounds"}}
                    for id, agent in zip(profile_ids, team["agents"])
                }}
                stop = Event()
                backends = self._team_backends(settings, roster, profile_ids, profiles, stop)
                return self._start(Run(settings, self.root, stop, backends, interactive=self.interactive,
                                       team=roster, team_profiles=profile_ids))
        if profile_id is not None:
            if mode != "model" or self.profiles is None:
                raise ValueError("Choose an available model profile.")
            options, key, name = self.profiles.get(profile_id)
            if rounds is not None:
                options["rounds"] = rounds
            settings = self.settings.for_level(level, **options)
            return self.start(settings, profile_id=profile_id, profile_name=name, key=key)
        if mode == "demo":
            settings = self.settings.for_level(level, model="demo", rounds=rounds)
        elif mode == "model" and self.model:
            settings = self.settings.for_level(level, rounds=self.settings.rounds if rounds is None else rounds)
        else:
            raise ValueError("Choose an available run mode.")
        return self.start(settings)

    def start(self, settings, *, profile_id=None, profile_name=None, key=None):
        with self.lock:
            self._check_idle()
            stop = Event()
            backend = self._backend(settings, stop, key)
            return self._start(Run(settings, self.root, stop, backend, interactive=self.interactive,
                                   profile_id=profile_id, profile_name=profile_name))

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
                if settings.backend == TEAM:
                    if self.profiles is None:
                        raise ValueError("This run needs its original model profiles.")
                    ids = checkpoint["team_profiles"]
                    profiles = self.profiles.get_many(ids.values())
                    backend = self._team_backends(settings, summary["team"], ids, profiles, stop)
                elif checkpoint.get("profile_id") is not None:
                    if self.profiles is None:
                        raise ValueError("This run needs its original model profile.")
                    profile, key, _ = self.profiles.get(checkpoint["profile_id"])
                    if (profile["backend"], profile["model"]) != (settings.backend, settings.model):
                        raise ValueError("Restore the original API and model in the run's profile to resume it.")
                    backend = self._backend(settings, stop, key)
                else:
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

    def _backend(self, settings, stop, key=None):
        if settings.model == "demo":
            if not self.allow_demo:
                raise ValueError("Start the runner with --demo to enable demos.")
            return None
        key = key or self.keys.get(settings.backend)
        if not key:
            raise ValueError(f"Set {BACKEND_KEYS[settings.backend]} before starting a model run.")
        if settings.backend == "gemini":
            return Gemini(key, settings, stop=stop)
        if settings.backend == "openrouter":
            return OpenRouter(key, settings)
        if settings.backend == "anthropic":
            return Anthropic(key, settings)
        return DirectChat(key, settings)

    def _team_backends(self, settings, team, ids, profiles, stop):
        backends = {}
        for agent_id, profile_id in ids.items():
            options = team["agents"][agent_id]["settings"]
            member = settings.for_level(settings.level, **options)
            profile, key, _ = profiles[profile_id]
            if (profile["backend"], profile["model"]) != (member.backend, member.model):
                raise ValueError("Restore the original API and model in each model profile to resume this team.")
            if profile_id not in backends:
                backends[profile_id] = self._backend(member, stop, key)
        return {agent_id: backends[profile_id] for agent_id, profile_id in ids.items()}

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
            name = run.team["name"] if run.team else settings.model
            print(f"{run.module.TITLE} · {name} · {settings.agents} {agent_label} · "
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
