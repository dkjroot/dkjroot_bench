import json
import re
from dataclasses import asdict
from uuid import uuid4

from dojo.config import TEAM, Settings
from dojo.storage import Record, write_private_json

PROFILE_ID = re.compile(r"[a-z0-9-]{1,64}")
PROFILE_OPTIONS = (
    "backend", "model", "max_completion_tokens", "request_timeout", "provider", "reasoning_effort",
    "gemini_input_tpm", "gemini_rpm", "gemini_thought_summaries",
)
PRESET_MODELS = (
    ("Gemini 3.8 Flash", "google/gemini-3.8-flash", "gemini", "gemini-3.8-flash"),
    ("Gemini 3.5 Flash-Lite", "google/gemini-3.5-flash-lite", "gemini", "gemini-3.5-flash-lite"),
    ("Gemini 3.1 Pro Preview", "google/gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview"),
    ("GPT-5.5", "openai/gpt-5.5", "openai", "gpt-5.5"),
    ("GPT-5.4 Mini", "openai/gpt-5.4-mini", "openai", "gpt-5.4-mini"),
    ("Claude Opus 4.8", "anthropic/claude-opus-4.8", "anthropic", "claude-opus-4-8"),
    ("Claude Sonnet 5", "anthropic/claude-sonnet-5", "anthropic", "claude-sonnet-5"),
    ("Claude Haiku 4.5", "anthropic/claude-haiku-4.5", "anthropic", "claude-haiku-4-5-20251001"),
    ("Grok 4.3", "x-ai/grok-4.3", "xai", "grok-4.3"),
    ("DeepSeek V4 Pro", "deepseek/deepseek-v4-pro", "deepseek", "deepseek-v4-pro"),
    ("Qwen3.7 Plus", "qwen/qwen3.7-plus", None, None),
)


def presets():
    entries = {}
    for name, router_model, direct_backend, direct_model in PRESET_MODELS:
        routes = [("openrouter", router_model, "OpenRouter")]
        if direct_backend:
            routes.append((direct_backend, direct_model, "Direct"))
        for backend, model, label in routes:
            profile_id = re.sub(r"[^a-z0-9]+", "-", backend + "-" + model)
            entries[profile_id] = {"name": f"{name} · {label}", "rounds": None, "api_key": "",
                                   **model_options({"backend": backend, "model": model})}
    return entries


def model_options(data):
    model = data.get("model")
    if not isinstance(model, str) or not model or model == "demo" or len(model) > 256:
        raise ValueError("Enter a model ID for this profile.")
    if data.get("backend") == TEAM:
        raise ValueError("Choose an API for this model profile.")
    settings = asdict(Settings(**{key: data[key] for key in PROFILE_OPTIONS if key in data},
                               rounds=data.get("rounds")))
    return {key: settings[key] for key in PROFILE_OPTIONS}


def public_profile(profile_id, profile):
    return {"id": profile_id, "enabled": bool(profile["api_key"]),
            **{key: profile[key] for key in ("name", "rounds", *PROFILE_OPTIONS)}}


class Profiles:
    def __init__(self, path):
        self.path = path
        self.defaults = presets()

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                entries = json.load(source)
            required = {*PROFILE_OPTIONS, "name", "rounds", "api_key"}
            if not isinstance(entries, dict) or any(
                not PROFILE_ID.fullmatch(profile_id) or not isinstance(profile, dict)
                or not required <= profile.keys() or not isinstance(profile["api_key"], str)
                for profile_id, profile in entries.items()
            ):
                raise ValueError
            return entries
        except FileNotFoundError:
            return self.defaults.copy()
        except ValueError as error:
            raise OSError("Could not read model profiles.") from error

    def list(self):
        return [public_profile(profile_id, profile) for profile_id, profile in self._read().items()]

    def get(self, profile_id):
        return self.resolve(self._read(), profile_id)

    def get_many(self, profile_ids):
        entries = self._read()
        return {profile_id: self.resolve(entries, profile_id) for profile_id in dict.fromkeys(profile_ids)}

    @staticmethod
    def resolve(entries, profile_id):
        if not isinstance(profile_id, str) or not PROFILE_ID.fullmatch(profile_id):
            raise ValueError("Choose a model profile.")
        profile = entries.get(profile_id)
        if not profile or not profile["api_key"]:
            raise ValueError("Add an API key to an available model profile before running it.")
        settings = {key: profile[key] for key in (*PROFILE_OPTIONS, "rounds")}
        return settings, profile["api_key"], profile["name"]

    def save(self, data):
        if not isinstance(data, dict) or data.keys() - {*PROFILE_OPTIONS, "name", "id", "api_key", "rounds"}:
            raise ValueError("Expected model profile settings.")
        name = data.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or not name.strip().isprintable():
            raise ValueError("Enter a profile name of up to 100 characters.")
        options = model_options(data)
        profile_id = data.get("id")
        if "id" in data and (not isinstance(profile_id, str) or not PROFILE_ID.fullmatch(profile_id)):
            raise ValueError("Choose a model profile.")
        with self._lock():
            entries = self._read()
            previous = entries.get(profile_id)
            if profile_id and previous is None:
                raise ValueError("That model profile no longer exists.")
            previous_key = previous["api_key"] if previous else ""
            if previous_key and options["backend"] != previous["backend"] and "api_key" not in data:
                raise ValueError("Replace or remove the API key when changing APIs.")
            api_key = data.get("api_key", previous_key)
            if not isinstance(api_key, str):
                raise ValueError("Enter an API key or leave it blank.")
            api_key = api_key.strip()
            if api_key and (len(api_key) > 2048 or any(not 33 <= ord(char) <= 126 for char in api_key)):
                raise ValueError("Enter an API key without spaces.")
            profile_id = profile_id or uuid4().hex
            profile = {"name": name.strip(), "rounds": data.get("rounds"), **options, "api_key": api_key}
            entries[profile_id] = profile
            write_private_json(self.path, entries)
        return public_profile(profile_id, profile)

    def delete(self, profile_id):
        if not isinstance(profile_id, str) or not PROFILE_ID.fullmatch(profile_id):
            raise ValueError("Choose a model profile.")
        with self._lock():
            entries = self._read()
            if profile_id not in entries:
                raise ValueError("That model profile no longer exists.")
            del entries[profile_id]
            write_private_json(self.path, entries)

    def _lock(self):
        try:
            return Record.acquire(self.path.parent, self.path.name + ".lock")
        except BlockingIOError as error:
            raise BlockingIOError("Another viewer is saving a model profile. Try again.") from error

