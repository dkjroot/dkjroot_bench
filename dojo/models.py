import json
from dataclasses import dataclass
from http.client import HTTPException
from time import monotonic
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dojo.wire import MAX_REPLY_BYTES, decode


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


@dataclass
class Reply:
    message: dict | None
    usage: dict
    model: str | None = None
    provider: str | None = None
    generation_id: str | None = None
    seconds: float = 0
    finish_reason: str | None = None
    error: str | None = None


class OpenRouter:
    def __init__(self, key, settings):
        self.key = key
        self.settings = settings

    def complete(self, messages, tools):
        settings = self.settings
        routing = {"require_parameters": True}
        if settings.provider:
            routing.update(only=[settings.provider], allow_fallbacks=False)
        body = {
            "model": settings.model,
            "messages": messages,
            "tools": tools,
            "parallel_tool_calls": False,
            "max_completion_tokens": settings.max_completion_tokens,
            "provider": routing,
            "plugins": [{"id": "web", "enabled": False}, {"id": "response-healing", "enabled": False}],
            "usage": {"include": True},
        }
        if settings.reasoning_effort:
            body["reasoning"] = {"effort": settings.reasoning_effort}
        request = Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        started = monotonic()
        response = {}
        try:
            with build_opener(NoRedirect).open(request, timeout=settings.request_timeout) as incoming:
                raw = incoming.read(MAX_REPLY_BYTES + 1)
            if len(raw) > MAX_REPLY_BYTES:
                raise ValueError("Response exceeds the size limit.")
            response = decode(raw)
            if not isinstance(response, dict):
                raise ValueError("Expected a JSON object.")
            if response.get("error"):
                error = response["error"]
                raise ValueError(error.get("message", "Provider error") if isinstance(error, dict)
                                 else str(error))
            choice = response["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("Expected a completion choice.")
            if choice.get("error"):
                raise ValueError(str(choice["error"]))
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("Expected an assistant message.")
            reason = choice.get("finish_reason")
            return self.reply(response, started, message=message,
                              finish_reason=reason[:128] if isinstance(reason, str) else None)
        except HTTPError as error:
            with error:
                return self.reply(response, started, error=f"OpenRouter HTTP {error.code}: {error.reason}")
        except (OSError, HTTPException, ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
            return self.reply(response, started, error=f"OpenRouter request failed: {error}")

    def reply(self, response, started, message=None, **fields):
        response = response if isinstance(response, dict) else {}
        if "error" in fields:
            fields["error"] = fields["error"].replace(self.key, "[redacted]")[:600]

        def metadata(key):
            value = response.get(key)
            return value[:256] if isinstance(value, str) else None

        return Reply(message=message, usage=response.get("usage") or {}, model=metadata("model"),
                     provider=metadata("provider"), generation_id=metadata("id"),
                     seconds=round(monotonic() - started, 4), **fields)
