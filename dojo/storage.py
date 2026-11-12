import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{8}")


def now():
    return datetime.now(timezone.utc)


class Record:
    def __init__(self, root, summary):
        self.id = now().strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        self.directory = root / self.id
        self.directory.mkdir(parents=True)
        self.lock = self.acquire(self.directory)
        self.summary = {"id": self.id, **summary}
        try:
            self.save_summary()
            self.index = 0
            self.stream = (self.directory / "frames.jsonl").open("w", encoding="utf-8")
        except BaseException:
            self.lock.close()
            raise

    def save_summary(self):
        temporary = self.directory / "summary.tmp"
        temporary.write_text(json.dumps(self.summary, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.directory / "summary.json")

    def checkpoint(self, state):
        temporary = self.directory / "checkpoint.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"next_index": self.index, **state}, stream, separators=(",", ":"))
        temporary.replace(self.directory / "checkpoint.json")

    @staticmethod
    def acquire(directory):
        lock = (directory / ".writer-lock").open("a+b")
        try:
            if os.name == "nt":
                from msvcrt import LK_NBLCK, locking
                locking(lock.fileno(), LK_NBLCK, 1)
            else:
                from fcntl import LOCK_EX, LOCK_NB, flock
                flock(lock, LOCK_EX | LOCK_NB)
        except OSError as error:
            lock.close()
            raise BlockingIOError("This recording is open in another viewer.") from error
        return lock

    @classmethod
    def reopen(cls, directory, summary, index, lock):
        record = cls.__new__(cls)
        record.id = summary["id"]
        record.directory = directory
        record.summary = summary
        record.index = index
        record.lock = lock
        record.stream = (directory / "frames.jsonl").open("a", encoding="utf-8")
        return record

    def frame(self, data):
        self.stream.write(json.dumps({"index": self.index, **data}, separators=(",", ":")) + "\n")
        self.stream.flush()
        self.index += 1

    def close(self):
        try:
            self.stream.close()
        finally:
            self.lock.close()


def summaries(root):
    records = []
    for path in root.glob("*/summary.json"):
        if RUN_ID.fullmatch(path.parent.name) and not path.parent.is_symlink() and not path.is_symlink():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if (isinstance(record, dict) and record.get("id") == path.parent.name
                    and isinstance(record.get("started_at"), str) and isinstance(record.get("settings"), dict)):
                records.append(record)
    return sorted(records, key=lambda record: record["started_at"], reverse=True)


def frames(directory: Path, offset: int, limit=100):
    batch = []
    path = directory / "frames.jsonl"
    if not path.is_file():
        return batch, offset
    with path.open("rb") as source:
        source.seek(offset)
        for _ in range(limit):
            line = source.readline()
            if not line.endswith(b"\n"):
                break
            batch.append(json.loads(line))
            offset = source.tell()
    return batch, offset
