import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from dojo.config import Settings
from dojo.launcher import Launcher, RunBusy
from dojo.models import Reply
from dojo.runner import Run
from dojo.storage import frames, summaries
from levels.word_jumble import Puzzle


class WordJumbleTests(unittest.TestCase):
    def test_seeded_shuffles_preserve_the_words_of_every_stage(self):
        opening_shuffles = set()
        for seed in range(300):
            puzzle = Puzzle(seed, ["1"])
            self.assertEqual(len(puzzle.stages), 10)
            self.assertEqual(puzzle.stages, Puzzle(seed, ["1"]).stages)
            for stage in puzzle.stages:
                ordered = re.findall(r"[a-z]+", stage.clue.lower())
                self.assertCountEqual(stage.words, ordered)
                self.assertNotEqual(stage.words, tuple(ordered))
                self.assertTrue(stage.explanation)
            opening = puzzle.stages[0]
            self.assertEqual(opening.answer, "crow")
            self.assertEqual(len({stage.answer for stage in puzzle.stages}), 10)
            opening_shuffles.add(opening.words)
        self.assertGreater(len(opening_shuffles), 1)

    def test_requires_one_agent(self):
        self.assertEqual(Settings(level="word_jumble").agents, 1)
        with self.assertRaisesRegex(ValueError, "requires 1 agent"):
            Settings(level="word_jumble", agents=2)

    def test_demo_records_feedback_and_reveals_solution_only_on_success(self):
        with TemporaryDirectory() as directory:
            run = Run(Settings(level="word_jumble", seed=42, demo_delay=0), Path(directory), Event())
            summary = run.execute()
            records, _ = frames(run.record.directory, 0)
        self.assertEqual(summary["status"], "solved")
        self.assertEqual(summary["score"], 1)
        self.assertEqual(summary["metrics"]["requests"], 0)
        self.assertEqual(summary["metrics"]["tool_calls"], 21)
        attempts = [frame["event"] for frame in records if frame["event"].get("name") == "submit"]
        self.assertEqual([attempt["result"]["feedback"] for attempt in attempts],
                         ["Incorrect."]
                         + ["Success. Next puzzle is available."] * 9
                         + ["Success. All ten puzzles are solved."])
        solved = [frame for frame in records if frame["event"].get("result", {}).get("correct")]
        self.assertEqual(len(solved), 10)
        for number, frame in enumerate(solved, 1):
            self.assertEqual(frame["score"], number / 10)
            self.assertEqual(frame["won"], number == 10)
            self.assertEqual(frame["world"]["completed"], number)
            self.assertEqual(frame["world"]["stage"], min(number + 1, 10))
            self.assertEqual(frame["world"]["solution"]["stage"], number)
            self.assertEqual(frame["world"]["solution"]["answer"], frame["event"]["arguments"]["answer"])
        self.assertTrue(all(frame["world"]["solution"] is None for frame in records[:solved[0]["index"]]))
        self.assertEqual(records[-1]["world"]["solution"]["answer"], attempts[-1]["arguments"]["answer"])

    def test_future_answers_and_wrong_answers_cannot_advance_current_stage(self):
        puzzle = Puzzle(42, ["1"])
        before = puzzle.act("1", "read", {})
        later = puzzle.stages[1].answer
        self.assertFalse(puzzle.act("1", "submit", {"answer": later})["correct"])
        self.assertEqual(puzzle.evaluate(), (False, 0))
        self.assertEqual(puzzle.act("1", "read", {}), before)
        self.assertTrue(puzzle.act("1", "submit", {"answer": puzzle.stages[0].answer})["correct"])
        self.assertEqual(puzzle.evaluate(), (False, 0.1))
        self.assertEqual(puzzle.act("1", "read", {}), {"words": list(puzzle.stages[1].words)})

    def test_wrong_guesses_give_no_length_or_format_clue(self):
        puzzle = Puzzle(42, ["1"])
        for stage in puzzle.stages:
            for guess in ("?", "q", "q" * len(stage.answer), "q" * 499, "two words"):
                self.assertEqual(puzzle.act("1", "submit", {"answer": guess}),
                                 {"correct": False, "feedback": "Incorrect."})
            puzzle.act("1", "submit", {"answer": stage.answer})

    def test_model_sees_only_goal_tools_and_requested_words(self):
        with TemporaryDirectory() as directory:
            settings = Settings(level="word_jumble", seed=42, model="example/model")
            backend = Mock()
            run = Run(settings, Path(directory), Event(), backend)
            turns = iter((index, name) for index in range(10) for name in ("read", "submit"))

            def respond(history, tools):
                index, name = next(turns)
                stage = run.puzzle.stages[index]
                self.assertEqual(run.puzzle.completed, index)
                self.assertEqual([tool["function"]["name"] for tool in tools], ["read", "submit"])
                self.assertEqual(history[0], {"role": "user", "content": "Submit the correct answer."})
                arguments = {}
                if name == "read":
                    if index == 0:
                        self.assertEqual(len(history), 1)
                        self.assertNotIn(stage.answer, json.dumps(tools))
                    else:
                        self.assertEqual(json.loads(history[-1]["content"]),
                                         {"correct": True, "feedback": "Success. Next puzzle is available."})
                else:
                    self.assertEqual(json.loads(history[-1]["content"]), {"words": list(stage.words)})
                    arguments = {"answer": " " + stage.answer.upper() + " "}
                return Reply({"tool_calls": [{
                    "id": f"call_{index}_{name}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}, {})

            backend.complete.side_effect = respond
            summary = run.execute()
        self.assertEqual(summary["status"], "solved")
        self.assertEqual(summary["rounds"], 20)


class LauncherTests(unittest.TestCase):
    def test_demo_launch_and_resume_require_opt_in(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(level="word_jumble", rounds=1, demo_delay=0)
            launcher = Launcher(settings, root, None, interactive=True)
            with self.assertRaisesRegex(ValueError, "--demo"):
                launcher.launch("word_jumble", "demo")
            with self.assertRaisesRegex(ValueError, "--demo"):
                launcher.start(settings)
            run = Run(settings, root, Event())
            run.execute()
            with self.assertRaisesRegex(ValueError, "--demo"):
                launcher.start_saved(run.record.id, rounds=2)

    def test_stopping_a_model_run_records_in_flight_usage_and_allows_another_run(self):
        with TemporaryDirectory() as directory, patch("dojo.launcher.OpenRouter") as backend:
            root = Path(directory)
            settings = Settings(level="word_jumble", model="example/model", seed=42, demo_delay=0)
            launcher = Launcher(settings, root, "test-key", allow_demo=True)
            requested, release = Event(), Event()

            def respond(history, tools):
                requested.set()
                release.wait(5)
                return Reply({"tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "submit", "arguments": '{"answer":"crow"}'},
                }]}, {"total_tokens": 19})

            backend.return_value.complete.side_effect = respond
            run_id = launcher.launch("word_jumble", "model")
            try:
                self.assertTrue(requested.wait(5))
                self.assertEqual(launcher.active_run, run_id)
                self.assertFalse(launcher.run_state()["stopping"])
                with self.assertRaises(RunBusy):
                    launcher.stop_run("20260101T000000-deadbeef")
                launcher.stop_run(run_id)
                self.assertEqual(launcher.active_run, run_id)
                self.assertTrue(launcher.run_state()["stopping"])
                with self.assertRaises(RunBusy):
                    launcher.launch("word_jumble", "demo")
            finally:
                release.set()
                launcher.wait()

            self.assertIsNone(launcher.active_run)
            backend.return_value.complete.assert_called_once()
            stopped, = summaries(root)
            self.assertEqual(stopped["status"], "interrupted")
            self.assertEqual(stopped["metrics"]["total_tokens"], 19)
            self.assertEqual(stopped["metrics"]["tool_calls"], 0)
            records, _ = frames(root / run_id, 0)
            self.assertEqual(records[-1]["event"], {"kind": "finish", "status": "interrupted"})

            next_id = launcher.launch("word_jumble", "demo")
            launcher.wait()
            new_run, _ = summaries(root)
            self.assertEqual(new_run["id"], next_id)
            self.assertEqual(new_run["status"], "solved")

    def test_browser_level_switch_uses_new_agent_count_and_serialises_launches(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = Launcher(Settings(agents=4, seed=42, demo_delay=0), root, None, allow_demo=True)
            unblock = Event()
            execute = Run.execute

            def hold(run):
                unblock.wait(5)
                return execute(run)

            with patch.object(Run, "execute", hold):
                try:
                    run_id = launcher.launch("word_jumble", "demo")
                    self.assertEqual(launcher.active_run, run_id)
                    with self.assertRaises(RunBusy):
                        launcher.launch("glass_tic_tac_toe", "demo")
                finally:
                    unblock.set()
                    launcher.wait()
            self.assertIsNone(launcher.active_run)
            summary, = summaries(root)
            self.assertEqual(summary["settings"]["agents"], 1)
            self.assertEqual(summary["status"], "solved")
            self.assertIsNone(launcher.model)

    def test_model_launch_uses_configured_model_and_selected_level(self):
        with TemporaryDirectory() as directory, patch("dojo.launcher.OpenRouter") as backend:
            root = Path(directory)
            settings = Settings(model="example/model", seed=42, rounds=12, agents=4)
            launcher = Launcher(settings, root, "test-key")
            backend.return_value.complete.side_effect = [Reply({"tool_calls": [{
                "id": f"call_submit_{index}",
                "type": "function",
                "function": {"name": "submit", "arguments": json.dumps({"answer": stage.answer})},
            }]}, {}) for index, stage in enumerate(Puzzle(42, ["1"]).stages)]
            launcher.launch("word_jumble", "model")
            launcher.wait()
            summary, = summaries(root)
            self.assertEqual(launcher.model, "example/model")
            self.assertEqual(summary["settings"]["model"], "example/model")
            self.assertEqual(summary["settings"]["level"], "word_jumble")
            self.assertEqual(summary["settings"]["agents"], 1)
            self.assertEqual(summary["settings"]["rounds"], 12)
            self.assertEqual(summary["status"], "solved")

    def test_round_override_applies_to_each_launch_without_changing_the_config(self):
        with TemporaryDirectory() as directory, patch("dojo.launcher.OpenRouter") as backend:
            root = Path(directory)
            settings = Settings(level="word_jumble", model="example/model", rounds=12, demo_delay=0)
            launcher = Launcher(settings, root, "test-key", allow_demo=True)
            backend.return_value.complete.return_value = Reply({"tool_calls": [{
                "id": "call_read", "type": "function", "function": {"name": "read", "arguments": "{}"},
            }]}, {})

            with self.assertRaisesRegex(ValueError, "rounds must be an integer"):
                launcher.launch("word_jumble", "model", rounds=True)
            model_id = launcher.launch("word_jumble", "model", rounds=2)
            launcher.wait()
            demo_id = launcher.launch("word_jumble", "demo", rounds=1)
            launcher.wait()
            runs = {run["id"]: run for run in summaries(root)}
            self.assertEqual(backend.return_value.complete.call_count, 2)
            for run_id, limit in ((model_id, 2), (demo_id, 1)):
                self.assertEqual(runs[run_id]["settings"]["rounds"], limit)
                self.assertEqual(runs[run_id]["rounds"], limit)
                self.assertEqual(runs[run_id]["status"], "round_limit")
            self.assertEqual(settings.rounds, 12)
