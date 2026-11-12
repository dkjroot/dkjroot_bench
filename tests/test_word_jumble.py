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
    def test_seeded_words_are_shuffled_without_changing_the_sentence(self):
        sentences = set()
        for seed in range(300):
            puzzle = Puzzle(seed, ["1"])
            ordered = re.findall(r"[a-z]+", puzzle.sentence.lower())
            words = puzzle.act("1", "read", {})["words"]
            self.assertEqual(words, Puzzle(seed, ["1"]).act("1", "read", {})["words"])
            self.assertCountEqual(words, ordered)
            self.assertNotEqual("".join(word[0] for word in words), puzzle.answer)
            self.assertEqual("".join(word[0] for word in ordered), puzzle.answer)
            sentences.add(puzzle.sentence)
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
        self.assertEqual(summary["metrics"]["tool_calls"], 4)
        self.assertTrue(all(frame["world"]["solution"] is None for frame in records if not frame["won"]))
        attempts = [frame["event"] for frame in records if frame["event"].get("name") == "submit"]
        self.assertEqual([attempt["result"]["feedback"] for attempt in attempts],
                         ["Too long. Submit a single word.", "Incorrect.", "Correct."])
        self.assertEqual(records[-1]["world"]["solution"]["answer"], attempts[-1]["arguments"]["answer"])

    def test_model_sees_only_goal_tools_and_requested_words(self):
        with TemporaryDirectory() as directory:
            settings = Settings(level="word_jumble", seed=42, model="example/model")
            backend = Mock()
            run = Run(settings, Path(directory), Event(), backend)
            turns = iter([("read", {}), ("submit", {"answer": " " + run.puzzle.answer.upper() + " "})])

            def respond(history, tools):
                name, arguments = next(turns)
                self.assertEqual([tool["function"]["name"] for tool in tools], ["read", "submit"])
                self.assertEqual(history[0], {"role": "user", "content": "Submit the correct answer."})
                if name == "read":
                    self.assertEqual(len(history), 1)
                    self.assertNotIn(run.puzzle.answer, json.dumps(tools))
                else:
                    self.assertEqual(json.loads(history[-1]["content"]), {"words": run.puzzle.words})
                return Reply({"tool_calls": [{
                    "id": "call_" + name,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}, {})

            backend.complete.side_effect = respond
            summary = run.execute()
        self.assertEqual(summary["status"], "solved")
        self.assertEqual(summary["rounds"], 2)


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
            settings = Settings(model="example/model", seed=42, rounds=8, agents=4)
            launcher = Launcher(settings, root, "test-key")
            answer = Puzzle(42, ["1"]).answer
            backend.return_value.complete.return_value = Reply({"tool_calls": [{
                "id": "call_submit",
                "type": "function",
                "function": {"name": "submit", "arguments": json.dumps({"answer": answer})},
            }]}, {})
            launcher.launch("word_jumble", "model")
            launcher.wait()
            summary, = summaries(root)
            self.assertEqual(launcher.model, "example/model")
            self.assertEqual(summary["settings"]["model"], "example/model")
            self.assertEqual(summary["settings"]["level"], "word_jumble")
            self.assertEqual(summary["settings"]["agents"], 1)
            self.assertEqual(summary["settings"]["rounds"], 8)
            self.assertEqual(summary["status"], "solved")
