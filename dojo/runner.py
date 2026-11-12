import json
import math
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from time import monotonic

from dojo.level import Level, load_level, tool, validate_arguments
from dojo.models import Reply
from dojo.storage import Record, now
from dojo.wire import MAX_CALLS_PER_REPLY, TOOL_NAME, parse_arguments, valid_call

HARNESS_VERSION = 2
AGENT_ID = {"type": "string", "minLength": 1, "maxLength": 2}
MESSAGE = {"type": "string", "minLength": 1, "maxLength": 1000}
COMMUNICATION = (
    tool("whoAmI", "Return your agent ID."),
    tool("listAgents", "List the agent IDs in this session."),
    tool("messageOtherAgent", "Send a message to one other agent.", agent=AGENT_ID, message=MESSAGE),
    tool("messageAllOtherAgents", "Send a message to all other agents.", message=MESSAGE),
    tool("readMyMessages", "Read and clear the new messages addressed to you."),
)
USAGE_FIELDS = {
    "input_tokens": "prompt_tokens",
    "output_tokens": "completion_tokens",
    "total_tokens": "total_tokens",
    "cost_usd": "cost",
}


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

    def snapshot(self):
        return {
            "id": self.id, "unread": len(self.inbox), "last_look": self.last_look,
            "last_action": self.last_action, "status": self.status,
            "sent": self.sent[-6:], "received": self.received[-6:],
        }


