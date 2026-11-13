import json
from http.client import HTTPException
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request

from dojo.models import Reply, request_json


class Anthropic:
    def __init__(self, key, settings):
        self.key = key
        self.settings = settings

    @staticmethod
    def messages(history):
        messages = []
        for message in history:
            role = message["role"]
            text = message.get("content")
            content = []
            if role == "tool":
                role = "user"
                content.append({"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": text})
            elif role == "assistant":
                content = list(message["anthropic_content"])
            elif isinstance(text, str) and text.strip():
                content.append({"type": "text", "text": text})
            elif role == "user" and isinstance(text, list):
                for image in text:
                    header, data = image["image_url"]["url"].split(",", 1)
                    content.append({"type": "image", "source": {"type": "base64", "data": data,
                                    "media_type": header.removeprefix("data:").removesuffix(";base64")}})
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(content)
            else:
                messages.append({"role": role, "content": content})
        return messages

    def complete(self, history, tools):
        settings = self.settings
        started = monotonic()
        response = {}
        try:
            definitions = [tool["function"] for tool in tools]
            body = {"model": settings.model, "messages": self.messages(history),
                    "max_tokens": settings.max_completion_tokens,
                    "tools": [{"name": tool["name"], "description": tool["description"],
                               "input_schema": tool["parameters"]} for tool in definitions]}
            if tools:
                body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
            request = Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                              headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                       "Content-Type": "application/json"})
            response = request_json(request, settings.request_timeout)
            content = response["content"]
            if (not isinstance(content, list) or not content
                    or not all(isinstance(block, dict) for block in content)):
                raise ValueError("Expected message content.")
            text = "\n".join(block["text"] for block in content if block["type"] == "text")
            calls = [{"id": block["id"], "type": "function", "function": {
                "name": block["name"], "arguments": json.dumps(block["input"]),
            }} for block in content if block["type"] == "tool_use"]
            thought = " ".join(block["thinking"] for block in content
                               if block["type"] == "thinking" and isinstance(block.get("thinking"), str))
            return self.reply(response, started, message={"content": text, "tool_calls": calls,
                                                         "anthropic_content": content},
                              thought_summary=thought[:400] or None)
        except HTTPError as error:
            with error:
                return self.reply(response, started, error=f"Anthropic HTTP {error.code}: {error.reason}")
        except (OSError, HTTPException, ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
            return self.reply(response, started, error=f"Anthropic request failed: {error}")

    def reply(self, response, started, message=None, error=None, thought_summary=None):
        usage = response.get("usage")
        counts = {}
        if isinstance(usage, dict):
            inputs = (usage.get("input_tokens"), usage.get("cache_creation_input_tokens", 0),
                      usage.get("cache_read_input_tokens", 0))
            if all(type(count) is int and count >= 0 for count in inputs):
                counts["prompt_tokens"] = sum(inputs)
            counts["completion_tokens"] = usage.get("output_tokens")

        def metadata(key):
            value = response.get(key)
            return value[:256] if isinstance(value, str) else None

        return Reply(message, counts, model=metadata("model"), provider="Anthropic" if response else None,
                     generation_id=metadata("id"), finish_reason=metadata("stop_reason"),
                     thought_summary=thought_summary,
                     seconds=round(monotonic() - started, 4),
                     error=error.replace(self.key, "[redacted]")[:600] if error else None)
