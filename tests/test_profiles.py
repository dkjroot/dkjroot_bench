import io
import json
import stat
import unittest
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from dojo.anthropic import Anthropic
from dojo.config import Settings
from dojo.launcher import Launcher
from dojo.models import DirectChat, Reply
from dojo.profiles import Profiles
from dojo.runner import Run
from dojo.viewer import serve


class ProfileTests(unittest.TestCase):
    def test_server_keeps_keys_private_and_launches_the_chosen_profile(self):
        with TemporaryDirectory() as directory, patch("dojo.launcher.OpenRouter") as adapter:
            root = Path(directory)
            path = root / "model-profiles.json"
            store = Profiles(path)
            launcher = Launcher(Settings(level="word_jumble"), root / "runs", None,
                                interactive=True, profiles=store)
            with patch("dojo.viewer.ThreadingHTTPServer") as server:
                serve(launcher.root, 0, launcher, public_root=root / "public")
            handler_class = server.call_args.args[1]
            origin = "http://127.0.0.1:8765"

            def api(endpoint, data=None, request_origin=origin, expected=200):
                handler = handler_class.__new__(handler_class)
                handler.server = SimpleNamespace(server_port=8765)
                handler.path = "/api/" + endpoint
                handler.headers = Message()
                handler.headers["Host"] = "127.0.0.1:8765"
                handler.headers["Origin"] = request_origin
                with patch.object(handler, "send") as send, patch.object(handler, "send_error") as reject:
                    if data is None:
                        handler.do_GET()
                    else:
                        raw = json.dumps(data).encode()
                        handler.headers["Content-Length"] = str(len(raw))
                        handler.headers["Content-Type"] = "application/json"
                        handler.rfile = io.BytesIO(raw)
                        handler.do_POST()
                if reject.called:
                    reject.assert_called_once_with(expected)
                    return None
                body, _, status = send.call_args.args
                self.assertEqual(status, expected)
                return json.loads(body)

            try:
                presets = api("profiles")
                self.assertTrue(any(profile["model"] == "google/gemini-3.5-flash-lite" for profile in presets))
                self.assertTrue(all(not profile["enabled"] for profile in presets))
                self.assertFalse(path.exists())
                setup = {"backend": "openrouter", "model": "google/gemini-3.5-flash-lite",
                         "rounds": 2, "max_completion_tokens": 500, "provider": "google-ai-studio"}
                api("profiles/save", {**setup, "name": "Cheap", "api_key": "fixture-cheap"},
                    request_origin="https://other.test", expected=403)
                cheap = api("profiles/save", {**setup, "name": "Cheap", "api_key": "fixture-cheap"})
                paid = api("profiles/save", {**setup, "name": "Paid", "api_key": "fixture-paid"})
                self.assertTrue(paid["enabled"])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertNotIn("fixture-", json.dumps([paid, cheap, api("profiles")]))
                self.assertEqual(Profiles(path).get(cheap["id"])[1], "fixture-cheap")
                api("profiles/save", {**setup, "id": paid["id"], "name": "Paid renamed"})
                api("profiles/save", {"id": paid["id"], "name": "Other", "backend": "gemini",
                                      "model": "gemini-3.5-flash-lite"}, expected=400)
                adapter.return_value.complete.return_value = Reply({"tool_calls": [{
                    "id": "call", "type": "function", "function": {"name": "read", "arguments": "{}"},
                }]}, {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0005})
                run = api("launch", {"level": "word_jumble", "mode": "model", "profile_id": paid["id"],
                                     "rounds": 1}, expected=202)
                launcher.stop_run(run["id"])
                launcher.wait(5)
                key, settings = adapter.call_args.args
                self.assertEqual(key, "fixture-paid")
                self.assertEqual((settings.backend, settings.model, settings.rounds, settings.max_completion_tokens),
                                 ("openrouter", "google/gemini-3.5-flash-lite", 1, 500))
                saved = launcher.root / run["id"]
                self.assertEqual(json.loads((saved / "summary.json").read_text())["profile_name"], "Paid renamed")
                self.assertEqual(json.loads((saved / "summary.json").read_text())["characters"],
                                 {"1": {"name": "Ann", "badge": "A", "sprite": "rocket"}})
                for file in ("summary.json", "frames.jsonl", "checkpoint.json"):
                    self.assertNotIn("fixture-", (saved / file).read_text())
                self.assertEqual(json.loads((saved / "checkpoint.json").read_text())["profile_id"], paid["id"])
                resumed = Launcher(launcher.settings, launcher.root, None, interactive=True, profiles=Profiles(path))
                try:
                    self.assertEqual(resumed.start_saved(run["id"], rounds=3), run["id"])
                    self.assertEqual(adapter.call_args.args[0], "fixture-paid")
                finally:
                    resumed.shutdown()
                    resumed.wait(5)
                api("profiles/save", {**setup, "id": paid["id"], "name": "Paid renamed", "api_key": ""})
                api("launch", {"level": "word_jumble", "mode": "model", "profile_id": paid["id"]}, expected=400)
                api("profiles/delete", {"id": cheap["id"]})
                self.assertNotIn(cheap["id"], {profile["id"] for profile in Profiles(path).list()})
            finally:
                launcher.shutdown()
                launcher.wait(5)


