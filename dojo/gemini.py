import json
from http.client import HTTPException
from threading import Event
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request

from dojo.gemini_limits import GeminiPacer, prompt_weight
from dojo.models import Reply, read_json, request_json


def function_calls(content):
    return [part["functionCall"] for part in content["parts"] if "functionCall" in part]


def usage_counts(response):
    usage = response.get("usageMetadata", {})
    counts = {"total_tokens": usage.get("totalTokenCount")}
    for metric, primary, extra in (
        ("prompt_tokens", "promptTokenCount", "toolUsePromptTokenCount"),
        ("completion_tokens", "candidatesTokenCount", "thoughtsTokenCount"),
    ):
        values = (usage.get(primary, 0), usage.get(extra, 0))
        if (primary in usage or extra in usage) and all(type(value) is int and value >= 0 for value in values):
            counts[metric] = sum(values)
    return counts


class Gemini:
    def __init__(self, key, settings, *, stop=None):
        self.key = key
        self.settings = settings
        self.stop = stop if stop is not None else Event()
        self.pacer = GeminiPacer(settings, self.stop)

    def contents(self, messages):
        contents = []
        # Gemini 2.x accepts images as user input. Newer models accept them in the tool result.
        inline_tool_images = not self.settings.model.startswith("gemini-2.")
        for message in messages:
            role = message["role"]
            if role == "user":
                contents.append({"role": "user", "parts": [{"text": message["content"]}]})
            elif role == "assistant":
                # Preserve model parts verbatim, including any thought signatures.
                native = message["native_content"]
                calls = function_calls(native)
                results, images = [], []
                contents.append(native)
                if calls:
                    contents.append({"role": "user", "parts": results})
            else:
                call = calls[int(message["tool_call_id"])]
                result = {"name": call["name"]}
                if call.get("id"):
                    result["id"] = call["id"]
                content = message["content"]
                if isinstance(content, str):
                    result["response"] = json.loads(content)
                else:
                    header, data = content["image_url"]["url"].split(",", 1)
                    image = {"mimeType": header.removeprefix("data:").removesuffix(";base64"), "data": data}
                    if inline_tool_images:
                        image["displayName"] = "observation.png"
                        result["parts"] = [{"inlineData": image}]
                        result["response"] = {"image": {"$ref": image["displayName"]}}
                    else:
                        result["response"] = {"output": "Image attached."}
                        if not images:
                            contents.append({"role": "user", "parts": images})
                        images.append({"inlineData": image})
                results.append({"functionResponse": result})
        return contents

    def complete(self, messages, tools):
        settings = self.settings
        started = monotonic()
        response = {}
        retries = 0
        try:
            functions = (tool["function"] for tool in tools)
            declarations = [{"name": function["name"], "description": function["description"],
                             "parametersJsonSchema": function["parameters"]} for function in functions]
            body = {"contents": self.contents(messages),
                    "tools": [{"functionDeclarations": declarations}],
                    "generationConfig": {"maxOutputTokens": settings.max_completion_tokens}}
            if settings.gemini_thought_summaries:
                body["generationConfig"]["thinkingConfig"] = {"includeThoughts": True}
            request = Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{settings.model}:generateContent",
                data=json.dumps(body).encode(),
                headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
            )
            weight = prompt_weight(body)
            while True:
                entry = self.pacer.reserve(weight)
                if entry is None or self.stop.is_set():
                    return None
                try:
                    response = request_json(request, settings.request_timeout)
                    break
                except HTTPError as error:
                    with error:
                        try:
                            failure = read_json(error).get("error")
                        except (OSError, HTTPException, ValueError, TypeError, RecursionError):
                            failure = None
                    failure = failure if isinstance(failure, dict) else {}
                    retry = error.code == 429 and self.pacer.limited(entry, failure, error.headers.get("Retry-After") if error.headers else None)
                    if not retry or retries == 3:
                        return self.reply({}, started, rate_limit_retries=retries,
                                          error=f"Gemini HTTP {error.code}: {failure.get('message') or error.reason}")
                    retries += 1
            usage = response.get("usageMetadata")
            if isinstance(usage, dict):
                self.pacer.measured(entry, weight, usage_counts(response).get("prompt_tokens"))
            candidates = response.get("candidates")
            if not candidates:
                feedback = response.get("promptFeedback") or {}
                reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
                raise ValueError(f"Prompt blocked ({reason})." if reason else "Expected a candidate.")
            candidate = candidates[0]
            if not isinstance(candidate, dict):
                raise ValueError("Expected a candidate object.")
            content = candidate.get("content") or {"role": "model", "parts": []}
            if (not isinstance(content, dict) or content.get("role") != "model"
                    or not isinstance(content.get("parts", []), list)
                    or not all(isinstance(part, dict) for part in content.get("parts", []))):
                raise ValueError("Expected model content.")
            content.setdefault("parts", [])
            calls = [{"id": str(index), "type": "function", "function": {
                "name": call["name"], "arguments": json.dumps(call.get("args", {})),
            }} for index, call in enumerate(function_calls(content))]
            reason = candidate.get("finishReason")
            thoughts = " ".join(part["text"] for part in content["parts"]
                                if part.get("thought") is True and isinstance(part.get("text"), str))
            return self.reply(response, started, message={"tool_calls": calls}, native_content=content,
                              rate_limit_retries=retries, thought_summary=thoughts[:400] or None,
                              finish_reason=reason[:128] if isinstance(reason, str) else None)
        except (OSError, HTTPException, ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
            return self.reply(response, started, rate_limit_retries=retries, error=f"Gemini request failed: {error}")

    def reply(self, response, started, message=None, **fields):
        if "error" in fields:
            fields["error"] = fields["error"].replace(self.key, "[redacted]")[:600]
        model = response.get("modelVersion")
        generation = response.get("responseId")
        usage = usage_counts(response) if isinstance(response.get("usageMetadata"), dict) else {}
        return Reply(message=message, usage=usage, model=model[:256] if isinstance(model, str) else None,
                     provider="Google Gemini" if response else None,
                     generation_id=generation[:256] if isinstance(generation, str) else None,
                     seconds=round(monotonic() - started, 4), **fields)
