import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from dojo.config import Settings
from dojo.models import OpenRouter, Reply
from dojo.runner import Run
from dojo.storage import frames
from levels.glass_tic_tac_toe import Puzzle, RADIUS, SIGN, SIZE, score_board


def call(name, arguments, id="call_1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": arguments}}


def reply(*calls, usage=None, **message):
    return Reply({"tool_calls": list(calls), **message}, usage or {})


class LevelTests(unittest.TestCase):
    def test_look_is_local_and_movement_clamps_to_wall(self):
        puzzle = Puzzle(42, ["1"])
        puzzle.positions["1"] = (0, 0)
        view = puzzle.act("1", "look", {})
        self.assertEqual(set(view), {"grid", "carrying"})
        self.assertEqual(len(view["grid"]), 2 * RADIUS + 1)
        self.assertEqual(view["grid"][0][0], " ")
        self.assertEqual(view["grid"][RADIUS][RADIUS - 1], "#")
        self.assertEqual(puzzle.act("1", "moveX", {"steps": -3}), {"moved": 0})
        self.assertIn("error", puzzle.act("1", "moveX", {"steps": 4}))
        self.assertEqual(puzzle.act("1", "moveX", {"steps": 3}), {"moved": 3})
        puzzle.positions["1"] = (SIZE - 2, 0)
        self.assertEqual(puzzle.act("1", "moveX", {"steps": 3}), {"moved": 1})
        puzzle.positions["1"] = (14, SIZE - 1)
        self.assertEqual(puzzle.act("1", "look", {})["inscriptions"], [SIGN])

    def test_score_requires_reachable_x_win(self):
        self.assertEqual(score_board("........."), (False, 0))
        self.assertEqual(score_board("XXXOO...."), (True, 1))
        self.assertEqual(score_board("XXX......"), (False, 0.6))
        self.assertFalse(score_board("XXXOOO...")[0])
        self.assertFalse(score_board("XXXOO.?..")[0])


class HarnessTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_offline_demo_solves_with_one_four_or_eight_agents(self):
        for count in (1, 4, 8):
            with self.subTest(agents=count):
                run = Run(Settings(seed=42, agents=count, demo_delay=0), self.root, Event())
                summary = run.execute()
                self.assertEqual(summary["status"], "solved")
                self.assertEqual(summary["score"], 1)
                self.assertEqual(summary["metrics"]["requests"], 0)
                first, offset = frames(run.record.directory, 0, limit=1)
                rest, _ = frames(run.record.directory, offset, limit=1000)
                self.assertEqual(first[0]["event"]["kind"], "start")
                self.assertEqual(rest[-1]["event"]["kind"], "finish")
                self.assertEqual(len(first[0]["agents"]), count)
                self.assertEqual(len({(p["x"], p["y"]) for p in first[0]["world"]["players"]}), count)

    def test_demo_reports_local_clues_before_it_attempts_a_solution(self):
        run = Run(Settings(seed=42, demo_delay=0), self.root, Event())
        run.execute()
        batch, _ = frames(run.record.directory, 0, limit=1000)
        actions = [frame for frame in batch if frame["event"]["kind"] == "tool"]
        self.assertEqual(actions, sorted(actions, key=lambda f: (f["round"], f["event"]["agent"])))
        last_looks = {}
        seen = set()
        for frame in actions:
            event = frame["event"]
            agent, name, result = event["agent"], event["name"], event["result"]
            self.assertNotIn("error", result)
            if name == "look":
                last_looks[agent] = result
            if name == "lift":
                self.assertIn("plan", seen)
                break
            if name != "messageAllOtherAgents":
                continue
            text = event["arguments"]["message"]
            grid = "".join(last_looks[agent]["grid"])
            if "three-by-three grid." in text:
                self.assertIn("+", grid)
                seen.add("grid")
            if "I found an inscription" in text:
                self.assertEqual(last_looks[agent]["inscriptions"], [SIGN])
                seen.add("sign")
            for mark in ("X", "O"):
                if f"There's a loose {mark}" in text:
                    self.assertIn(mark, grid)
                    seen.add(mark)
            if "Sounds like tic-tac-toe." in text:
                self.assertEqual(seen, {"grid", "sign", "X", "O"})
                self.assertEqual(next(a["unread"] for a in frame["agents"] if a["id"] == agent), 0)
                seen.add("plan")
        self.assertIn("plan", seen)

    def test_agent_history_contains_only_its_own_observations_and_mail(self):
        run = Run(Settings(seed=42, agents=3), self.root, Event())
        self.addCleanup(run.record.close)
        one, two, three = run.agents.values()
        self.assertIsNot(one.history, two.history)
        self.assertEqual(one.history, [{"role": "user", "content": "Solve the puzzle."}])
        run.apply_reply(one, reply(call("look", "{}"), reasoning_details=[{"id": "private"}]))
        run.apply_reply(one, reply(call("messageOtherAgent", '{"agent":"2","message":"hello"}')))
        run.apply_reply(two, reply(call("readMyMessages", "{}")))
        run.apply_reply(three, reply(call("readMyMessages", "{}")))
        self.assertEqual(json.loads(two.history[-1]["content"])["messages"][0]["text"], "hello")
        self.assertEqual(json.loads(three.history[-1]["content"]), {"messages": []})
        self.assertEqual(one.history[1]["reasoning_details"], [{"id": "private"}])
        self.assertNotIn("private", json.dumps(two.history))
        self.assertNotIn("grid", json.dumps(two.history))
        batch, _ = frames(run.record.directory, 0)
        self.assertNotIn("private", json.dumps(batch))
        self.assertEqual(run.dispatch(two, "readMyMessages", {}), {"messages": []})

    def test_untrusted_tool_arguments_cannot_change_world(self):
        run = Run(Settings(seed=42), self.root, Event())
        self.addCleanup(run.record.close)
        agent = run.agents["1"]
        before = run.puzzle.snapshot()
        for name, arguments in (
            ("moveX", '{"steps":-3,"steps":3}'),
            ("moveX", '{"steps":3,"agent":"2"}'),
            ("moveX", '{"steps":true}'),
            ("moveX", '{"steps":NaN}'),
            ("moveX", '{"steps":1e999}'),
            ("moveX", '{"steps":4}'),
            ("moveX", '{"steps":-4}'),
            ("moveX", '{"steps":0}'),
            ("moveX", '{"steps":"3"}'),
            ("moveX", '{"steps":[' + "[" * 1200 + "0" + "]" * 1200 + "]}"),
            ("moveX", " " * 8193 + '{"steps":1}'),
            ("messageAllOtherAgents", '{"message":"' + "x" * 1001 + '"}'),
            ("messageAllOtherAgents", r'{"message":"\ud800"}'),
            ("look", '[]'),
            ("exec", '{"code":"print(1)"}'),
        ):
            with self.subTest(tool=name, arguments=arguments[:60]):
                run.apply_reply(agent, reply(call(name, arguments)))
                self.assertIn("error", json.loads(agent.history[-1]["content"]))
                self.assertEqual(run.puzzle.snapshot(), before)
        self.assertFalse(agent.sent)
        self.assertTrue(all(not other.inbox for other in run.agents.values()))

    def test_bad_envelope_rejects_entire_reply_and_plain_text_cannot_act(self):
        run = Run(Settings(seed=42), self.root, Event())
        self.addCleanup(run.record.close)
        agent = run.agents["1"]
        before = run.puzzle.snapshot()
        run.apply_reply(agent, reply(content='{"name":"moveX","arguments":{"steps":3}}'))
        self.assertEqual(agent.status, "stopped")
        run.apply_reply(agent, reply(call("moveX", '{"steps":3}'),
                                     call("moveY", '{"steps":3}')))
        self.assertEqual(agent.status, "error")
        self.assertEqual(run.puzzle.snapshot(), before)

    def test_multiple_moves_in_one_reply_are_rejected_and_agent_can_retry(self):
        run = Run(Settings(seed=42), self.root, Event())
        self.addCleanup(run.record.close)
        agent = run.agents["1"]
        before = run.puzzle.snapshot()
        run.apply_reply(agent, reply(call("moveX", '{"steps":3}', "x"),
                                     call("moveY", '{"steps":3}', "y")))
        self.assertEqual(agent.status, "ready")
        self.assertEqual(run.puzzle.snapshot(), before)
        self.assertEqual([entry["tool_call_id"] for entry in agent.history[-2:]], ["x", "y"])
        self.assertTrue(all("error" in json.loads(entry["content"]) for entry in agent.history[-2:]))
        run.apply_reply(agent, reply(call("moveX", '{"steps":1}')))
        self.assertEqual(json.loads(agent.history[-1]["content"]), {"moved": 1})

    def test_parallel_requests_account_for_usage_even_with_a_failed_reply(self):
        backend = Mock()
        backend.complete.side_effect = [
            Reply(None, {}, error="request failed"),
            Reply({"tool_calls": []}, {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
                  model="example/model", provider="example"),
        ]
        settings = Settings(seed=42, agents=2, model="example/model", rounds=3)
        run = Run(settings, self.root, Event(), backend)
        summary = run.execute()
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["metrics"]["requests"], 2)
        self.assertEqual(summary["metrics"]["total_tokens"], 15)
        self.assertEqual(summary["metrics"]["missing_usage"]["total_tokens"], 1)
        self.assertEqual(summary["metrics"]["missing_usage"]["cost_usd"], 1)
        self.assertEqual(summary["providers"], ["example"])

    def test_mixed_resolved_models_do_not_act(self):
        backend = Mock()
        backend.complete.side_effect = [
            Reply({"tool_calls": [call("moveX", '{"steps":8}')]}, {}, model="example/a"),
            Reply({"tool_calls": [call("moveY", '{"steps":8}')]}, {}, model="example/b"),
        ]
        run = Run(Settings(seed=42, agents=2, model="example/model"), self.root, Event(), backend)
        summary = run.execute()
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["models"], ["example/a", "example/b"])
        self.assertEqual(summary["metrics"]["tool_calls"], 0)

    def test_mocked_model_solves_through_the_openrouter_adapter(self):
        settings = Settings(seed=42, agents=1, model="example/model", rounds=160)
        run = Run(settings, self.root, Event(), OpenRouter("test-key", settings))
        plans = iter(run.module.demo(run.puzzle)["1"])
        calls = 0

        def respond(request, timeout):
            nonlocal calls
            body = json.loads(request.data)
            history = body["messages"]
            self.assertEqual(len(history), calls * 2 + 1)
            self.assertEqual(history[0], {"role": "user", "content": "Solve the puzzle."})
            if not calls:
                self.assertNotIn("glass_tic_tac_toe", request.data.decode().lower())
                self.assertNotIn("tic-tac-toe", request.data.decode().lower())
                self.assertNotIn("x must win", request.data.decode().lower())
                self.assertNotIn("x and o", request.data.decode().lower())
            if calls:
                self.assertEqual(history[-1]["tool_call_id"], f"call_{calls}")
            name, arguments = next(plans)
            calls += 1
            message = {"tool_calls": [call(name, json.dumps(arguments), f"call_{calls}")]}
            return io.BytesIO(json.dumps({
                "choices": [{"message": message}],
                "model": "example/model", "provider": "fixture",
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "cost": 0.001},
            }).encode())

        with patch("dojo.models.build_opener") as opener:
            opener.return_value.open.side_effect = respond
            summary = run.execute()
        self.assertEqual(summary["status"], "solved")
        self.assertEqual(summary["score"], 1)
        self.assertEqual(summary["metrics"]["total_tokens"], calls * 60)
        self.assertAlmostEqual(summary["metrics"]["cost_usd"], calls * 0.001)
        self.assertEqual(summary["metrics"]["missing_usage"]["cost_usd"], 0)


