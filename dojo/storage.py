import json
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
        self.summary = {"id": self.id, **summary}
        self.save_summary()
        self.index = 0
        self.stream = (self.directory / "frames.jsonl").open("w", encoding="utf-8")

    def save_summary(self):
        temporary = self.directory / "summary.tmp"
        temporary.write_text(json.dumps(self.summary, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.directory / "summary.json")

    def frame(self, data):
        self.stream.write(json.dumps({"index": self.index, **data}, separators=(",", ":")) + "\n")
        self.stream.flush()
        self.index += 1

    def close(self):
        self.stream.close()


def summaries(root):
    records = []
    for path in root.glob("*/summary.json"):
        if RUN_ID.fullmatch(path.parent.name):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(record, dict) and isinstance(record.get("started_at"), str):
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
