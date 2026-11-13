import json
import math
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from threading import Condition, Lock
from time import monotonic, time

from dojo.config import TEAM, validate_rounds
from dojo.level import ImageObservation, Level, load_level, tool, validate_arguments
from dojo.models import Reply
from dojo.storage import Record, now
from dojo.teams import default_characters
from dojo.wire import MAX_CALLS_PER_REPLY, TOOL_NAME, parse_arguments, valid_call

HARNESS_VERSION = 3
HALT_RETRIES = 3
CONTINUE_PROMPT = "Continue. Choose one of your tools and call it."
RECIPIENT = {"type": "string", "minLength": 1, "maxLength": 40}
MESSAGE = {"type": "string", "minLength": 1, "maxLength": 1000}
COMMUNICATION = (
    tool("whoAmI", "Return your name and agent ID."),
    tool("listAgents", "List the agent names and IDs in this session."),
    tool("messageOtherAgent", "Send a message to one other agent by name or ID.",
         agent=RECIPIENT, message=MESSAGE),
    tool("messageAllOtherAgents", "Send a message to all other agents.", message=MESSAGE),
)
USAGE_FIELDS = {
    "input_tokens": "prompt_tokens",
    "output_tokens": "completion_tokens",
    "total_tokens": "total_tokens",
    "cost_usd": "cost",
}


def usage_metrics():
    return {"requests": 0, **dict.fromkeys(USAGE_FIELDS, 0),
            "missing_usage": dict.fromkeys(USAGE_FIELDS, 0)}


def validate_request_limit(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 10000):
        raise ValueError("Requests per minute must be an integer from 1 to 10000.")


class RunBusy(Exception):
    pass


@dataclass
class Agent:
    id: str
    history: list[dict]
    inbox: list[dict] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)
    received: list[dict] = field(default_factory=list)
    last_look: dict | None = None
    last_action: str | None = None
    status: str = "ready"
    consecutive_halts: int = 0

    def snapshot(self):
        return {
            "id": self.id, "unread": len(self.inbox), "last_look": self.last_look,
            "last_action": self.last_action, "status": self.status,
            "sent": self.sent[-6:], "received": self.received[-6:],
        }


