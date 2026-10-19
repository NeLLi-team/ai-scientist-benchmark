"""Run judge models through the `claude` and `codex` command-line tools.

Each call gets a fresh empty working directory, a minimal environment and no
tools (Claude) or a read-only sandbox with no shell tool (Codex). Both CLIs use
the host's own login. The login files are never read, copied into output or
logged; Codex sees a private HOME that only links to the host's `auth.json`.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = [
    "JUDGES",
    "VERDICT_LABELS",
    "VERDICT_SCHEMA",
    "Judge",
    "JudgeCall",
    "JudgeError",
    "install_refreshed_login",
    "verdict_entries",
]

logger = logging.getLogger(__name__)

VERDICT_LABELS = ("matched", "partial", "missing", "contradicted")
VERDICT_FIELDS = ("assertion_id", "verdict", "candidate_ids", "quote", "reason")
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "assertion_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICT_LABELS)},
                    "candidate_ids": {"type": "array", "items": {"type": "string"}},
                    "quote": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": list(VERDICT_FIELDS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}
ENVIRONMENT_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "CLAUDE_CONFIG_DIR",
)
JUDGE_TIMEOUT_S = 3600
ERROR_TAIL_CHARS = 500
OWNER_RW = 0o600
MAX_LOGIN_BYTES = 1024 * 1024
LOGIN_LOCK_SUFFIX = ".judge-lock"


class JudgeError(RuntimeError):
    """Raised when a judge call fails or returns an answer that is not usable."""


@dataclass(frozen=True, slots=True)
class JudgeCall:
    """What one judge call returned: the answer and what the CLI reported."""

    answer: str
    cli_stdout: str
    model: str | None
    usage: dict[str, int]
    command: tuple[str, ...]
    duration_s: float


class Judge(Protocol):
    """A judge model reached through its own command-line tool."""

    name: str

    def version(self) -> str:
        """The CLI's version line."""
        ...

    def run(self, packet_text: str) -> JudgeCall:
        """Send the packet on stdin and return the call; raise JudgeError on failure."""
        ...


class ClaudeJudge:
    """Sonnet through `claude -p`: no tools, no settings, no MCP, no session file."""

    name = "sonnet"
    executable = "claude"

    def version(self) -> str:
        return _cli_version(self.executable)

    def run(self, packet_text: str) -> JudgeCall:
        command = [
            self.executable,
            "-p",
            "--model",
            "sonnet",
            "--effort",
            "xhigh",
            "--safe-mode",
            "--tools",
            "",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(VERDICT_SCHEMA),
        ]
        with tempfile.TemporaryDirectory(prefix="judge-cwd-") as cwd:
            started = time.monotonic()
            stdout = _run_cli(
                self.name, command, packet_text, Path(cwd), _environment()
            )
        result = _json_object(self.name, stdout)
        if result.get("is_error"):
            raise JudgeError(f"{self.name} reported an error result")
        return JudgeCall(
            answer=_claude_answer(result),
            cli_stdout=stdout,
            model=",".join(sorted(result.get("modelUsage", {}))) or None,
            usage=_int_values(result.get("usage", {})),
            command=tuple(command),
            duration_s=round(time.monotonic() - started, 1),
        )


class CodexJudge:
    """Astra through `codex exec`: read-only sandbox, no shell tool, private HOME."""

    name = "astra"
    executable = "codex"

    def version(self) -> str:
        return _cli_version(self.executable)

    def run(self, packet_text: str) -> JudgeCall:
        host_auth = _host_codex_auth()
        with tempfile.TemporaryDirectory(prefix="judge-") as tmp:
            root = Path(tmp)
            home, cwd = root / "home", root / "cwd"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            cwd.mkdir()
            login = codex_home / "auth.json"
            login.symlink_to(host_auth)
            schema_file, last_message = root / "schema.json", root / "last_message.txt"
            schema_file.write_text(json.dumps(VERDICT_SCHEMA), encoding="utf-8")
            command = self._command(schema_file, last_message)
            environment = {
                **_environment(),
                "HOME": str(home),
                "CODEX_HOME": str(codex_home),
            }
            started = time.monotonic()
            try:
                stdout = _run_cli(self.name, command, packet_text, cwd, environment)
            finally:
                _store_refreshed_login(login, host_auth)
            return JudgeCall(
                answer=_read_last_message(self.name, last_message),
                cli_stdout=stdout,
                model=_rollout_model(codex_home),
                usage=_codex_usage(stdout),
                command=tuple(command),
                duration_s=round(time.monotonic() - started, 1),
            )

    def _command(self, schema_file: Path, last_message: Path) -> list[str]:
        return [
            self.executable,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-m",
            "gpt-6-astra",
            "-c",
            "model_reasoning_effort=xhigh",
            "--disable",
            "shell_tool",
            "--json",
            "--output-schema",
            str(schema_file),
            "-o",
            str(last_message),
        ]


JUDGES: dict[str, Judge] = {
    judge.name: judge for judge in (ClaudeJudge(), CodexJudge())
}


def verdict_entries(judge_name: str, answer: str) -> list[dict]:
    """Parse a judge answer into its list of verdict entries.

    Raises:
        JudgeError: The answer is not a JSON object with a `verdicts` list.
    """
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError as err:
        raise JudgeError(f"{judge_name} answer is not JSON: {err}") from err
    entries = parsed.get("verdicts") if isinstance(parsed, dict) else None
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise JudgeError(f"{judge_name} answer has no list of verdict objects")
    return entries


def _environment() -> dict[str, str]:
    """The few variables a judge CLI needs; API keys and other secrets stay out."""
    return {key: os.environ[key] for key in ENVIRONMENT_KEYS if key in os.environ}


