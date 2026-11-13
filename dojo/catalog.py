import math
from http.client import HTTPException
from threading import Lock
from time import monotonic

from dojo.config import MODEL_ID, MODEL_ROUTERS
from dojo.models import request_json
from dojo.storage import now

CATALOG_URL = "https://openrouter.ai/api/v1/models"
ROUTERS = MODEL_ROUTERS | {"openrouter/auto-code", "openrouter/auto-beta", "openrouter/bodybuilder"}


def token_count(value):
    return value if type(value) is int and 0 < value < 2**53 else None


def unit_price(pricing, field, scale=1):
    try:
        value = float(pricing[field]) * scale
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def text_models(payload):
    entries = payload.get("data")
    if not isinstance(entries, list) or not entries:
        raise ValueError("No model catalog data.")
    models = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model = entry.get("id")
        architecture = entry.get("architecture") or {}
        if (not isinstance(model, str) or not MODEL_ID.fullmatch(model) or model in ROUTERS
                or model.endswith(":online") or model.endswith(":batch")
                or not isinstance(architecture, dict)):
            continue
        inputs = architecture.get("input_modalities")
        outputs = architecture.get("output_modalities")
        endpoints = entry.get("endpoints")
        if (not isinstance(inputs, list) or "text" not in inputs
                or outputs != ["text"]
                or isinstance(endpoints, list) and "/api/v1/chat/completions" not in endpoints):
            continue
        provider = entry.get("top_provider") or {}
        pricing = entry.get("pricing")
        if not isinstance(pricing, dict):
            pricing = {}
        parameters = entry.get("supported_parameters")
        tools = isinstance(parameters, list) and "tools" in parameters
        rates = {field: unit_price(pricing, key, scale) for field, key, scale in (
            ("input_usd", "prompt", 1_000_000), ("output_usd", "completion", 1_000_000),
            ("request_usd", "request", 1),
        )}
        free = (rates["input_usd"] == rates["output_usd"] == 0
                and ("request" not in pricing or rates["request_usd"] == 0))
        name = entry.get("name")
        models[model] = {"model": model, "name": name[:200] if isinstance(name, str) and name else model,
                         "tools": tools, "image": "image" in inputs, "free": free,
                         "context": token_count(entry.get("context_length")),
                         "max_output": token_count(provider.get("max_completion_tokens")) if isinstance(provider, dict) else None,
                         **rates}
    return list(models.values())


class OpenRouterCatalog:
    def __init__(self):
        self.lock = Lock()
        self.snapshot = None
        self.next_fetch = 0
        self.stale = False

    def list(self, refresh=False):
        with self.lock:
            if refresh or monotonic() >= self.next_fetch:
                try:
                    models = text_models(request_json(CATALOG_URL, timeout=10))
                    if not models:
                        raise ValueError("No matching models.")
                except (OSError, HTTPException, ValueError, RecursionError) as error:
                    self.stale = True
                    self.next_fetch = monotonic() + 30
                    if self.snapshot is None:
                        raise OSError("Could not load the OpenRouter catalog.") from error
                else:
                    self.snapshot = {"models": models, "updated_at": now().isoformat()}
                    self.stale = False
                    self.next_fetch = monotonic() + 900
            if self.snapshot is None:
                raise OSError("Could not load the OpenRouter catalog.")
            return {**self.snapshot, "stale": self.stale}