class DirectProviderTests(unittest.TestCase):
    def test_direct_chat_routes_to_the_provider_and_passes_only_supported_settings(self):
        cases = (
            ("openai", "gpt-5.5", "https://api.openai.com/v1/chat/completions", "max_completion_tokens"),
            ("xai", "grok-4.3", "https://api.x.ai/v1/chat/completions", "max_tokens"),
            ("deepseek", "deepseek-v4-pro", "https://api.deepseek.com/chat/completions", "max_tokens"),
        )
        for backend, model, url, limit in cases:
            with self.subTest(backend=backend), patch("dojo.models.request_json") as send:
                settings = Settings(backend=backend, model=model, max_completion_tokens=500, reasoning_effort="low")
                transport = DirectChat("fixture-key", settings)
                message = {"tool_calls": [{"id": "call", "type": "function",
                                          "function": {"name": "read", "arguments": "{}"}}]}
                if backend == "deepseek":
                    message["tool_calls"][0]["index"] = 0
                send.return_value = {"choices": [{"message": message}], "id": "fixture", "model": model,
                                     "usage": {"prompt_tokens": 17, "completion_tokens": 5}}
                if backend == "xai":
                    send.return_value["usage"].update(cost_in_usd_ticks=10_000_000, total_tokens=25,
                                                      completion_tokens_details={"reasoning_tokens": 3})
                reply = transport.complete([{"role": "user", "content": "Solve the puzzle."}], [])
                request, timeout = send.call_args.args
                body = json.loads(request.data)
                self.assertEqual(request.full_url, url)
                self.assertEqual(request.get_header("Authorization"), "Bearer fixture-key")
                self.assertEqual((body[limit], timeout, body["reasoning_effort"]), (500, 90, "low"))
                self.assertFalse({"provider", "plugins", "api_key"} & body.keys())
                self.assertNotIn("index", reply.message["tool_calls"][0])
                self.assertEqual(reply.usage["prompt_tokens"], 17)
                if backend == "xai":
                    self.assertEqual(reply.usage["cost"], 0.001)
                    self.assertEqual(reply.usage["completion_tokens"], 8)

    def test_anthropic_preserves_thinking_and_tool_blocks_across_rounds(self):
        with TemporaryDirectory() as directory, patch("dojo.anthropic.request_json") as send:
            settings = Settings(level="word_jumble", backend="anthropic", model="claude-opus-4-8", rounds=2)
            backend = Anthropic("fixture-anthropic", settings)
            content = [{"type": "thinking", "thinking": "Look at the puzzle.", "signature": "signed"},
                       {"type": "tool_use", "id": "toolu_fixture", "name": "read", "input": {}}]
            send.return_value = {"content": content, "model": settings.model, "stop_reason": "tool_use",
                                 "usage": {"input_tokens": 11, "cache_read_input_tokens": 4, "output_tokens": 8}}
            run = Run(settings, Path(directory), Event(), backend)
            summary = run.execute()
            first, second = [call.args[0] for call in send.call_args_list]
            self.assertEqual(first.full_url, "https://api.anthropic.com/v1/messages")
            self.assertEqual(first.get_header("X-api-key"), "fixture-anthropic")
            first_body = json.loads(first.data)
            self.assertEqual(first_body["messages"], [{"role": "user", "content": [
                {"type": "text", "text": run.module.GOAL}]}])
            self.assertNotIn("word_jumble", first.data.decode())
            history = json.loads(second.data)["messages"]
            self.assertEqual(history[1], {"role": "assistant", "content": content})
            self.assertEqual(history[2]["content"][0]["tool_use_id"], "toolu_fixture")
            self.assertEqual(summary["metrics"]["total_tokens"], 46)
            self.assertEqual(summary["metrics"]["missing_usage"]["cost_usd"], 2)
