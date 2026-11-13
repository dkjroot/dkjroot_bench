import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
from unittest.mock import patch

from dojo.config import Settings
from dojo.launcher import Launcher, RunBusy
from dojo.models import Reply
from dojo.runner import Run
from dojo.storage import frames, summaries


class RunControlTests(unittest.TestCase):
    def test_request_cap_can_be_set_on_error_and_survives_saved_resume(self):
        with TemporaryDirectory() as directory, patch("dojo.launcher.OpenRouter") as factory:
            root = Path(directory)
            settings = Settings(level="word_jumble", model="example/model", rounds=1)
            backend = factory.return_value
            backend.complete.side_effect = [
                Reply(None, {}, error="429 Rate limited"),
                Reply({"tool_calls": [{"id": "call", "type": "function",
                                      "function": {"name": "read", "arguments": "{}"}}]}, {}),
            ]
            launcher = Launcher(settings, root, "test-key", interactive=True)
            run_id = launcher.launch(settings.level, "model")
            try:
                self.wait_for_pause(launcher, "error", 0)
                with self.assertRaisesRegex(ValueError, "Requests per minute"):
                    launcher.resume_run(run_id, pacing={"rpm": 0})
                start = monotonic()
                launcher.resume_run(run_id, pacing={"rpm": 120})
                self.wait_for_pause(launcher, "round_limit", 1)
                self.assertGreaterEqual(monotonic() - start, 0.4)
                self.assertEqual(launcher.run_state()["request_rpm"], 120)
            finally:
                launcher.shutdown()
                launcher.wait(5)
            self.assertEqual(summaries(root)[0]["request_rpm"], 120)
            backend.complete.side_effect = None
            backend.complete.return_value = Reply({"tool_calls": [{"id": "next", "type": "function",
                "function": {"name": "read", "arguments": "{}"}}]}, {})
            restored = Launcher(settings, root, "test-key", interactive=True)
            try:
                restored.start_saved(run_id, rounds=2, pacing={"rpm": 180})
                self.wait_for_pause(restored, "round_limit", 2)
                self.assertEqual(restored.run_state()["request_rpm"], 180)
            finally:
                restored.shutdown()
                restored.wait(5)

    def wait_for_pause(self, launcher, reason, completed):
        deadline = monotonic() + 5
        delay = Event()
        while monotonic() < deadline:
            state = launcher.run_state()
            if state.get("pause_reason") == reason and state.get("paused_round") == completed:
                return
            if state["active_run"] is None:
                break
            delay.wait(0.01)
        self.fail(f"Expected a {reason} pause after round {completed}, got {launcher.run_state()}")

    def test_model_pauses_after_in_flight_round_and_resumes_the_same_history(self):
        with TemporaryDirectory() as directory, patch("dojo.launcher.OpenRouter") as backend:
            root = Path(directory)
            settings = Settings(level="word_jumble", model="example/model", rounds=2, seed=42)
            launcher = Launcher(settings, root, "test-key", interactive=True)
            entered, release = Event(), Event()
            history_lengths = []

            def respond(history, tools):
                history_lengths.append(len(history))
                if len(history) == 1:
                    entered.set()
                    release.wait(5)
                else:
                    self.assertEqual(set(json.loads(history[-1]["content"])), {"words"})
                return Reply({"tool_calls": [{
                    "id": f"call_{len(history)}", "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }]}, {"total_tokens": 10})

            backend.return_value.complete.side_effect = respond
            run_id = launcher.launch("word_jumble", "model")
            try:
                self.assertTrue(entered.wait(5))
                launcher.pause_run(run_id)
                self.assertTrue(launcher.run_state()["pausing"])
                with self.assertRaises(RunBusy):
                    launcher.resume_run(run_id)
                release.set()
                self.wait_for_pause(launcher, "manual", 1)
                self.assertEqual(history_lengths, [1])
                self.assertEqual(summaries(root)[0]["status"], "paused")
                with self.assertRaisesRegex(ValueError, "greater than the 1 completed rounds"):
                    launcher.resume_run(run_id, 1)

                launcher.resume_run(run_id)
                self.wait_for_pause(launcher, "round_limit", 2)
                self.assertEqual(history_lengths, [1, 3])
                self.assertEqual(launcher.active_run, run_id)
                with self.assertRaisesRegex(ValueError, "greater than the 2 completed rounds"):
                    launcher.resume_run(run_id)
                launcher.resume_run(run_id, 3)
                self.wait_for_pause(launcher, "round_limit", 3)
                self.assertEqual(history_lengths, [1, 3, 5])
                launcher.stop_run(run_id)
                launcher.wait(5)
                self.assertIsNone(launcher.active_run)
            finally:
                release.set()
                launcher.shutdown()
                launcher.wait(5)

            saved, = summaries(root)
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["settings"]["rounds"], 3)
            self.assertEqual(saved["rounds"], 3)
            self.assertEqual(saved["metrics"]["total_tokens"], 30)
            replay, _ = frames(root / run_id, 0)
            controls = [(frame["event"]["kind"], frame["round"], frame["round_limit"])
                        for frame in replay if frame["event"]["kind"] in ("pause", "resume")]
            self.assertEqual(controls, [("pause", 1, 2), ("resume", 1, 2), ("pause", 2, 2),
                                        ("resume", 2, 3), ("pause", 3, 3)])
            self.assertEqual(replay[-1]["event"], {"kind": "finish", "status": "interrupted"})

    def test_ball_machine_resumes_without_repeating_mechanism_ticks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(level="ball_machine", seed=42, agents=2, rounds=3, demo_delay=0)
            baseline = Run(settings, root, Event())
            self.assertEqual(baseline.execute()["status"], "round_limit")
            launcher = Launcher(settings, root, None, interactive=True, allow_demo=True)
            run_id = launcher.launch("ball_machine", "demo", rounds=1)
            try:
                self.wait_for_pause(launcher, "round_limit", 1)
                paused, _ = frames(root / run_id, 0)
                self.assertEqual([frame["round"] for frame in paused if frame["event"]["kind"] == "tick"], [1])
                launcher.resume_run(run_id, 3)
                self.wait_for_pause(launcher, "round_limit", 3)
            finally:
                launcher.shutdown()
                launcher.wait(5)
            saved, _ = frames(root / run_id, 0)
            uninterrupted, _ = frames(baseline.record.directory, 0)
            ticks = [frame for frame in saved if frame["event"]["kind"] == "tick"]
            self.assertEqual([frame["round"] for frame in ticks], [1, 2, 3])
            self.assertEqual([frame["world"] for frame in ticks],
                             [frame["world"] for frame in uninterrupted if frame["event"]["kind"] == "tick"])
            self.assertEqual(saved[-1]["event"]["status"], "interrupted")

    def test_saved_error_checkpoint_retains_successful_calls_and_rebuilds_the_world(self):
        with TemporaryDirectory() as directory, patch("dojo.launcher.OpenRouter") as factory:
            root = Path(directory)
            settings = Settings(level="ball_machine", model="example/model", seed=42, agents=2, rounds=2)
            launcher = Launcher(settings, root, "test-key", interactive=True)

            def call(name, args):
                return Reply({"tool_calls": [{"id": "fixture", "type": "function",
                                             "function": {"name": name, "arguments": json.dumps(args)}}]}, {})

            backend = factory.return_value
            backend.complete.side_effect = [call("whoAmI", {}), call("whoAmI", {}),
                                            call("moveX", {"steps": 1}), Reply(None, {}, error="Temporary error")]
            run_id = launcher.launch("ball_machine", "model")
            try:
                self.wait_for_pause(launcher, "error", 1)
                self.assertEqual(backend.complete.call_count, 4)
                another = Launcher(settings, root, "test-key", interactive=True)
                with self.assertRaises(RunBusy):
                    another.start_saved(run_id)
                launcher.stop_run(run_id)
                launcher.wait(5)
                self.assertTrue(summaries(root)[0]["resumable"])
            finally:
                launcher.shutdown()
                launcher.wait(5)

            backend.complete.reset_mock()
            backend.complete.side_effect = None
            backend.complete.return_value = call("moveX", {"steps": 1})
            restored = Launcher(settings, root, "test-key", interactive=True)
            try:
                self.assertEqual(restored.start_saved(run_id), run_id)
                self.wait_for_pause(restored, "round_limit", 2)
                backend.complete.assert_called_once()
                history = backend.complete.call_args.args[0]
                self.assertIn(json.loads(history[2]["content"])["id"], ("1", "2"))
            finally:
                restored.shutdown()
                restored.wait(5)
            replay, _ = frames(root / run_id, 0)
            self.assertEqual([frame["round"] for frame in replay if frame["event"]["kind"] == "tick"], [1, 2])
            moves = [frame for frame in replay if frame["event"].get("name") == "moveX"]
            self.assertEqual([frame["event"]["agent"] for frame in moves], ["1", "2"])
            self.assertEqual(len(summaries(root)), 1)
