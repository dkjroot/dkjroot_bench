import json
import unittest
from base64 import b64decode
from io import BytesIO
from pathlib import Path
from struct import unpack
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from dojo.config import Settings
from dojo.level import ImageObservation
from dojo.models import OpenRouter
from dojo.runner import Run
from dojo.storage import frames
from levels.ball_machine import Puzzle, STATIONS, STRIDE, demo


class BallMachineTests(unittest.TestCase):
    def setUp(self):
        self.crew = {key: str(i) for i, key in enumerate(STATIONS, 1)}
        self.puzzle = Puzzle(42, list(self.crew.values()))

    def park_crew(self):
        for key, agent in self.crew.items():
            x, y = STATIONS[key]
            while x > 0:
                steps = min(x, STRIDE)
                self.puzzle.act(agent, "moveX", {"steps": steps})
                x -= steps
            self.puzzle.act(agent, "moveY", {"steps": y})

    def tick(self, **amounts):
        for key, amount in amounts.items():
            self.puzzle.act(self.crew[key], "actuate", {"amount": amount})
        return self.puzzle.advance_round()

    def release_ball(self):
        self.park_crew()
        self.tick(hoist=1, jaws=0)
        self.tick(hoist=1, jaws=1)
        self.tick()
        self.tick(slide=1)
        self.tick()
        self.tick(jaws=0)

    def test_look_is_a_plain_grayscale_png_and_movement_stays_in_the_corridor(self):
        initial = self.puzzle.act("2", "look", {})
        self.assertIsInstance(initial, ImageObservation)
        data = b64decode(initial.url.split(",", 1)[1], validate=True)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(unpack(">IIBBBBB", data[16:29]), (380, 260, 8, 0, 0, 0, 0))
        chunks = []
        offset = 8
        while offset < len(data):
            size, name = unpack(">I4s", data[offset:offset + 8])
            chunks.append(name)
            offset += size + 12
        self.assertEqual(chunks, [b"IHDR", b"IDAT", b"IEND"])
        self.assertEqual(list(self.puzzle.positions.values()), [(0, 0)] * 5)
        self.assertNotEqual(self.puzzle.act("1", "look", {}), initial)
        self.assertEqual(self.puzzle.act("1", "actuate", {"amount": 0.75}),
                         {"message": "This does nothing here."})
        self.assertEqual(self.puzzle.act("1", "moveY", {"steps": 3}), {"message": "Moved."})
        self.assertEqual(self.puzzle.positions["1"], (0, 2))
        self.assertEqual(self.puzzle.act("1", "moveY", {"steps": 3}), {"message": "Blocked."})
        self.assertNotEqual(self.puzzle.act("2", "look", {}), initial)

    def test_pickup_needs_the_close_edge_and_hoist_release_unblocks_slide(self):
        self.park_crew()
        self.tick(hoist=1, jaws=1, slide=1, tilt=1, lid=1)
        for _ in range(5):
            self.tick(hoist=1, jaws=1, slide=1, tilt=1, lid=1)
        self.assertFalse(self.puzzle.held)
        self.assertEqual(self.puzzle.slide, 0)
        self.assertFalse(self.puzzle.snapshot()["lid"]["open"])
        self.tick(hoist=1, jaws=0)
        self.tick(hoist=1, jaws=1)
        self.assertIsNotNone(self.puzzle.held)
        self.tick()
        self.assertEqual(self.puzzle.depth, 0.5)
        self.tick()
        self.assertEqual(self.puzzle.depth, 0)
        self.assertGreater(self.puzzle.slide, 0)
        self.assertEqual(self.puzzle.evaluate(), (False, 0))

    def test_default_plank_spills_the_ball(self):
        self.release_ball()
        messages = [message for _ in range(12) for message in self.tick()]
        self.assertIn("The ball rolled off the plank.", messages)
        self.assertIn("A ball fell onto the floor.", messages)
        self.assertEqual(self.puzzle.balls, [])
        self.assertEqual(self.puzzle.evaluate(), (False, 0))

    def test_near_level_plank_catches_without_sending_the_ball_towards_goal(self):
        self.release_ball()
        start = self.puzzle.balls[0].x
        for _ in range(20):
            self.tick(tilt=0.55)
        self.assertEqual(self.puzzle.balls[0].phase, "rolling")
        self.assertEqual(self.puzzle.balls[0].x, start)
        self.assertEqual(self.puzzle.evaluate(), (False, 0))

    def test_lid_stays_open_for_two_rounds_and_needs_a_release(self):
        self.park_crew()
        for expected in (True, True, False, False, False):
            self.tick(lid=0.8)
            self.assertEqual(self.puzzle.snapshot()["lid"]["open"], expected)
        self.tick()
        self.tick(lid=0.8)
        self.assertTrue(self.puzzle.snapshot()["lid"]["open"])

    def test_numeric_validation_rejects_untrusted_actuation_values(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(level="ball_machine", seed=42, demo_delay=0)
        run = Run(settings, Path(directory.name), Event())
        self.addCleanup(run.record.close)
        agent = run.agents["1"]
        run.dispatch(agent, "moveX", {"steps": 2})
        run.dispatch(agent, "moveY", {"steps": 2})
        for amount in (True, "0.5", -0.01, 1.01, float("nan"), float("inf")):
            with self.subTest(amount=amount):
                self.assertIn("error", run.dispatch(agent, "actuate", {"amount": amount}))
                self.assertFalse(run.puzzle.pending)
        result = run.dispatch(agent, "actuate", {"amount": 0.25})
        self.assertEqual(result, {"message": "Actuated."})
        self.assertEqual(run.puzzle.pending["hoist"], 0.25)

    def test_demo_replay_shows_a_miss_and_awards_goals_only_at_closure(self):
        with TemporaryDirectory() as directory:
            for agents in range(2, 9):
                with self.subTest(agents=agents):
                    settings = Settings(level="ball_machine", agents=agents, seed=42, demo_delay=0)
                    run = Run(settings, Path(directory), Event())
                    summary = run.execute()
                    saved, _ = frames(run.record.directory, 0, limit=500)
                    self.assertEqual(summary["status"], "solved")
                    self.assertEqual(summary["metrics"]["requests"], 0)
                    self.assertEqual(saved[-1]["score"], 1)
                    ticks = [frame for frame in saved if frame["event"]["kind"] == "tick"]
                    self.assertEqual([frame["world"]["round"] for frame in ticks],
                                     list(range(1, summary["rounds"] + 1)))
                    outcomes = {message: frame for frame in ticks for message in frame["event"]["messages"]}
                    self.assertEqual(outcomes["The ball bounced off the closed lid."]["score"], 0)
                    for number in range(1, 4):
                        goal = outcomes[f"The lid closed. Goal {number} of 3."]
                        self.assertEqual(goal["score"], number / 3)
                        self.assertFalse(goal["world"]["lid"]["open"])
                        inside = ticks[goal["round"] - 2]
                        self.assertEqual(inside["score"], (number - 1) / 3)
                        self.assertTrue(inside["world"]["lid"]["open"])
                        self.assertIn("in_bucket", {ball["phase"] for ball in inside["world"]["balls"]})

    def test_model_transport_receives_the_same_private_pngs_as_the_recording(self):
        with TemporaryDirectory() as directory:
            settings = Settings(level="ball_machine", model="example/model")
            self.assertEqual(settings.rounds, 400)
            run = Run(settings, Path(directory), Event(), OpenRouter("test-key", settings))
            plan = demo(run.puzzle)
            sent_images = {}
            sent_mail = {}

            def respond(request, timeout):
                body = json.loads(request.data)
                history = body["messages"]
                self.assertEqual(history[0], {
                    "role": "user", "content": "Get 3 balls into the bin before the time runs out.",
                })
                index = sum(message["role"] == "assistant" for message in history)
                if index == 0:
                    self.assertEqual(len(history), 1)
                    tools = json.dumps(body["tools"]).lower()
                    self.assertNotIn("readmymessages", tools)
                    for hint in ("station", "corridor", "hoist", "plank", "gripper", "lid", "round"):
                        self.assertNotRegex(tools, rf"\b{hint}\b")
                    name, arguments = "whoAmI", {}
                else:
                    agent = json.loads(history[2]["content"])["id"]
                    if history[-1]["role"] == "user" and isinstance(history[-1]["content"], str):
                        sent_mail[agent, index + 1] = json.loads(history[-1]["content"])["messages"]
                        history = history[:-1]
                    if history[-1]["role"] == "user":
                        self.assertEqual(history[-2]["content"], "Image attached.")
                        self.assertEqual(history[-2]["tool_call_id"], f"call_{index - 1}")
                        part, = history[-1]["content"]
                        self.assertEqual(set(part), {"type", "image_url"})
                        self.assertEqual(part["type"], "image_url")
                        sent_images[agent, index] = part["image_url"]["url"]
                    name, arguments = plan[agent][index] or ("look", {})
                return BytesIO(json.dumps({"choices": [{"message": {"tool_calls": [{
                    "id": f"call_{index}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}}]}).encode())

            with patch("dojo.models.build_opener") as opener:
                opener.return_value.open.side_effect = respond
                summary = run.execute()
            self.assertEqual(summary["status"], "solved")
            self.assertEqual(summary["score"], 1)
            self.assertEqual(summary["metrics"]["requests"], summary["rounds"] * settings.agents)
            saved, _ = frames(run.record.directory, 0, limit=2000)
            recorded = {(event["agent"], frame["round"]): event["image"]
                        for frame in saved if "image" in (event := frame["event"])
                        and frame["round"] < summary["rounds"]}
            self.assertTrue(recorded)
            self.assertEqual(recorded, sent_images)
            messages = {message["id"]: {"from": message["from"], "from_name": run.characters[message["from"]]["name"],
                                       "text": message["text"]}
                        for frame in saved for agent in frame["agents"] for message in agent["sent"]}
            delivered = {(agent, frame["round"]): [messages[id] for id in ids]
                         for frame in saved if frame["event"]["kind"] == "mail"
                         for agent, ids in frame["event"]["deliveries"].items()}
            self.assertTrue(delivered)
            self.assertEqual(delivered, sent_mail)
            self.assertTrue(all(agent["last_look"] is None for frame in saved for agent in frame["agents"]))