class TransportTests(unittest.TestCase):
    def test_request_contains_only_agent_context_and_tools(self):
        settings = Settings(model="example/model", provider="test", reasoning_effort="low")
        backend = OpenRouter("test-key", settings)
        payload = {"choices": [{"message": {"tool_calls": [call("look", "{}")]}}],
                   "usage": {"cost": 0.001}, "provider": "test"}
        with patch("dojo.models.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
            result = backend.complete([{"role": "user", "content": "Solve the puzzle."}], [])
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        body = json.loads(request.data)
        self.assertEqual(body["messages"], [{"role": "user", "content": "Solve the puzzle."}])
        self.assertEqual(body["tools"], [])
        self.assertEqual(body["provider"]["only"], ["test"])
        self.assertEqual(body["plugins"], [
            {"id": "web", "enabled": False}, {"id": "response-healing", "enabled": False},
        ])
        self.assertNotIn("test-key", request.data.decode())
        self.assertEqual(result.provider, "test")
        self.assertEqual(result.message["tool_calls"][0]["function"]["name"], "look")

    def test_wire_rejects_oversize_and_ambiguous_responses(self):
        backend = OpenRouter("test-key", Settings(model="example/model"))
        with patch("dojo.models.build_opener") as opener:
            for raw in (b"x" * (4 * 1024 * 1024 + 1), b'{"choices":[],"choices":[]}'):
                opener.return_value.open.return_value = io.BytesIO(raw)
                result = backend.complete([], [])
                self.assertIsNone(result.message)
                self.assertIsNotNone(result.error)

    def test_model_settings_reject_search_variants_and_router_ids(self):
        for model in ("example/model:online", "openrouter/auto", "@preset/example"):
            with self.subTest(model=model), self.assertRaises(ValueError):
                Settings(model=model)
