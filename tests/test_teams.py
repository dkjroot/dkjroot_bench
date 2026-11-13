import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from threading import Event
from unittest.mock import patch

from dojo.config import Settings
from dojo.launcher import Launcher
from dojo.models import Reply
from dojo.profiles import Profiles
from dojo.publication import publish
from dojo.storage import frames
from dojo.teams import Teams


class TeamTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.profiles = Profiles(self.root / "model-profiles.json")
        self.first = self.profiles.save({"name": "Gemini on OpenRouter", "model": "google/gemini-3.5-flash-lite",
                                         "api_key": "fixture-router", "max_completion_tokens": 333})
        self.second = self.profiles.save({"name": "Gemini direct", "model": "gemini-3.5-flash-lite",
                                          "backend": "gemini", "api_key": "fixture-direct"})
        self.teams = Teams(self.root / "team-profiles.json", self.profiles)
        self.roster = [
            {"name": "Titan", "badge": "t", "sprite": "glider", "profile_id": self.first["id"]},
            {"name": "Rhea", "badge": "R", "sprite": "rover", "profile_id": self.second["id"]},
        ]
        self.team = self.teams.save({"name": "Saturn", "rounds": 1, "agents": self.roster})

    def launcher(self, *, interactive=False):
        return Launcher(Settings(seed=42), self.root / "runs", None, interactive=interactive,
                        profiles=self.profiles, teams=self.teams)

    @staticmethod
    def reply(model, **fields):
        return Reply({"tool_calls": [{"id": "call", "type": "function",
                                     "function": {"name": "whoAmI", "arguments": "{}"}}]},
                     model=model, **fields)

    def wait_for_pause(self, launcher):
        deadline = monotonic() + 4
        while monotonic() < deadline and launcher.active_run:
            if launcher.run_state().get("pause_reason") == "round_limit":
                return
            Event().wait(0.01)
        self.fail(f"Expected a round limit pause, got {launcher.run_state()}")

    def test_eight_agents_route_and_record_separate_model_usage(self):
        roster = self.roster + [dict(self.roster[0], name=f"Moon {index}", badge=str(index)) for index in range(3, 9)]
        self.team = self.teams.save({"id": self.team["id"], "name": "Saturn", "agents": roster, "rounds": 3})
        launcher = self.launcher()
        with patch("dojo.launcher.OpenRouter") as router, patch("dojo.launcher.Gemini") as direct:
            router.return_value.complete.return_value = self.reply(
                "google/gemini-3.5-flash-lite", provider="OpenRouter",
                usage={"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001})
            direct.return_value.complete.return_value = self.reply(
                "gemini-3.5-flash-lite-001", provider="Google Gemini",
                usage={"prompt_tokens": 14, "completion_tokens": 3, "cached_prompt_tokens": 4})
            run_id = launcher.launch("glass_tic_tac_toe", "team", rounds=1, team_id=self.team["id"],
                                     squad=list(range(8)))
            launcher.wait(5)
            self.assertIsNone(launcher.active_run)
            self.assertEqual(router.call_count, 1)
            self.assertEqual(direct.call_count, 1)
            self.assertEqual(router.call_args.args[0], "fixture-router")
            self.assertEqual(direct.call_args.args[0], "fixture-direct")
            self.assertEqual(router.call_args.args[1].max_completion_tokens, 333)
            self.assertEqual(router.return_value.complete.call_count, 7)
            self.assertEqual(direct.return_value.complete.call_count, 1)
            for call in router.return_value.complete.call_args_list + direct.return_value.complete.call_args_list:
                history, tools = call.args
                self.assertEqual(history[0], {"role": "user", "content": "Solve the puzzle."})
                sent = json.dumps([history, tools])
                for secret in ("Saturn", "fixture-", "glider"):
                    self.assertNotIn(secret, sent)
                self.assertNotIn("Titan", json.dumps([history[0], tools]))
                self.assertNotIn("Rhea", json.dumps([history[0], tools]))
        directory = launcher.root / run_id
        summary = json.loads((directory / "summary.json").read_text())
        self.assertEqual(summary["status"], "round_limit")
        self.assertEqual(summary["models"], ["gemini-3.5-flash-lite-001", "google/gemini-3.5-flash-lite"])
        self.assertEqual(summary["team"]["agents"]["1"]["badge"], "t")
        self.assertEqual(summary["metrics"]["total_tokens"], 101)
        self.assertEqual(summary["metrics"]["agent_usage"]["2"]["cached_input_tokens"], 4)
        self.assertEqual(summary["metrics"]["missing_usage"]["cost_usd"], 1)
        replay, _ = frames(directory, 0)
        actions = [frame["event"] for frame in replay if frame["event"]["kind"] == "tool"]
        self.assertEqual([event["agent"] for event in actions], [str(index) for index in range(1, 9)])
        self.assertEqual(actions[0]["result"], {"id": "1", "name": "Titan"})
        publish(launcher.root, self.root / "public", run_id, "git", True)
        public = (self.root / "public" / run_id / "summary.json").read_text()
        self.assertIn("Titan", public)
        self.assertNotIn("fixture-", public)
        self.assertNotIn("profile_id", public)

    def test_resume_uses_recorded_roster_and_current_keys(self):
        launcher = self.launcher(interactive=True)
        with patch("dojo.launcher.OpenRouter") as router, patch("dojo.launcher.Gemini") as direct:
            router.return_value.complete.return_value = self.reply("google/gemini-3.5-flash-lite", usage={})
            direct.return_value.complete.return_value = self.reply("gemini-3.5-flash-lite-001", usage={})
            run_id = launcher.launch("glass_tic_tac_toe", "team", team_id=self.team["id"])
            try:
                self.wait_for_pause(launcher)
            finally:
                launcher.shutdown()
                launcher.wait(5)
            self.teams.delete(self.team["id"])
            self.profiles.save({"id": self.first["id"], "name": "Updated", "model": "google/gemini-3.5-flash-lite",
                                "api_key": "fixture-replacement", "max_completion_tokens": 600})
            resumed = self.launcher(interactive=True)
            try:
                resumed.start_saved(run_id, rounds=2)
                self.wait_for_pause(resumed)
                self.assertEqual(router.call_args.args[0], "fixture-replacement")
                self.assertEqual(router.call_args.args[1].max_completion_tokens, 333)
            finally:
                resumed.shutdown()
                resumed.wait(5)
            summary = json.loads((resumed.root / run_id / "summary.json").read_text())
            self.assertEqual(summary["team"]["name"], "Saturn")
            self.assertEqual(summary["metrics"]["requests"], 4)

    def test_invalid_rosters_and_unavailable_profiles(self):
        with self.assertRaisesRegex(ValueError, "requires 1 agent"):
            self.launcher().launch("word_jumble", "team", team_id=self.team["id"], squad=[0, 1])
        with self.assertRaisesRegex(ValueError, "needs a squad of 2 to 8"):
            self.launcher().launch("ball_machine", "team", team_id=self.team["id"], squad=[0])
        for squad in ([0, 0], [False], [2], [-1]):
            with self.subTest(squad=squad), self.assertRaisesRegex(ValueError, "distinct agents"):
                self.launcher().launch("glass_tic_tac_toe", "team", team_id=self.team["id"], squad=squad)
        with self.assertRaisesRegex(ValueError, "between 1 and 8"):
            self.teams.save({"name": "Too many", "agents": [self.roster[0]] * 9})
        with self.assertRaisesRegex(ValueError, "one letter or number"):
            self.teams.save({"name": "Bad character", "agents": [dict(self.roster[0], badge="II")]})
        self.profiles.delete(self.second["id"])
        self.assertEqual(self.teams.list()[0]["available_agents"], [0])
        with self.assertRaisesRegex(ValueError, "API key"):
            self.launcher().launch("glass_tic_tac_toe", "team", team_id=self.team["id"], squad=[1])

    def test_default_squad_and_ordered_subset(self):
        from levels import ball_machine, glass_tic_tac_toe, word_jumble

        roster = self.roster + [dict(self.roster[0], name=f"Moon {index}") for index in range(3, 9)]
        team = self.teams.save({"id": self.team["id"], "name": "Saturn", "agents": roster, "rounds": 1})
        for level, count in ((glass_tic_tac_toe, 4), (ball_machine, 5), (word_jumble, 1)):
            with self.subTest(level=level.TITLE):
                self.assertEqual(self.teams.select(team["id"], level)["agents"], roster[:count])
        with self.assertRaisesRegex(ValueError, "roster changed"):
            self.teams.select(team["id"], glass_tic_tac_toe, [7, 1], revision=self.team["revision"])
        launcher = self.launcher()
        with patch("dojo.launcher.OpenRouter") as router, patch("dojo.launcher.Gemini") as direct:
            router.return_value.complete.return_value = self.reply("google/gemini-3.5-flash-lite", usage={})
            direct.return_value.complete.return_value = self.reply("gemini-3.5-flash-lite-001", usage={})
            run_id = launcher.launch("glass_tic_tac_toe", "team", team_id=team["id"],
                                     squad=[1, 7], team_revision=team["revision"])
            launcher.wait(5)
            router.return_value.complete.assert_called_once()
            direct.return_value.complete.assert_called_once()
        summary = json.loads((launcher.root / run_id / "summary.json").read_text())
        self.assertEqual(summary["settings"]["agents"], 2)
        self.assertEqual([agent["name"] for agent in summary["team"]["agents"].values()], ["Rhea", "Moon 8"])
        self.assertEqual(self.teams.get(team["id"])["agents"], roster)