class Run:
    def __init__(self, settings, root, stop, backend=None, *, interactive=False, restoring=False,
                 profile_id=None, profile_name=None, team=None, team_profiles=None):
        self.settings = settings
        self.profile_id = profile_id
        self.team = team
        self.team_profiles = team_profiles
        self.team_models = {}
        self.stop = stop
        self.backend = backend
        self.interactive = interactive
        self.control = Condition()
        self.request_lock = Lock()
        self.request_rpm = None
        self.request_times = []
        self.retry_at = 0
        self.round_limit = settings.rounds
        self.pause_requested = False
        self.pause_reason = None
        self.paused_round = None
        self.paused_at = None
        self.paused_seconds = 0
        self.finished = False
        self.module = load_level(settings.level)
        self.observation_tool = getattr(self.module, "OBSERVATION_TOOL", "look")
        seed = settings.seed if settings.seed is not None else secrets.randbits(48)
        self.agents = {str(i): Agent(str(i), [{"role": "user", "content": self.module.GOAL}])
                       for i in range(1, settings.agents + 1)}
        self.characters = team["agents"] if team else default_characters(self.agents)
        self.puzzle: Level = self.module.Puzzle(seed, list(self.agents))
        self.advance_world = getattr(self.puzzle, "advance_round", None)
        common_tools = COMMUNICATION if getattr(self.module, "USE_COMMUNICATION", True) else ()
        self.tools = common_tools + self.puzzle.tools
        if any(definition.get("type") != "function" for definition in self.tools):
            raise ValueError("Levels must declare local function tools.")
        self.definitions = {definition["function"]["name"]: definition for definition in self.tools}
        if (len(self.definitions) != len(self.tools)
                or not all(TOOL_NAME.fullmatch(name) for name in self.definitions)):
            raise ValueError("Tools must have valid, unique names.")
        self.message_id = 0
        self.round = 0
        self.round_open = False
        self.round_replies = {}
        self.metrics = {**usage_metrics(), "tool_calls": 0, "halts": 0}
        if team:
            self.metrics["agent_usage"] = {id: usage_metrics() for id in self.agents}
        self.models = set()
        self.providers = set()
        self.errors = {}
        self.started = monotonic()
        self.won, self.score = self.puzzle.evaluate()
        if restoring:
            return
        self.record = Record(root, {
            "settings": {**asdict(settings), "seed": seed},
            "level_title": self.module.TITLE, "level_version": self.module.VERSION,
            "harness_version": HARNESS_VERSION,
            "automatic_mail": bool(common_tools),
            "started_at": now().isoformat(), "status": "running",
            **({"profile_name": profile_name} if profile_name else {}),
            **({"team": team} if team else {}),
            **({"characters": self.characters} if not team and settings.model != "demo" else {}),
        })

    def save_checkpoint(self, agents=None):
        self.record.checkpoint({
            "version": 1, "round": self.round, "elapsed_seconds": self.elapsed(),
            "profile_id": self.profile_id,
            "request_times": self.request_times,
            "retry_at": self.retry_at,
            **({"team_profiles": self.team_profiles, "team_models": self.team_models} if self.team else {}),
            "message_id": self.message_id, "metrics": self.metrics,
            "models": sorted(self.models), "providers": sorted(self.providers),
            "agents": agents if agents is not None else [asdict(agent) for agent in self.agents.values()],
            "round_open": self.round_open,
            "round_replies": {id: asdict(reply) for id, reply in self.round_replies.items()},
        })
        self.record.summary["resumable"] = True

    @classmethod
    def restore(cls, settings, directory, summary, checkpoint, stop, backend, lock):
        run = cls(settings, directory.parent, stop, backend, interactive=True, restoring=True,
                  profile_id=checkpoint.get("profile_id"), team=summary.get("team") if settings.backend == TEAM else None,
                  team_profiles=checkpoint.get("team_profiles"))
        if (checkpoint.get("version") != 1 or summary.get("harness_version") != HARNESS_VERSION
                or summary.get("level_version") != run.module.VERSION or summary.get("id") != directory.name):
            raise ValueError("This checkpoint requires the original level and runner version.")
        if backend is None:
            run.demo_plans = run.module.demo(run.puzzle)
        puzzle_tools = {tool["function"]["name"] for tool in run.puzzle.tools}
        count = 0
        with (directory / "frames.jsonl").open(encoding="utf-8") as source:
            for count, line in enumerate(source, 1):
                event = json.loads(line)["event"]
                if event["kind"] == "tick" and run.advance_world is not None:
                    run.advance_world()
                elif event["kind"] == "tool" and event["name"] in puzzle_tools and event["arguments"] is not None:
                    run.puzzle.act(event["agent"], event["name"], event["arguments"])
        if count != checkpoint["next_index"]:
            raise ValueError("The saved checkpoint does not match the end of this recording.")
        run.round = checkpoint["round"]
        run.message_id = checkpoint["message_id"]
        run.metrics = checkpoint["metrics"]
        run.request_rpm = summary.get("request_rpm")
        run.request_times = checkpoint.get("request_times", [])
        run.retry_at = checkpoint.get("retry_at", 0)
        run.models, run.providers = set(checkpoint["models"]), set(checkpoint["providers"])
        run.team_models = checkpoint.get("team_models", {})
        run.agents = {data["id"]: Agent(**data) for data in checkpoint["agents"]}
        for agent in run.agents.values():
            if agent.status == "thinking":
                agent.status = "ready"
        run.round_open = checkpoint["round_open"]
        run.round_replies = {id: Reply(**reply) for id, reply in checkpoint["round_replies"].items()}
        run.started = monotonic() - checkpoint["elapsed_seconds"]
        run.won, run.score = run.puzzle.evaluate()
        run.record = Record.reopen(directory, summary, count, lock)
        run.restored = True
        return run

    def agent_identity(self, id):
        return {"id": id, "name": self.characters[id]["name"]}

    def dispatch(self, agent, name, arguments):
        if name not in self.definitions:
            return {"error": "Unknown tool."}
        error = validate_arguments(self.definitions[name], arguments)
        if error:
            return {"error": error}
        if name == "whoAmI":
            return self.agent_identity(agent.id)
        if name == "listAgents":
            return {"agents": [self.agent_identity(id) for id in self.agents]}
        if name == "messageOtherAgent":
            recipient = arguments["agent"].strip()
            if recipient not in self.agents:
                matches = [id for id, character in self.characters.items()
                           if character["name"].casefold() == recipient.casefold()]
                if len(matches) > 1:
                    return {"error": "More than one agent has that name. Choose an ID from listAgents."}
                if matches:
                    recipient = matches[0]
            if recipient == agent.id or recipient not in self.agents:
                return {"error": "Choose another agent from listAgents."}
            return self.send(agent, [recipient], arguments["message"])
        if name == "messageAllOtherAgents":
            return self.send(agent, [other for other in self.agents if other != agent.id],
                             arguments["message"])
        result = self.puzzle.act(agent.id, name, arguments)
        if name == self.observation_tool and isinstance(result, dict) and "error" not in result:
            if "agents" in result:
                result["agents"] = [{**other, "name": self.characters[other["id"]]["name"]}
                                    for other in result["agents"]]
            agent.last_look = result
        return result

    def send(self, agent, recipients, text):
        self.message_id += 1
        message = {"id": self.message_id, "from": agent.id, "to": recipients, "text": text}
        agent.sent.append(message)
        for recipient in recipients:
            self.agents[recipient].inbox.append(message)
            self.agents[recipient].received.append(message)
        return {"delivered_to": recipients}

    def deliver_mail(self, agents):
        deliveries = {}
        for agent in agents:
            if not agent.inbox:
                continue
            messages, agent.inbox = agent.inbox, []
            agent.history.append({"role": "user", "content": json.dumps({
                "messages": [{"from": message["from"], "from_name": self.characters[message["from"]]["name"],
                              "text": message["text"]} for message in messages],
            }, ensure_ascii=False)})
            deliveries[agent.id] = [message["id"] for message in messages]
        if deliveries:
            self.frame("mail", deliveries=deliveries)

    def elapsed(self):
        current = monotonic() if self.paused_at is None else self.paused_at
        return round(current - self.started - self.paused_seconds, 4)

    def frame(self, kind, **event):
        self.record.frame({
            "round": self.round, "round_limit": self.round_limit, "elapsed_seconds": self.elapsed(),
            "score": self.score, "won": self.won, "metrics": self.metrics,
            "agents": [agent.snapshot() for agent in self.agents.values()],
            "world": self.puzzle.snapshot(), "event": {"kind": kind, **event},
        })

    def action(self, agent, name, arguments, error=None):
        result = {"error": error} if error else self.dispatch(agent, name, arguments)
        agent.last_action = name
        self.metrics["tool_calls"] += 1
        self.update_score()
        if isinstance(result, ImageObservation):
            self.frame("tool", agent=agent.id, name=name, arguments=arguments,
                       result={}, image=result.url)
        else:
            self.frame("tool", agent=agent.id, name=name,
                       arguments=None if "error" in result else arguments, result=result)
        return result

    def update_score(self):
        self.won, self.score = self.puzzle.evaluate()
        if self.won:
            self.solved_at = self.elapsed()

    def advance_round(self):
        if self.advance_world is not None:
            messages = self.advance_world()
            self.update_score()
            self.frame("tick", messages=messages)

    def agent_backend(self, agent):
        return self.team["agents"][agent.id]["settings"]["backend"] if self.team else self.settings.backend

    def record_usage(self, agent, reply, *, track_model=True):
        if reply.retry_after is not None:
            self.retry_at = max(self.retry_at, time() + reply.retry_after)
        metrics = [self.metrics]
        if self.team:
            metrics.append(self.metrics["agent_usage"][agent.id])
        for entry in metrics:
            entry["requests"] += 1 + reply.rate_limit_retries
        usage = reply.usage if isinstance(reply.usage, dict) else {}
        for metric, key in USAGE_FIELDS.items():
            value = usage.get(key)
            if metric == "total_tokens" and value is None:
                prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
                if type(prompt) is int and type(completion) is int:
                    value = prompt + completion
            if (type(value) is int or (metric == "cost_usd" and type(value) is float)
                    and math.isfinite(value)) and value >= 0:
                for entry in metrics:
                    entry[metric] += value
            else:
                for entry in metrics:
                    entry["missing_usage"][metric] += 1
        if track_model and reply.model:
            if self.models and reply.model not in self.models:
                self.errors[agent.id] = "The model API reported more than one model for the run."
                agent.status = "error"
            self.models.add(reply.model)
        if reply.provider:
            self.providers.add(reply.provider)
        recorded_usage = {key: usage[key] for key in USAGE_FIELDS.values()
                          if type(usage.get(key)) in (int, float)}
        cached = usage.get("cached_prompt_tokens")
        if self.agent_backend(agent) == "gemini" and type(cached) is int and cached >= 0:
            for entry in metrics:
                entry["cached_input_tokens"] = entry.get("cached_input_tokens", 0) + cached
            recorded_usage["cached_prompt_tokens"] = cached
        self.frame("request", agent=agent.id, model=reply.model, provider=reply.provider,
                   generation_id=reply.generation_id, seconds=reply.seconds,
                   finish_reason=reply.finish_reason, usage=recorded_usage, error=reply.error,
                   rate_limit_retries=reply.rate_limit_retries)

    @staticmethod
    def reply_error(reply):
        if reply.error:
            return reply.error
        if not isinstance(reply.message, dict):
            return "The provider returned a malformed message."
        calls = reply.message.get("tool_calls")
        if calls is not None and (not isinstance(calls, list) or len(calls) > MAX_CALLS_PER_REPLY
                                  or not all(valid_call(call) for call in calls)
                                  or len({call["id"] for call in calls}) != len(calls)):
            return "The provider returned a malformed tool-call envelope."

    def apply_reply(self, agent, reply: Reply):
        error = self.reply_error(reply)
        if error:
            agent.status = "error"
            self.errors[agent.id] = error
            return
        message = reply.message
        calls = message.get("tool_calls") or []
        thought = reply.thought_summary or message.get("reasoning") or message.get("reasoning_content")
        details = message.get("reasoning_details")
        if not thought and isinstance(details, list):
            thought = next((detail.get("summary") or detail.get("text") for detail in details
                            if isinstance(detail, dict) and detail.get("type") in ("reasoning.summary", "reasoning.text")
                            and isinstance(detail.get("summary") or detail.get("text"), str)), None)
        if isinstance(thought, str) and thought.strip():
            self.frame("thinking", agent=agent.id, text=thought.strip()[:400])
        # Keep the provider's reasoning continuation fields within this agent's context.
        content = message.get("content")
        continuation = {key: message[key] for key in ("reasoning", "reasoning_details")
                        if message.get(key) is not None}
        backend = self.agent_backend(agent)
        if backend == "anthropic" and message.get("anthropic_content"):
            continuation["anthropic_content"] = message["anthropic_content"]
        if backend == "deepseek" and isinstance(message.get("reasoning_content"), str):
            continuation["reasoning_content"] = message["reasoning_content"]
        if reply.native_content is not None:
            continuation["native_content"] = reply.native_content
        if (calls or (isinstance(content, str) and content.strip()) or (reply.native_content or {}).get("parts")
                or continuation.get("reasoning_content") or continuation.get("anthropic_content")):
            turn = {"role": "assistant", "content": content if isinstance(content, str) else None, **continuation}
            if calls:
                turn["tool_calls"] = calls
            agent.history.append(turn)
        if not calls:
            agent.consecutive_halts += 1
            self.metrics["halts"] += 1
            retry = agent.consecutive_halts <= HALT_RETRIES
            agent.status = "ready" if retry else "stopped"
            if retry:
                agent.history.append({"role": "user", "content": CONTINUE_PROMPT})
            self.frame("agent_halted", agent=agent.id, reason=reply.finish_reason,
                       retry=agent.consecutive_halts if retry else None, max_retries=HALT_RETRIES)
            if not retry:
                self.frame("agent_stopped", agent=agent.id, reason=reply.finish_reason)
            return
        agent.consecutive_halts = 0
        agent.status = "ready"
        limit = getattr(self.module, "MAX_CALLS_PER_TURN", MAX_CALLS_PER_REPLY)
        error = f"Too many tool calls. This level allows {limit} per turn." if len(calls) > limit else None
        images = []
        for call in calls:
            if self.won or (not self.interactive and self.stop.is_set()):
                break
            function = call["function"]
            arguments = None if error else parse_arguments(function["arguments"])
            result = self.action(agent, function["name"], arguments, error=error)
            if isinstance(result, ImageObservation):
                image = {"type": "image_url", "image_url": {"url": result.url}}
                if reply.native_content is None:
                    images.append(image)
                    content = "Image attached."
                else:
                    content = image
            else:
                content = json.dumps(result, ensure_ascii=False)
            agent.history.append({"role": "tool", "tool_call_id": call["id"],
                                  "content": content})
        # Chat Completions carries image inputs in user messages. Finish the tool replies first.
        if images:
            agent.history.append({"role": "user", "content": images})

    def request_reply(self, agent):
        while not self.stop.is_set():
            with self.request_lock:
                current = time()
                self.request_times = [stamp for stamp in self.request_times if stamp > current - 60]
                due = self.retry_at
                if self.request_rpm and self.request_times:
                    due = max(due, self.request_times[-1] + 60 / self.request_rpm)
                    if len(self.request_times) >= self.request_rpm:
                        due = max(due, self.request_times[-self.request_rpm] + 60)
                if due <= current:
                    self.request_times.append(current)
                    break
            self.stop.wait(due - current)
        if not self.stop.is_set():
            backend = self.backend[agent.id] if self.team else self.backend
            return backend.complete(agent.history, self.tools)

    def control_state(self):
        with self.control:
            return {
                "stopping": self.stop.is_set(), "round_limit": self.round_limit,
                "pause_reason": self.pause_reason, "pausing": self.pause_requested and self.pause_reason is None,
                "paused_round": self.paused_round if self.pause_reason else None,
                "request_rpm": self.request_rpm, "model_run": self.backend is not None,
            }

    def pause(self):
        with self.control:
            if not self.interactive or self.finished or self.stop.is_set():
                raise RunBusy("This run cannot be paused.")
            self.pause_requested = True

    def set_request_limit(self, rpm):
        validate_request_limit(rpm)
        if self.backend is None:
            raise ValueError("Request pacing applies to model runs.")
        self.request_rpm = rpm
        self.record.summary["request_rpm"] = rpm

    def resume(self, rounds=None, pacing=None):
        with self.control:
            if not self.pause_reason or self.finished or self.stop.is_set():
                raise RunBusy("Wait for the run to pause before resuming.")
            limit = self.round_limit if rounds is None else rounds
            validate_rounds(limit)
            if limit <= self.paused_round:
                raise ValueError(f"Max rounds must be greater than the {self.paused_round} completed rounds.")
            if pacing is not None:
                self.set_request_limit(pacing["rpm"])
            self.round_limit = limit
            self.pause_reason = None
            self.pause_requested = False
            self.control.notify_all()

    def interrupt(self):
        with self.control:
            self.stop.set()
            self.control.notify_all()

    def next_round(self):
        with self.control:
            while not self.stop.is_set():
                at_limit = self.round >= self.round_limit
                if not at_limit and not self.pause_requested:
                    self.round += 1
                    return None
                if not self.interactive:
                    return "round_limit"

                self.wait_for_resume("round_limit" if at_limit else "manual", self.round)
            return "interrupted"

    def wait_for_resume(self, reason, completed):
        with self.control:
            if self.stop.is_set():
                return False
            self.pause_reason = reason
            self.paused_round = completed
            self.paused_at = monotonic()
            self.frame("pause", reason=reason)
            self.record.summary.update(status="paused", rounds=self.round, score=self.score,
                                       metrics=self.metrics, errors=dict(self.errors))
            self.save_checkpoint()
            self.record.save_summary()
            self.control.wait_for(lambda: self.stop.is_set() or self.pause_reason is None)
            self.paused_seconds += monotonic() - self.paused_at
            self.paused_at = None
            if self.stop.is_set():
                return False
            self.errors.clear()
            self.frame("resume")
            self.record.summary["settings"]["rounds"] = self.round_limit
            self.record.summary.update(status="running", errors={}, resumable=False)
            self.record.save_summary()
            return True

    def reply_models(self, agents, replies):
        # Each profile may resolve to a different model. Its agents must agree on the resolved version.
        models = {}
        if self.team:
            models.update({id: {model} for id, model in self.team_models.items()})
        elif self.models:
            models[None] = set(self.models)
        known = set(models)
        for agent, reply in zip(agents, replies):
            group = self.team_profiles[agent.id] if self.team else None
            if group not in known and reply and reply.model and not self.reply_error(reply):
                models.setdefault(group, set()).add(reply.model)
        return models

    def collect_replies(self, agents, replies, *, retain):
        models = self.reply_models(agents, replies)
        for agent, reply in zip(agents, replies):
            if reply is None:
                continue
            self.record_usage(agent, reply, track_model=False)
            group = self.team_profiles[agent.id] if self.team else None
            expected = models.get(group, set())
            error = self.reply_error(reply)
            if reply.model and (len(expected) > 1 or (expected and reply.model not in expected)):
                scope = "this profile" if self.team else "the run"
                error = error or f"The model API reported more than one model for {scope}."
                self.errors[agent.id] = error
            if reply.model and (not retain or not error):
                self.models.add(reply.model)
            if not error:
                if self.team and reply.model:
                    self.team_models[group] = reply.model
                if retain:
                    self.round_replies[agent.id] = reply
            elif retain:
                self.errors[agent.id] = error

    def interactive_replies(self, pool, active):
        while not self.stop.is_set():
            pending = [agent for agent in active if agent.id not in self.round_replies]
            futures = [pool.submit(self.request_reply, agent) for agent in pending]
            replies = [future.result() for future in futures]
            self.errors.clear()
            self.collect_replies(pending, replies, retain=True)
            if self.stop.is_set():
                break
            if not self.errors:
                return [self.round_replies[agent.id] for agent in active]
            if not self.wait_for_resume("error", self.round - 1):
                break

    def model_rounds(self):
        with ThreadPoolExecutor(max_workers=len(self.agents)) as pool:
            while True:
                active = [agent for agent in self.agents.values() if agent.status == "ready"]
                if not active:
                    return "error" if self.errors else "agents_stopped"
                if not self.round_open:
                    status = self.next_round()
                    if status:
                        return status
                    self.round_open = True
                    self.deliver_mail(active)
                for agent in active:
                    agent.status = "thinking"
                self.frame("round")
                if self.interactive:
                    replies = self.interactive_replies(pool, active)
                else:
                    pending = [pool.submit(self.request_reply, agent) for agent in active]
                    replies = [request.result() for request in pending]
                    if self.team:
                        self.collect_replies(active, replies, retain=False)
                    else:
                        for agent, reply in zip(active, replies):
                            if reply is not None:
                                self.record_usage(agent, reply)
                if self.stop.is_set():
                    return "interrupted"
                if (not self.team and len(self.models) > 1) or (self.team and self.errors):
                    return "error"
                for agent, reply in zip(active, replies):
                    if self.won or (not self.interactive and self.stop.is_set()):
                        break
                    self.apply_reply(agent, reply)
                if self.won:
                    return "solved"
                if self.errors:
                    return "error"
                self.advance_round()
                self.round_open = False
                self.round_replies.clear()
                if self.won:
                    return "solved"
                if all(agent.status == "stopped" for agent in self.agents.values()):
                    return "agents_stopped"

    def demo_rounds(self):
        plans = self.demo_plans if hasattr(self, "demo_plans") else self.module.demo(self.puzzle)
        while True:
            status = self.next_round()
            if status:
                return status
            if all(len(plan) < self.round for plan in plans.values()):
                self.round -= 1
                return "agents_stopped"
            self.deliver_mail(agent for id, agent in self.agents.items()
                              if len(plans[id]) >= self.round and plans[id][self.round - 1] is not None)
            for agent in self.agents:
                if len(plans[agent]) < self.round:
                    continue
                step = plans[agent][self.round - 1]
                if step is None:
                    continue
                if not self.interactive and self.stop.is_set():
                    return "interrupted"
                self.action(self.agents[agent], *step)
                if self.won:
                    return "solved"
                if self.advance_world is None and self.stop.wait(self.settings.demo_delay) and not self.interactive:
                    return "interrupted"
            self.advance_round()
            if self.won:
                return "solved"
            if self.advance_world is not None and self.stop.wait(self.settings.demo_delay):
                return "interrupted"

    def execute(self):
        status = "error"
        try:
            if getattr(self, "restored", False):
                self.record.summary["settings"]["rounds"] = self.round_limit
                self.record.summary.pop("ended_at", None)
                self.record.summary.update(status="running", errors={}, resumable=False)
                self.frame("resume")
                self.record.save_summary()
            else:
                self.frame("start")
            if self.won:
                self.solved_at = self.elapsed()
                status = "solved"
            else:
                status = self.demo_rounds() if self.backend is None else self.model_rounds()
        finally:
            with self.control:
                self.finished = True
            self.record.summary["settings"]["rounds"] = self.round_limit
            self.record.summary.update(
                status=status, won=self.won, score=self.score, rounds=self.round,
                elapsed_seconds=self.elapsed(), time_to_solve_seconds=getattr(self, "solved_at", None),
                models=sorted(self.models), providers=sorted(self.providers),
                errors=self.errors, metrics=self.metrics, ended_at=now().isoformat(),
            )
            resumable = status == "round_limit" or (self.interactive and status == "interrupted")
            checkpoint_agents = [asdict(agent) for agent in self.agents.values()] if resumable else None
            for agent in self.agents.values():
                if agent.status in ("ready", "thinking"):
                    agent.status = "finished"
            try:
                self.frame("finish", status=status)
                if checkpoint_agents is not None:
                    self.save_checkpoint(checkpoint_agents)
                else:
                    self.record.summary["resumable"] = False
                self.record.save_summary()
            finally:
                self.record.close()
        return self.record.summary
