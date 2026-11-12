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
from levels.word_jumble import Puzzle, SENTENCES


class WordJumbleTests(unittest.TestCase):
    def test_seeded_shuffles_preserve_the_words_of_every_stage(self):
        sentences = set()
        for seed in range(300):
            puzzle = Puzzle(seed, ["1"])
            self.assertEqual(len(puzzle.stages), 10)
            self.assertEqual(puzzle.stages, Puzzle(seed, ["1"]).stages)
            for stage in puzzle.stages:
                ordered = re.findall(r"[a-z]+", stage.clue.lower())
                self.assertCountEqual(stage.words, ordered)
                self.assertNotEqual(stage.words, tuple(ordered))
            opening = puzzle.stages[0]
            ordered = re.findall(r"[a-z]+", opening.clue.lower())
            self.assertNotEqual("".join(word[0] for word in opening.words), opening.answer)
            self.assertEqual("".join(word[0] for word in ordered), opening.answer)
            self.assertEqual(len({stage.answer for stage in puzzle.stages}), 10)
            self.assertTrue(all(stage.explanation for stage in puzzle.stages[1:]))
            sentences.add(opening.clue)
        self.assertEqual(sentences, set(SENTENCES))

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
        self.assertEqual(summary["metrics"]["tool_calls"], 22)
        attempts = [frame["event"] for frame in records if frame["event"].get("name") == "submit"]
        self.assertEqual([attempt["result"]["feedback"] for attempt in attempts],
                         ["Too long. Submit a single word.", "Incorrect."]
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
    def test_browser_level_switch_uses_new_agent_count_and_serialises_launches(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = Launcher(Settings(agents=4, seed=42, demo_delay=0), root, None)
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
