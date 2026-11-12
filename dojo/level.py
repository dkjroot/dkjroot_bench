import importlib
import re
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

LEVELS = Path(__file__).resolve().parent.parent / "levels"


@dataclass(frozen=True)
class ImageObservation:
    url: str

    @classmethod
    def png(cls, data):
        return cls("data:image/png;base64," + b64encode(data).decode("ascii"))


class Level(Protocol):
    tools: tuple[dict, ...]

    def act(self, agent: str, name: str, arguments: dict) -> dict | ImageObservation: ...
    def evaluate(self) -> tuple[bool, float]: ...
    def snapshot(self) -> dict: ...


def available_levels():
    return sorted(path.name for path in LEVELS.iterdir()
                  if re.fullmatch(r"[a-z][a-z0-9_]*", path.name)
                  and (path / "__init__.py").is_file())


def load_level(name):
    if name not in available_levels():
        raise ValueError(f"Unknown level {name!r}. Choose from {', '.join(available_levels())}.")
    return importlib.import_module(f"levels.{name}")


def tool(name, description, **properties):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


def validate_arguments(definition, arguments):
    fields = definition["function"]["parameters"]["properties"]
    if not isinstance(arguments, dict) or arguments.keys() != fields.keys():
        return "Expected arguments: " + (", ".join(fields) or "none")
    for name, field in fields.items():
        value = arguments[name]
        if field["type"] == "integer":
            if type(value) is not int or not field["minimum"] <= value <= field["maximum"]:
                return f"{name} must be an integer from {field['minimum']} to {field['maximum']}."
            if "enum" in field and value not in field["enum"]:
                return f"{name} must be one of {', '.join(map(str, field['enum']))}."
        elif field["type"] == "number":
            if type(value) not in (int, float) or not field["minimum"] <= value <= field["maximum"]:
                return f"{name} must be a number from {field['minimum']} to {field['maximum']}."
        elif field["type"] == "string":
            if not isinstance(value, str) or not 1 <= len(value) <= field["maxLength"]:
                return f"{name} must contain 1 to {field['maxLength']} characters."
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                return f"{name} must contain valid Unicode."
        else:
            raise ValueError(f"Unsupported tool argument type: {field['type']}")
    return None
