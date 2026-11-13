import filecmp
import json
import shutil
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from dojo.models import openrouter_account_rate_rejection
from dojo.storage import RUN_ID, Record, summaries

MAX_COMMENT_LENGTH = 1000


@lru_cache(maxsize=256)
def uncounted_rate_rejections(path, modified, size):
    count = 0
    try:
        with path.open(encoding="utf-8") as source:
            for line in source:
                event = json.loads(line)["event"]
                if (event.get("kind") == "request" and not event.get("generation_id")
                        and not event.get("usage") and openrouter_account_rate_rejection(event.get("error"))):
                    count += 1
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return count


def reconcile_rate_rejections(run, directory):
    metrics = run.get("metrics", {})
    missing = metrics.get("missing_usage", {})
    if (run["settings"].get("backend") != "openrouter" or not any(missing.values())
            or run.get("status") in ("running", "paused")):
        return
    path = directory / "frames.jsonl"
    if path.is_symlink():
        return
    try:
        stat = path.stat()
    except OSError:
        return
    count = uncounted_rate_rejections(path, stat.st_mtime_ns, stat.st_size)
    if count:
        metrics["missing_usage"] = {field: max(0, value - count) for field, value in missing.items()}


def recording_directory(root, run_id):
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Choose a recording.")
    directory = root / run_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("That recording is unavailable.")
    return directory


def recording_lists(root, public_root):
    public = summaries(public_root)
    for run in public:
        reconcile_rate_rejections(run, public_root / run["id"])
        run["comment"] = read_comment(public_root / run["id"])
    published = {run["id"]: run for run in public}
    local = summaries(root)
    for run in local:
        run_id = run["id"]
        directory = root / run_id
        reconcile_rate_rejections(run, directory)
        run["published_locally"] = (directory / ".published-locally").is_file()
        run["published_to_git"] = run_id in published
        run["same_public_replay"] = (run["published_to_git"]
                                     and same_replay(directory, public_root / run_id))
        run["comment"] = published[run_id]["comment"] if run["published_to_git"] else read_comment(directory)
    return {"local": local, "public": public}


def same_replay(local, public):
    left, right = local / "frames.jsonl", public / "frames.jsonl"
    if left.is_symlink() or right.is_symlink():
        return False
    try:
        # A resumed local run can share the ID of an older public snapshot.
        # filecmp caches comparisons while the files' stat signatures match.
        return filecmp.cmp(left, right, shallow=False)
    except OSError:
        return False


def read_comment(directory):
    path = directory / "comment.txt"
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        with path.open(encoding="utf-8") as source:
            return source.read(MAX_COMMENT_LENGTH).strip()
    except (OSError, UnicodeError):
        return ""


def save_comment(root, public_root, run_id, comment):
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Choose a recording.")
    comment = comment.strip()
    if len(comment) > MAX_COMMENT_LENGTH:
        raise ValueError(f"Comments can contain up to {MAX_COMMENT_LENGTH} characters.")
    if "\x00" in comment:
        raise ValueError("Enter a text comment.")
    try:
        comment.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("Enter a valid text comment.") from error
    public = public_root / run_id
    directory = recording_directory(public_root if (public / "summary.json").is_file() else root, run_id)
    summary = directory / "summary.json"
    if summary.is_symlink() or not summary.is_file():
        raise ValueError("That recording is unavailable.")
    path = directory / "comment.txt"
    if not comment:
        path.unlink(missing_ok=True)
        return comment
    temporary = None
    try:
        with NamedTemporaryFile(dir=directory, prefix=".comment-", delete=False, mode="w", encoding="utf-8") as file:
            temporary = Path(file.name)
            file.write(comment + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return comment


def publish(root, public_root, run_id, destination, enabled):
    directory = recording_directory(root, run_id)
    with Record.acquire(directory):
        summary_path = directory / "summary.json"
        replay = directory / "frames.jsonl"
        if any(path.is_symlink() or not path.is_file() for path in (summary_path, replay)):
            raise ValueError("This recording is missing its summary or replay.")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("id") != run_id or summary.get("status") in (None, "running", "paused"):
            raise ValueError("Finish or stop the run before publishing it.")
        if enabled and summary.get("settings", {}).get("model") == "demo":
            raise ValueError("Only model recordings can be published.")
        if destination == "local":
            marker = directory / ".published-locally"
            if enabled:
                marker.touch()
            else:
                marker.unlink(missing_ok=True)
            return
        if destination != "git" or enabled is not True:
            raise ValueError("Choose a publication destination.")
        public_root.mkdir(parents=True, exist_ok=True)
        target = public_root / run_id
        try:
            target.mkdir()
        except FileExistsError as error:
            raise ValueError("A Git recording with this ID already exists.") from error
        try:
            shutil.copyfile(replay, target / "frames.jsonl")
            comment = read_comment(directory)
            if comment:
                (target / "comment.txt").write_text(comment + "\n", encoding="utf-8")
            summary.pop("resumable", None)
            summary.pop("profile_name", None)
            summary_file = target / "summary.tmp"
            summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            summary_file.replace(target / "summary.json")
        except BaseException:
            shutil.rmtree(target)
            raise


def delete_unpublished(root, public_root, ids):
    deleted, skipped = [], []
    for id in ids:
        try:
            delete_recording(root, public_root, id, unpublished_only=True)
        except (ValueError, OSError):
            skipped.append(id)
        else:
            deleted.append(id)
    return {"deleted": deleted, "skipped": skipped}


def delete_recording(root, public_root, run_id, *, unpublished_only=False):
    directory = recording_directory(root, run_id)
    with Record.acquire(directory):
        if (public_root / run_id).exists():
            raise ValueError("Git-published recordings cannot be deleted here.")
        if unpublished_only and (directory / ".published-locally").is_file():
            raise ValueError("This recording is published locally.")
        summary_path = directory / "summary.json"
        if summary_path.is_symlink() or not summary_path.is_file():
            raise ValueError("This recording is missing its summary.")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("id") != run_id or summary.get("status") in (None, "running", "paused"):
            raise ValueError("Finish or stop the run before deleting it.")
        removed = root / (".deleting-" + uuid4().hex)
        directory.rename(removed)
    shutil.rmtree(removed)
