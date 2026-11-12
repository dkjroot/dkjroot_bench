import json
import re
from dataclasses import asdict, dataclass, fields, replace

from dojo.level import load_level

MODEL_ID = re.compile(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*(?::[\w.-]+)?", re.ASCII)
MODEL_ROUTERS = {"openrouter/auto", "openrouter/free", "openrouter/pareto-code", "openrouter/fusion"}
GEMINI_MODEL_ID = re.compile(r"gemini-[a-z0-9]+(?:[.-][a-z0-9]+)*")
BACKEND_KEYS = {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}
DEFAULT_DEMO_ROUNDS = 160
MAX_ROUNDS = 1000


def validate_rounds(rounds):
    if type(rounds) is not int or not 1 <= rounds <= MAX_ROUNDS:
        raise ValueError(f"rounds must be an integer from 1 to {MAX_ROUNDS}.")


@dataclass(frozen=True)
class Settings:
    level: str = "glass_tic_tac_toe"
    model: str = "demo"
    agents: int | None = None
    seed: int | None = None
    rounds: int | None = None
    max_completion_tokens: int = 2048
    request_timeout: int = 90
    provider: str | None = None
    reasoning_effort: str | None = None
    demo_delay: float = 0.14
    backend: str = "openrouter"
    gemini_input_tpm: int | None = None
    gemini_rpm: int | None = None
    gemini_thought_summaries: bool = False

    def __post_init__(self):
        if not isinstance(self.level, str):
            raise ValueError("level must be a directory name.")
        level = load_level(self.level)
        if self.agents is None:
            object.__setattr__(self, "agents", level.DEFAULT_AGENTS)
        if self.rounds is None:
            rounds = DEFAULT_DEMO_ROUNDS if self.model == "demo" else getattr(level, "DEFAULT_MODEL_ROUNDS", 60)
            object.__setattr__(self, "rounds", rounds)
        validate_rounds(self.rounds)
        for name, lower, upper in (
            ("agents", 1, 8),
            ("max_completion_tokens", 1, 200_000), ("request_timeout", 1, 600),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer from {lower} to {upper}.")
        fixed_agents = getattr(level, "FIXED_AGENTS", None)
        if fixed_agents is not None and self.agents != fixed_agents:
            raise ValueError(f"{level.TITLE} requires {fixed_agents} agent.")
        minimum_agents = getattr(level, "MIN_AGENTS", 1)
        if self.agents < minimum_agents:
            raise ValueError(f"{level.TITLE} requires at least {minimum_agents} agents.")
        if self.seed is not None and (type(self.seed) is not int or abs(self.seed) > 2**53 - 1):
            raise ValueError("seed must be an integer between -(2**53 - 1) and 2**53 - 1.")
        if type(self.demo_delay) not in (int, float) or not 0 <= self.demo_delay <= 5:
            raise ValueError("demo_delay must be a number from 0 to 5 seconds.")
        if not isinstance(self.backend, str) or self.backend not in BACKEND_KEYS:
            raise ValueError("backend must be openrouter or gemini.")
        for name in ("gemini_input_tpm", "gemini_rpm"):
            value = getattr(self, name)
            if value is not None and (self.backend != "gemini" or type(value) is not int or value < 1):
                raise ValueError(f"{name} requires a positive integer and the Gemini backend.")
        if type(self.gemini_thought_summaries) is not bool or (self.gemini_thought_summaries and self.backend != "gemini"):
            raise ValueError("gemini_thought_summaries requires the Gemini backend.")
        if self.backend == "gemini":
            if self.model != "demo" and (not isinstance(self.model, str)
                                         or not GEMINI_MODEL_ID.fullmatch(self.model)):
                raise ValueError("model must be demo or a Gemini API model ID such as gemini-3.1-flash-lite.")
            if self.provider is not None or self.reasoning_effort is not None:
                raise ValueError("provider and reasoning_effort settings require the OpenRouter backend.")
        elif self.model != "demo":
            if not isinstance(self.model, str) or not MODEL_ID.fullmatch(self.model):
                raise ValueError("model must be demo or a concrete OpenRouter model ID.")
            model, _, variant = self.model.lower().partition(":")
            if model in MODEL_ROUTERS or variant == "online":
                raise ValueError("Choose one model without a router or the web search variant.")
        if self.provider is not None and (not isinstance(self.provider, str)
                                          or not self.provider.strip()):
            raise ValueError("provider must be an OpenRouter provider slug.")
        if self.reasoning_effort not in (None, "none", "minimal", "low", "medium",
                                         "high", "xhigh"):
            raise ValueError("Invalid reasoning_effort.")
        if self.model == "demo" and not hasattr(level, "demo"):
            raise ValueError(f"{self.level} has no scripted demo.")

    def for_level(self, level, **changes):
        agents = self.agents if self.level == level else None
        return replace(self, level=level, agents=agents, **changes)

    @classmethod
    def read(cls, path):
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, dict):
            raise ValueError("The configuration must be a JSON object.")
        unknown = data.keys() - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        return cls(**data)

    def write(self, path):
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
