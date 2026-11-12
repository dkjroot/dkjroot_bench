import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from dojo.config import Settings
from dojo.gemini import Gemini
from dojo.gemini_limits import GeminiPacer
from dojo.launcher import Launcher
from dojo.runner import CONTINUE_PROMPT
from dojo.storage import frames, summaries
from levels.ball_machine import GOAL, Puzzle


class GeminiTests(unittest.TestCase):
    def test_quota_retry_is_shared_and_preserves_the_request(self):
        class Clock:
            now = 100

            def is_set(self):
                return False

            def wait(self, seconds):
                self.now += seconds

        clock = Clock()
        settings = Settings(backend="gemini", model="gemini-3.8-flash", gemini_input_tpm=2_000_000,
                            gemini_thought_summaries=True)
        with patch("dojo.gemini_limits.monotonic", side_effect=lambda: clock.now):
            pacer = GeminiPacer(settings, clock)
            pacer.reserve(300_000)
            pacer.reserve(300_000)
            self.assertAlmostEqual(clock.now, 110)
            backend = Gemini("test-key", settings, stop=clock)
            fault = {"error": {"message": "Quota exceeded. Please retry in 46s.", "details": [{
                "violations": [{"quotaId": "InputTokensPerMinute", "quotaMetric": "input_token_count",
                                "quotaValue": "600000"}],
            }]}}
            error = HTTPError("https://generativelanguage.googleapis.com", 429, "Too Many Requests", {},
                              io.BytesIO(json.dumps(fault).encode()))
            payload = {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "Consider the controls.", "thought": True, "thoughtSignature": "opaque"},
                {"functionCall": {"name": "look"}},
            ]}}]}
            with patch("dojo.models.build_opener") as opener:
                opener.return_value.open.side_effect = [error, io.BytesIO(json.dumps(payload).encode())]
                reply = backend.complete([{"role": "user", "content": GOAL}], Puzzle.tools)
            self.assertEqual(clock.now, 156)
            self.assertEqual(backend.pacer.tpm, 600_000)
            self.assertEqual(reply.rate_limit_retries, 1)
            self.assertEqual(reply.thought_summary, "Consider the controls.")
            self.assertEqual(reply.native_content["parts"][0]["thoughtSignature"], "opaque")
            first, retry = opener.return_value.open.call_args_list
            self.assertEqual(first.args[0].data, retry.args[0].data)
            self.assertEqual(json.loads(first.args[0].data)["generationConfig"]["thinkingConfig"],
                             {"includeThoughts": True})

    def test_browser_launch_keeps_signatures_and_observations_in_each_agents_history(self):
        for model in ("gemini-3.1-flash-lite", "gemini-2.5-flash"):
            with self.subTest(model=model), TemporaryDirectory() as directory:
                root = Path(directory)
                settings = Settings(backend="gemini", model=model, level="ball_machine",
                                    agents=2, rounds=6, seed=42, request_timeout=15)
                launcher = Launcher(settings, root, "test-key")
                sent_images = {}

                def respond(request, timeout):
                    self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/"
                                     f"v1beta/models/{model}:generateContent")
                    self.assertEqual(request.headers["X-goog-api-key"], "test-key")
                    self.assertEqual(timeout, 15)
                    body = json.loads(request.data)
                    self.assertEqual(set(body), {"contents", "tools", "generationConfig"})
                    self.assertEqual(body["generationConfig"], {"maxOutputTokens": 2048})
                    tools, = body["tools"]
                    self.assertEqual(set(tools), {"functionDeclarations"})
                    self.assertNotRegex(json.dumps(tools).lower(),
                                        r"\b(station|corridor|hoist|plank|gripper|lid|round)\b")
                    contents = body["contents"]
                    self.assertEqual(contents[0], {"role": "user", "parts": [{"text": GOAL}]})
                    if len(contents) == 1:
                        part = {"functionCall": {"name": "whoAmI"}, "thoughtSignature": "first"}
                    else:
                        identity, = contents[2]["parts"]
                        agent = identity["functionResponse"]["response"]["id"]
                        self.assertEqual(identity, {"functionResponse": {
                            "name": "whoAmI", "response": {"id": agent},
                        }})
                        self.assertEqual(contents[1]["parts"], [
                            {"functionCall": {"name": "whoAmI"}, "thoughtSignature": "first"},
                        ])
                        look = {"functionCall": {"name": "look", "id": f"look-{agent}"},
                                "thoughtSignature": f"second-{agent}"}
                        if len(contents) == 3:
                            part = look
                        else:
                            self.assertEqual(contents[3]["parts"], [look])
                            result, = contents[4]["parts"]
                            result = result["functionResponse"]
                            self.assertEqual(result["id"], f"look-{agent}")
                            self.assertEqual(result["name"], "look")
                            if model.startswith("gemini-2."):
                                self.assertEqual(result["response"], {"output": "Image attached."})
                                image, = contents[5]["parts"]
                                base_length = 6
                            else:
                                self.assertEqual(result["response"], {"image": {"$ref": "observation.png"}})
                                image, = result["parts"]
                                base_length = 5
                            if len(contents) > base_length:
                                self.assertEqual(contents[-2:], [
                                    {"role": "model", "parts": [{"text": "Done."}]},
                                    {"role": "user", "parts": [{"text": CONTINUE_PROMPT}]},
                                ])
                            image = image["inlineData"]
                            self.assertEqual(image["mimeType"], "image/png")
                            sent_images[agent] = "data:image/png;base64," + image["data"]
                            part = {"text": "Done."}
                    return io.BytesIO(json.dumps({
                        "candidates": [{"content": {"role": "model", "parts": [part]},
                                        "finishReason": "STOP"}],
                        "modelVersion": model, "responseId": "generation",
                        "usageMetadata": {"promptTokenCount": 12, "toolUsePromptTokenCount": 2,
                                          "cachedContentTokenCount": 4,
                                          "candidatesTokenCount": 3, "thoughtsTokenCount": 5,
                                          "totalTokenCount": 22},
                    }).encode())

                with patch("dojo.models.build_opener") as opener:
                    opener.return_value.open.side_effect = respond
                    run_id = launcher.launch("ball_machine", "model")
                    launcher.wait()

                summary, = summaries(root)
                self.assertEqual(summary["status"], "agents_stopped")
                self.assertEqual(summary["settings"]["backend"], "gemini")
                self.assertEqual(summary["providers"], ["Google Gemini"])
                self.assertEqual(summary["metrics"]["input_tokens"], 12 * 14)
                self.assertEqual(summary["metrics"]["cached_input_tokens"], 12 * 4)
                self.assertEqual(summary["metrics"]["output_tokens"], 12 * 8)
                self.assertEqual(summary["metrics"]["total_tokens"], 12 * 22)
                self.assertEqual(summary["metrics"]["halts"], 8)
                self.assertEqual(summary["metrics"]["missing_usage"]["cost_usd"], 12)
                saved, _ = frames(root / run_id, 0)
                requests = [frame["event"] for frame in saved if frame["event"]["kind"] == "request"]
                self.assertEqual([event["usage"]["cached_prompt_tokens"] for event in requests], [4] * 12)
                recorded = {event["agent"]: event["image"] for frame in saved
                            if "image" in (event := frame["event"])}
                self.assertEqual(set(recorded), {"1", "2"})
                self.assertNotEqual(recorded["1"], recorded["2"])
                self.assertEqual(recorded, sent_images)
                self.assertNotIn("thoughtSignature", json.dumps(saved))
                self.assertNotIn("test-key", json.dumps(summary))

    def test_gemini_http_error_records_status_and_redacts_key(self):
        settings = Settings(backend="gemini", model="gemini-3.1-flash-lite")
        backend = Gemini("test-key", settings)
        error = HTTPError("https://generativelanguage.googleapis.com", 403, "Forbidden", {},
                          io.BytesIO(b'{"error":{"message":"Invalid test-key"}}'))
        with patch("dojo.models.build_opener") as opener:
            opener.return_value.open.side_effect = error
            result = backend.complete([{"role": "user", "content": GOAL}], Puzzle.tools)
        self.assertEqual(result.error, "Gemini HTTP 403: Invalid [redacted]")

    def test_backend_validation_and_missing_gemini_key(self):
        self.assertEqual(Settings(model="google/gemini-3.1-flash-lite").backend, "openrouter")
        with self.assertRaisesRegex(ValueError, "Gemini API model ID"):
            Settings(backend="gemini", model="google/gemini-3.1-flash-lite")
        with self.assertRaisesRegex(ValueError, "require the OpenRouter backend"):
            Settings(backend="gemini", model="gemini-3.1-flash-lite", provider="google-ai-studio")
        settings = Settings(backend="gemini", model="gemini-3.1-flash-lite")
        with TemporaryDirectory() as directory:
            launcher = Launcher(settings, Path(directory), None)
            self.assertIsNone(launcher.model)
            with self.assertRaisesRegex(ValueError, "Set GEMINI_API_KEY"):
                launcher.start(settings)
            launcher = Launcher(Settings(model="example/model"), Path(directory), "openrouter-key")
            with self.assertRaisesRegex(ValueError, "Set GEMINI_API_KEY"):
                launcher.start(settings)