class Run:
    def __init__(self, settings, root, stop, backend=None):
        self.settings = settings
        self.stop = stop
        self.backend = backend
        self.module = load_level(settings.level)
        self.observation_tool = getattr(self.module, "OBSERVATION_TOOL", "look")
        seed = settings.seed if settings.seed is not None else secrets.randbits(48)
        self.agents = {str(i): Agent(str(i), [{"role": "user", "content": self.module.GOAL}])
                       for i in range(1, settings.agents + 1)}
        self.puzzle: Level = self.module.Puzzle(seed, list(self.agents))
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
        self.metrics = {
            "requests": 0, "tool_calls": 0,
            **dict.fromkeys(USAGE_FIELDS, 0),
            "missing_usage": dict.fromkeys(USAGE_FIELDS, 0),
        }
        self.models = set()
        self.providers = set()
        self.errors = {}
        self.started = monotonic()
        self.won, self.score = self.puzzle.evaluate()
        self.record = Record(root, {
            "settings": {**asdict(settings), "seed": seed},
            "level_title": self.module.TITLE, "level_version": self.module.VERSION,
            "harness_version": HARNESS_VERSION,
            "started_at": now().isoformat(), "status": "running",
        })

    def dispatch(self, agent, name, arguments):
        if name not in self.definitions:
            return {"error": "Unknown tool."}
        error = validate_arguments(self.definitions[name], arguments)
        if error:
            return {"error": error}
        if name == "whoAmI":
            return {"id": agent.id}
        if name == "listAgents":
            return {"agents": list(self.agents)}
        if name == "readMyMessages":
            messages, agent.inbox = agent.inbox, []
            return {"messages": messages}
        if name == "messageOtherAgent":
            recipient = arguments["agent"]
            if recipient == agent.id or recipient not in self.agents:
                return {"error": "Choose another agent from listAgents."}
            return self.send(agent, [recipient], arguments["message"])
        if name == "messageAllOtherAgents":
            return self.send(agent, [other for other in self.agents if other != agent.id],
                             arguments["message"])
        result = self.puzzle.act(agent.id, name, arguments)
        if name == self.observation_tool and "error" not in result:
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

    def elapsed(self):
        return round(monotonic() - self.started, 4)

    def frame(self, kind, **event):
        self.record.frame({
            "round": self.round, "elapsed_seconds": self.elapsed(),
            "score": self.score, "won": self.won, "metrics": self.metrics,
            "agents": [agent.snapshot() for agent in self.agents.values()],
            "world": self.puzzle.snapshot(), "event": {"kind": kind, **event},
        })

    def action(self, agent, name, arguments, error=None):
        result = {"error": error} if error else self.dispatch(agent, name, arguments)
        agent.last_action = name
        self.metrics["tool_calls"] += 1
        self.won, self.score = self.puzzle.evaluate()
        if self.won:
            self.solved_at = self.elapsed()
        self.frame("tool", agent=agent.id, name=name,
                   arguments=None if "error" in result else arguments, result=result)
        return result

    def record_usage(self, agent, reply):
        self.metrics["requests"] += 1
        usage = reply.usage if isinstance(reply.usage, dict) else {}
        for metric, key in USAGE_FIELDS.items():
            value = usage.get(key)
            if metric == "total_tokens" and value is None:
                prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
                if type(prompt) is int and type(completion) is int:
                    value = prompt + completion
            if (type(value) is int or (metric == "cost_usd" and type(value) is float)
                    and math.isfinite(value)) and value >= 0:
                self.metrics[metric] += value
            else:
                self.metrics["missing_usage"][metric] += 1
        if reply.model:
            if self.models and reply.model not in self.models:
                self.errors[agent.id] = "OpenRouter reported more than one model for the run."
                agent.status = "error"
            self.models.add(reply.model)
        if reply.provider:
            self.providers.add(reply.provider)
        recorded_usage = {key: usage[key] for key in USAGE_FIELDS.values()
                          if type(usage.get(key)) in (int, float)}
        self.frame("request", agent=agent.id, model=reply.model, provider=reply.provider,
                   generation_id=reply.generation_id, seconds=reply.seconds,
                   finish_reason=reply.finish_reason, usage=recorded_usage, error=reply.error)

    def apply_reply(self, agent, reply: Reply):
        if reply.error:
            agent.status = "error"
            self.errors[agent.id] = reply.error
            return
        message = reply.message
        calls = message.get("tool_calls")
        if calls is None:
            calls = []
        if (not isinstance(calls, list) or len(calls) > MAX_CALLS_PER_REPLY
                or not all(valid_call(call) for call in calls)
                or len({call["id"] for call in calls}) != len(calls)):
            agent.status = "error"
            self.errors[agent.id] = "The provider returned a malformed tool-call envelope."
            return
        if not calls:
            agent.status = "stopped"
            self.frame("agent_stopped", agent=agent.id, reason=reply.finish_reason)
            return
        # Keep the provider's reasoning continuation fields within this agent's context.
        content = message.get("content")
        continuation = {key: message[key] for key in ("reasoning", "reasoning_details")
                        if message.get(key) is not None}
        agent.history.append({"role": "assistant", "tool_calls": calls,
                              "content": content if isinstance(content, str) else None,
                              **continuation})
        agent.status = "ready"
        limit = getattr(self.module, "MAX_CALLS_PER_TURN", MAX_CALLS_PER_REPLY)
        error = f"Too many tool calls. This level allows {limit} per turn." if len(calls) > limit else None
        for call in calls:
            if self.won or self.stop.is_set():
                return
            function = call["function"]
            arguments = None if error else parse_arguments(function["arguments"])
            result = self.action(agent, function["name"], arguments, error=error)
            agent.history.append({"role": "tool", "tool_call_id": call["id"],
                                  "content": json.dumps(result, ensure_ascii=False)})

    def model_rounds(self):
        with ThreadPoolExecutor(max_workers=len(self.agents)) as pool:
            for self.round in range(1, self.settings.rounds + 1):
                active = [agent for agent in self.agents.values() if agent.status == "ready"]
                if not active:
                    self.round -= 1
                    return "error" if self.errors else "agents_stopped"
                if self.stop.is_set():
                    self.round -= 1
                    return "interrupted"
                for agent in active:
                    agent.status = "thinking"
                self.frame("round")
                pending = [pool.submit(self.backend.complete, agent.history, self.tools)
                           for agent in active]
                replies = [request.result() for request in pending]
                for agent, reply in zip(active, replies):
                    self.record_usage(agent, reply)
                if len(self.models) > 1:
                    return "error"
                for agent, reply in zip(active, replies):
                    if self.won or self.stop.is_set():
                        break
                    self.apply_reply(agent, reply)
                if self.won:
                    return "solved"
                if self.stop.is_set():
                    return "interrupted"
                if self.errors:
                    return "error"
                if all(agent.status == "stopped" for agent in self.agents.values()):
                    return "agents_stopped"
        return "round_limit"

    def demo_rounds(self):
        plans = self.module.demo(self.puzzle)
        for self.round in range(1, self.settings.rounds + 1):
            if all(len(plan) < self.round for plan in plans.values()):
                self.round -= 1
                return "agents_stopped"
            for agent in self.agents:
                if len(plans[agent]) < self.round:
                    continue
                step = plans[agent][self.round - 1]
                if step is None:
                    continue
                if self.stop.is_set():
                    return "interrupted"
                self.action(self.agents[agent], *step)
                if self.won:
                    return "solved"
                if self.stop.wait(self.settings.demo_delay):
                    return "interrupted"
        return "round_limit"

    def execute(self):
        status = "error"
        try:
            self.frame("start")
            if self.won:
                self.solved_at = self.elapsed()
                status = "solved"
            else:
                status = self.demo_rounds() if self.backend is None else self.model_rounds()
        finally:
            self.record.summary.update(
                status=status, won=self.won, score=self.score, rounds=self.round,
                elapsed_seconds=self.elapsed(), time_to_solve_seconds=getattr(self, "solved_at", None),
                models=sorted(self.models), providers=sorted(self.providers),
                errors=self.errors, metrics=self.metrics, ended_at=now().isoformat(),
            )
            for agent in self.agents.values():
                if agent.status in ("ready", "thinking"):
                    agent.status = "finished"
            try:
                self.frame("finish", status=status)
                self.record.save_summary()
            finally:
                self.record.close()
        return self.record.summary
