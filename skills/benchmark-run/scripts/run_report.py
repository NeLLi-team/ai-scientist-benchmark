"""Read a participant transcript and collect the facts that go into run.json.

Covers token usage (Codex `turn.completed`, Claude `result`), the model the CLI
reported, the deliverable hashes, and a credential-exposure count. Nothing here
prints or stores a credential value.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Iterable, Iterator
from pathlib import Path

DELIVERABLE_FILES = ("manuscript.md", "manuscript.pdf")
CLAUDE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
CODEX_TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
MIN_SECRET_LENGTH = 20
HASH_CHUNK_BYTES = 1 << 20


class JsonlStream:
    """Iterate the JSON objects of a JSONL file; count lines that do not parse."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.unparsed_lines = 0

    def __iter__(self) -> Iterator[dict]:
        """Yield each parsed object.

        A killed run can leave a cut last line, and a run that failed before
        the container started leaves no file; both are tolerated.
        """
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    self.unparsed_lines += 1
                    continue
                if isinstance(event, dict):
                    yield event


def parse_usage(setup: str, transcript: Path) -> dict:
    """Total the token usage a transcript reports.

    The result names its `source`: `turn.completed` or `result` when the CLI
    reported totals, `assistant_messages` when a Claude run was cut before its
    `result` event, and `none` when no usage was found.
    """
    stream = JsonlStream(transcript)
    usage = _codex_usage(stream) if setup == "codex" else _claude_usage(stream)
    usage["unparsed_lines"] = stream.unparsed_lines
    return usage


def _codex_usage(events: Iterable[dict]) -> dict:
    totals = dict.fromkeys(CODEX_TOKEN_KEYS, 0)
    turns = 0
    for event in events:
        if event.get("type") != "turn.completed":
            continue
        turns += 1
        for key, value in event.get("usage", {}).items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return {
        **totals,
        "turns_completed": turns,
        "source": "turn.completed" if turns else "none",
    }


def _claude_usage(events: Iterable[dict]) -> dict:
    result: dict | None = None
    by_message: dict[str, dict] = {}
    for event in events:
        if event.get("type") == "result":
            result = event
        elif event.get("type") == "assistant":
            message = event.get("message", {})
            by_message[message.get("id", "")] = message.get("usage", {})
    if result is not None:
        return _claude_result_usage(result)
    totals = {
        key: sum(u.get(key, 0) for u in by_message.values())
        for key in CLAUDE_TOKEN_KEYS
    }
    return {**totals, "source": "assistant_messages" if by_message else "none"}


def _claude_result_usage(result: dict) -> dict:
    usage = result.get("usage", {})
    reported = {key: usage.get(key, 0) for key in CLAUDE_TOKEN_KEYS}
    return {
        **reported,
        "total_cost_usd": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "is_error": result.get("is_error"),
        "models_used": sorted(result.get("modelUsage", {})),
        "source": "result",
    }


def reported_models(setup: str, transcript: Path, home: Path) -> dict:
    """List the models and efforts the CLI itself reported.

    Claude prints its model in the `system` init event. Codex does not print it
    in the exec JSONL, so the session rollout under `~/.codex/sessions` is read.
    """
    if setup == "claude":
        models = _unique(
            event.get("model")
            for event in JsonlStream(transcript)
            if event.get("type") == "system" and event.get("subtype") == "init"
        )
        return {
            "models": models,
            "efforts": [],
            "source": "transcript" if models else "none",
        }
    rollouts = sorted((home / ".codex" / "sessions").glob("**/rollout-*.jsonl"))
    contexts = [
        event.get("payload", {})
        for rollout in rollouts
        for event in JsonlStream(rollout)
        if event.get("type") == "turn_context"
    ]
    models = _unique(context.get("model") for context in contexts)
    efforts = _unique(context.get("effort") for context in contexts)
    return {
        "models": models,
        "efforts": efforts,
        "source": "codex session rollout" if models else "none",
    }


def _unique(values: Iterable[str | None]) -> list[str]:
    """Distinct non-empty values in first-seen order."""
    return list(dict.fromkeys(value for value in values if value))


def deliverable_report(work_dir: Path) -> dict:
    """Presence, size and SHA-256 of the expected deliverable files.

    A symlinked `deliverable` directory or file counts as absent: the participant
    controls it, and following it would hash a host file.
    """
    directory = work_dir / "deliverable"
    report = {}
    for name in DELIVERABLE_FILES:
        path = directory / name
        if _is_real_directory(directory) and _is_real_file(path):
            report[name] = {
                "present": True,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        else:
            report[name] = {"present": False}
    return report


def _is_real_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _is_real_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, record: dict) -> None:
    """Write `record` as indented JSON via a temporary file and a rename."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
