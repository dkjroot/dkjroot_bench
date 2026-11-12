import json
import re
from dataclasses import asdict, dataclass, fields, replace

from dojo.level import load_level

MODEL_ID = re.compile(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*(?::[\w.-]+)?", re.ASCII)
MODEL_ROUTERS = {"openrouter/auto", "openrouter/free", "openrouter/pareto-code", "openrouter/fusion"}


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

    def __post_init__(self):
        if not isinstance(self.level, str):
            raise ValueError("level must be a directory name.")
        level = load_level(self.level)
        if self.agents is None:
            object.__setattr__(self, "agents", level.DEFAULT_AGENTS)
        if self.rounds is None:
            object.__setattr__(self, "rounds", 160 if self.model == "demo" else 60)
        for name, lower, upper in (
            ("agents", 1, 8), ("rounds", 1, 1000),
            ("max_completion_tokens", 1, 200_000), ("request_timeout", 1, 600),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer from {lower} to {upper}.")
        fixed_agents = getattr(level, "FIXED_AGENTS", None)
        if fixed_agents is not None and self.agents != fixed_agents:
            raise ValueError(f"{level.TITLE} requires {fixed_agents} agent.")
        if self.seed is not None and (type(self.seed) is not int or abs(self.seed) > 2**53 - 1):
            raise ValueError("seed must be an integer between -(2**53 - 1) and 2**53 - 1.")
        if type(self.demo_delay) not in (int, float) or not 0 <= self.demo_delay <= 5:
            raise ValueError("demo_delay must be a number from 0 to 5 seconds.")
        if self.model != "demo":
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
