import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock

from dojo.config import Settings
from dojo.models import Reply
from dojo.publication import delete_recording, publish, recording_lists, save_comment
from dojo.runner import Run


class PublicationTests(unittest.TestCase):
    def test_comments_follow_a_run_into_git_and_remain_editable_after_publication(self):
        with TemporaryDirectory() as directory:
            local = Path(directory) / "runs"
            public = Path(directory) / "recordings"
            backend = Mock()
            backend.complete.return_value = Reply({"tool_calls": []}, {})
            run = Run(Settings(model="example/model", rounds=1), local, Event(), backend)
            run.execute()
            run_id = run.record.id
            self.assertEqual(save_comment(local, public, run_id, "  First note  "), "First note")
            publish(local, public, run_id, "git", True)
            target = public / run_id
            self.assertEqual((target / "comment.txt").read_text(), "First note\n")
            original = {name: (target / name).read_bytes() for name in ("summary.json", "frames.jsonl")}
            with self.assertRaisesRegex(ValueError, "1000 characters"):
                save_comment(local, public, run_id, "x" * 1001)
            self.assertEqual(save_comment(local, public, run_id, "New note\n第二行"), "New note\n第二行")
            lists = recording_lists(local, public)
            self.assertEqual(lists["local"][0]["comment"], lists["public"][0]["comment"])
            self.assertEqual(lists["public"][0]["comment"], "New note\n第二行")
            self.assertEqual({name: (target / name).read_bytes() for name in original}, original)
            save_comment(local / "another-checkout", public, run_id, "")
            self.assertFalse((target / "comment.txt").exists())
            self.assertEqual(recording_lists(local, public)["local"][0]["comment"], "")

    def test_local_and_git_publications_are_independent_and_git_copy_is_immutable(self):
        with TemporaryDirectory() as directory:
            local = Path(directory) / "runs"
            public = Path(directory) / "recordings"
            backend = Mock()
            backend.complete.return_value = Reply({"tool_calls": []}, {})
            run = Run(Settings(level="word_jumble", model="example/model", seed=42, rounds=1),
                      local, Event(), backend)
            run_id = run.record.id
            with self.assertRaises(BlockingIOError):
                publish(local, public, run_id, "git", True)
            run.execute()
            publish(local, public, run_id, "local", True)
            self.assertTrue(recording_lists(local, public)["local"][0]["published_locally"])
            self.assertEqual(recording_lists(local, public)["public"], [])

            publish(local, public, run_id, "git", True)
            target = public / run_id
            self.assertEqual({file.name for file in target.iterdir()}, {"frames.jsonl", "summary.json"})
            self.assertEqual((target / "frames.jsonl").read_bytes(),
                             (run.record.directory / "frames.jsonl").read_bytes())
            self.assertNotIn("resumable", json.loads((target / "summary.json").read_text()))
            with self.assertRaisesRegex(ValueError, "already exists"):
                publish(local, public, run_id, "git", True)
            publish(local, public, run_id, "local", False)
            lists = recording_lists(local, public)
            self.assertFalse(lists["local"][0]["published_locally"])
            self.assertTrue(lists["local"][0]["published_to_git"])
            self.assertEqual(lists["public"][0]["id"], run_id)
            with self.assertRaisesRegex(ValueError, "Git-published"):
                delete_recording(local, public, run_id)
            self.assertTrue(run.record.directory.exists())

    def test_delete_removes_a_finished_local_recording_and_its_publication_marker(self):
        with TemporaryDirectory() as directory:
            local = Path(directory) / "runs"
            public = Path(directory) / "recordings"
            run = Run(Settings(rounds=1, demo_delay=0), local, Event())
            with self.assertRaises(BlockingIOError):
                delete_recording(local, public, run.record.id)
            run.execute()
            with self.assertRaisesRegex(ValueError, "Only model recordings"):
                publish(local, public, run.record.id, "local", True)
            (run.record.directory / ".published-locally").touch()
            delete_recording(local, public, run.record.id)
            self.assertEqual(list(local.iterdir()), [])