def _cli_version(executable: str) -> str:
    try:
        done = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=True,
            env=_environment(),
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise JudgeError(f"cannot read the version of {executable}: {err}") from err
    return done.stdout.strip()


def _run_cli(
    name: str, command: list[str], packet_text: str, cwd: Path, environment: dict
) -> str:
    """Run one judge call and return its stdout; the packet goes in on stdin."""
    try:
        done = subprocess.run(
            command,
            input=packet_text,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            env=environment,
            timeout=JUDGE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise JudgeError(f"{name} could not run: {err}") from err
    if done.returncode != 0:
        tail = done.stderr.strip()[-ERROR_TAIL_CHARS:]
        raise JudgeError(f"{name} exited with status {done.returncode}: {tail}")
    return done.stdout


def _json_object(name: str, text: str) -> dict:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as err:
        raise JudgeError(f"{name} stdout is not one JSON object: {err}") from err
    if not isinstance(parsed, dict):
        raise JudgeError(f"{name} stdout is not one JSON object")
    return parsed


def _claude_answer(result: dict) -> str:
    """The schema-constrained answer as JSON text, or the plain result text."""
    structured = result.get("structured_output")
    if structured is not None:
        return json.dumps(structured)
    return str(result.get("result", ""))


def _int_values(usage: dict) -> dict[str, int]:
    return {key: value for key, value in usage.items() if isinstance(value, int)}


def _host_codex_auth() -> Path:
    codex_home = os.environ.get("CODEX_HOME") or Path.home() / ".codex"
    login = Path(codex_home) / "auth.json"
    if not login.exists():
        raise JudgeError(f"astra needs a Codex login at {login}; run `codex login`")
    return login


def install_refreshed_login(login: Path, host_auth: Path) -> bool:
    """Install a login file that the CLI replaced during a call over the host login.

    A symlinked `auth.json` is updated in place and needs nothing. If Codex swapped
    it for a new file (a token refresh), the host copy would go stale and the
    provider may revoke it. The new file is installed only if it is valid JSON that
    keeps every key of the current host login and differs from it. Installation
    takes an exclusive lock, stages a uniquely named file in the host directory
    (mode 0600) and renames it over the host file, so the host file is never
    truncated and never seen half written. Returns True if the host file changed.
    """
    if login.is_symlink() or not login.is_file():
        return False
    if login.stat().st_size > MAX_LOGIN_BYTES:
        logger.warning("ignored an oversized replacement login")
        return False
    replacement = login.read_bytes()
    host = host_auth.resolve()
    with _exclusive_lock(host.with_name(host.name + LOGIN_LOCK_SUFFIX)):
        current = _read_login(host)
        if current is None or current == replacement:
            return False
        if not _is_valid_login(replacement, current):
            logger.warning("ignored a replacement login that lost keys or is not JSON")
            return False
        _replace_atomically(host, replacement)
    logger.info(
        "Codex refreshed its login during a judge call; the host copy is updated"
    )
    return True


def _store_refreshed_login(login: Path, host_auth: Path) -> None:
    """Install a refreshed login; report, but never raise, a failure to store it."""
    try:
        install_refreshed_login(login, host_auth)
    except OSError as err:
        logger.error("could not store a refreshed Codex login: %s", err.strerror)


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Generator[None]:
    """Hold an advisory lock on `path` (created if needed) until the block ends."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, OWNER_RW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _read_login(path: Path) -> bytes | None:
    """The bytes of a login file, or None if it is missing, unreadable or oversized."""
    try:
        if path.stat().st_size > MAX_LOGIN_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _is_valid_login(replacement: bytes, current: bytes) -> bool:
    try:
        new, old = json.loads(replacement), json.loads(current)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not (isinstance(new, dict) and isinstance(old, dict) and _keeps_keys(new, old)):
        return False
    return _is_newer(new.get("last_refresh"), old.get("last_refresh"))


def _is_newer(new_refresh: object, old_refresh: object) -> bool:
    """A delayed, older refresh must not replace a newer host login."""
    if old_refresh is None:
        return True
    return isinstance(new_refresh, str) and new_refresh > str(old_refresh)


def _keeps_keys(new: dict, old: dict) -> bool:
    """True if `new` has every key of `old`, with a value of the same type."""
    for key, value in old.items():
        if key not in new:
            return False
        if value is None:
            continue
        if type(new[key]) is not type(value):
            return False
        if isinstance(value, dict) and not _keeps_keys(new[key], value):
            return False
        if isinstance(value, str) and value and not new[key]:
            return False
    return True


def _replace_atomically(target: Path, content: bytes) -> None:
    """Write `content` to a unique file beside `target`, then rename it over it."""
    descriptor, staged_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.judge-"
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), OWNER_RW)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(target)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _read_last_message(name: str, path: Path) -> str:
    if not path.exists():
        raise JudgeError(f"{name} wrote no last message")
    return path.read_text(encoding="utf-8")


def _codex_usage(stdout: str) -> dict[str, int]:
    """Sum the usage of every `turn.completed` event in Codex's JSONL stdout."""
    totals: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        for key, value in _int_values(event.get("usage", {})).items():
            totals[key] = totals.get(key, 0) + value
    return totals


def _rollout_model(codex_home: Path) -> str | None:
    """The model named in the session rollout, where Codex records it."""
    models: list[str] = []
    for rollout in sorted((codex_home / "sessions").glob("**/rollout-*.jsonl")):
        for line in rollout.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            model = event.get("payload", {}).get("model")
            if event.get("type") == "turn_context" and model and model not in models:
                models.append(model)
    return ",".join(models) or None
