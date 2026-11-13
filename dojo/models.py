import json
from dataclasses import dataclass
from http.client import HTTPException
from time import monotonic
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dojo.wire import MAX_REPLY_BYTES, decode


def openrouter_account_rate_rejection(error):
    return (isinstance(error, str) and error.startswith("OpenRouter HTTP 429:")
            and "new-account-rpm/" in error)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def read_json(incoming):
    raw = incoming.read(MAX_REPLY_BYTES + 1)
    if len(raw) > MAX_REPLY_BYTES:
        raise ValueError("Response exceeds the size limit.")
    response = decode(raw)
    if not isinstance(response, dict):
        raise ValueError("Expected a JSON object.")
    return response


def request_json(request, timeout):
    with build_opener(NoRedirect).open(request, timeout=timeout) as incoming:
        return read_json(incoming)


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
    native_content: dict | None = None
    rate_limit_retries: int = 0
    thought_summary: str | None = None
    retry_after: float | None = None


class ChatCompletions:
    def __init__(self, key, settings):
        self.key = key
        self.settings = settings

    def request_body(self, messages, tools):
        return {
            "model": self.settings.model,
            "messages": messages,
            "tools": tools,
        }

    def complete(self, messages, tools):
        request = Request(
            self.url,
            data=json.dumps(self.request_body(messages, tools)).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        started = monotonic()
        response = {}
        try:
            response = request_json(request, self.settings.request_timeout)
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
                try:
                    response = read_json(error)
                except (OSError, HTTPException, ValueError, TypeError, AttributeError, RecursionError):
                    pass
                failure = response.get("error")
                detail = failure.get("message") if isinstance(failure, dict) else None
                reason = detail if isinstance(detail, str) and detail.strip() else error.reason
                retry_after = None
                if error.code == 429:
                    hint = error.headers.get("Retry-After", "") if error.headers else ""
                    retry_after = min(int(hint), 86400) if hint.isascii() and hint.isdecimal() and len(hint) <= 9 else 60
                message = f"{self.provider_name} HTTP {error.code}: {reason}"
                if openrouter_account_rate_rejection(message) and not response.get("usage") and not response.get("id"):
                    response["usage"] = dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens", "cost"), 0)
                return self.reply(response, started, error=message, retry_after=retry_after)
        except (OSError, HTTPException, ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
            return self.reply(response, started, error=f"{self.provider_name} request failed: {error}")

    def reply(self, response, started, message=None, **fields):
        response = response if isinstance(response, dict) else {}
        if "error" in fields:
            fields["error"] = fields["error"].replace(self.key, "[redacted]")[:600]
        if message and isinstance(message.get("tool_calls"), list):
            # Some providers include the stream position on completed calls too.
            message = {**message, "tool_calls": [
                {key: value for key, value in call.items() if key != "index"} if isinstance(call, dict) else call
                for call in message["tool_calls"]
            ]}

        def metadata(key):
            value = response.get(key)
            return value[:256] if isinstance(value, str) else None

        return Reply(message=message, usage=response.get("usage") or {}, model=metadata("model"),
                     provider=metadata("provider") or (self.direct_provider if response else None),
                     generation_id=metadata("id"),
                     seconds=round(monotonic() - started, 4), **fields)


class OpenRouter(ChatCompletions):
    url = "https://openrouter.ai/api/v1/chat/completions"
    provider_name = "OpenRouter"
    direct_provider = None

    def request_body(self, messages, tools):
        settings = self.settings
        routing = {"require_parameters": True}
        if settings.provider:
            routing.update(only=[settings.provider], allow_fallbacks=False)
        body = super().request_body(messages, tools)
        # OpenRouter endpoint capabilities advertise the completion cap as max_tokens.
        # The runner enforces each level's tool-call limit after receiving the reply.
        body.update(max_tokens=settings.max_completion_tokens, provider=routing,
                    plugins=[{"id": "web", "enabled": False},
                                               {"id": "response-healing", "enabled": False}])
        if settings.reasoning_effort:
            body["reasoning"] = {"effort": settings.reasoning_effort}
        return body


class DirectChat(ChatCompletions):
    endpoints = {
        "openai": ("OpenAI", "https://api.openai.com/v1/chat/completions"),
        "xai": ("xAI", "https://api.x.ai/v1/chat/completions"),
        "deepseek": ("DeepSeek", "https://api.deepseek.com/chat/completions"),
    }

    def __init__(self, key, settings):
        super().__init__(key, settings)
        self.provider_name, self.url = self.endpoints[settings.backend]
        self.direct_provider = self.provider_name

    def request_body(self, messages, tools):
        body = super().request_body(messages, tools)
        settings = self.settings
        if settings.backend == "openai":
            body.update(store=False, parallel_tool_calls=False, max_completion_tokens=settings.max_completion_tokens)
        else:
            body["max_tokens"] = settings.max_completion_tokens
        effort = settings.reasoning_effort
        if settings.backend == "deepseek" and effort is not None:
            body["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
            if effort == "none":
                effort = None
        if effort is not None:
            body["reasoning_effort"] = effort
        return body

    def reply(self, response, started, message=None, **fields):
        reply = super().reply(response, started, message, **fields)
        if self.settings.backend == "xai" and isinstance(reply.usage, dict):
            usage = reply.usage.copy()
            ticks = usage.get("cost_in_usd_ticks")
            if type(ticks) is int and 0 <= ticks < 2**63:
                usage["cost"] = ticks / 10_000_000_000
            prompt, completion, total = (usage.get(field) for field in
                                         ("prompt_tokens", "completion_tokens", "total_tokens"))
            details = usage.get("completion_tokens_details")
            reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
            # Some Grok responses count reasoning only in the total.
            if (all(type(count) is int and count >= 0 for count in (prompt, completion, total, reasoning))
                    and total == prompt + completion + reasoning):
                usage["completion_tokens"] = completion + reasoning
            reply.usage = usage
        return reply
