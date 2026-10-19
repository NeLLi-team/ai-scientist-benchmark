#!/usr/bin/env python3
"""Stand-in for the codex and claude CLIs, used by the benchmark-run tests.

The runner mounts this file over the real agent binary inside the image. It
reads `key=value` lines from stdin (the participant prompt) and acts on `mode`:
`probe` records what the container exposes, `sleep` idles, `download` pulls
bytes from a URL. The other modes imitate a hostile participant that tampers
with its credential files or prints them. No model is called.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import sys
import time
import urllib.request
from pathlib import Path

WORK = Path("/benchmark/work")
FAKE_VERSION = "0.0.1-stub"
REFRESHED_SECRET = "SENTINEL-REFRESHED-ACCESS-TOKEN-9876543210zyxwvu"  # noqa: S105  # fake test value


def main() -> int:
    """Emit a fake transcript, then run the mode named in the prompt."""
    if "--version" in sys.argv[1:]:
        print(f"stub-agent {FAKE_VERSION}")
        return 0
    settings = parse_settings(sys.stdin.read())
    agent = Path(sys.argv[0]).name
    emit_start(agent)
    write_manuscript()
    MODES[settings.get("mode", "probe")](settings)
    emit_end(agent)
    return int(settings.get("exit", "0"))


def parse_settings(prompt: str) -> dict[str, str]:
    """Read `key=value` lines; repeated `hidden` keys are joined by commas."""
    settings: dict[str, str] = {}
    for line in prompt.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        previous = settings.get(key)
        settings[key] = f"{previous},{value}" if previous and key == "hidden" else value
    return settings


def emit(event: dict) -> None:
    """Print one JSON line and flush so a killed run keeps it."""
    print(json.dumps(event), flush=True)


def emit_start(agent: str) -> None:
    """Print the first transcript events for the given agent."""
    if agent == "claude":
        emit({"type": "system", "subtype": "init", "model": "claude-stub-1"})
    else:
        emit({"type": "thread.started", "thread_id": "stub-thread"})


def emit_end(agent: str) -> None:
    """Print the closing usage event for the given agent."""
    if agent == "claude":
        usage = {"input_tokens": 100, "output_tokens": 20}
        emit({"type": "result", "subtype": "success", "usage": usage})
        return
    usage = {"input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 20}
    emit({"type": "turn.completed", "usage": usage})


def write_manuscript() -> None:
    """Write a deliverable so the runner can hash it."""
    deliverable = WORK / "deliverable"
    deliverable.mkdir(parents=True, exist_ok=True)
    (deliverable / "manuscript.md").write_text("# Stub manuscript\n", encoding="utf-8")


def try_write(path: str) -> str:
    """Return the errno name of a failed write, or `ok`."""
    target = Path(path) / ".probe_write"
    try:
        target.write_text("x", encoding="utf-8")
    except OSError as err:
        return errno.errorcode.get(err.errno, str(err.errno))
    target.unlink()
    return "ok"


def list_dir(path: str) -> list[str]:
    """Sorted entries of a directory, or an empty list when it is missing."""
    try:
        return sorted(entry.name for entry in Path(path).iterdir())
    except OSError:
        return []


def credential_files() -> dict[str, dict]:
    """Mode, owner and size of the credential files under HOME (no contents)."""
    home = Path(os.environ["HOME"])
    found = {}
    for relative in (".codex/auth.json", ".claude/.credentials.json"):
        path = home / relative
        if path.exists():
            info = path.stat()
            found[relative] = {
                "mode": oct(info.st_mode & 0o777),
                "uid": info.st_uid,
                "bytes": info.st_size,
            }
    return found


def write_probe(settings: dict[str, str]) -> None:
    """Record what the container shows to the agent in `probe.json`."""
    hidden = [path for path in settings.get("hidden", "").split(",") if path]
    probe = {
        "argv": sys.argv[1:],
        "uid": os.getuid(),
        "gid": os.getgid(),
        "cwd": str(Path.cwd()),
        "env_home": os.environ.get("HOME"),
        "writes": {
            "input": try_write("/benchmark/input"),
            "work": try_write("/benchmark/work"),
            "home": try_write(os.environ["HOME"]),
            "tmp": try_write("/tmp"),  # probing the container's /tmp mount
            "db": try_write("/media/shared-expansion/db"),
            "gv_exports": try_write("/media/shared-expansion/db/gv-exports"),
        },
        "input_files": list_dir("/benchmark/input"),
        "db_entries": list_dir("/media/shared-expansion/db"),
        "gv_exports_entries": list_dir("/media/shared-expansion/db/gv-exports"),
        "home_dirs": list_dir("/home"),
        "hidden_visible": {path: Path(path).exists() for path in hidden},
        "home_entries": list_dir(os.environ["HOME"]),
        "credential_files": credential_files(),
    }
    (WORK / "probe.json").write_text(json.dumps(probe), encoding="utf-8")


def credential_path() -> Path:
    """The credentials file the runner copied into HOME for this agent."""
    home = Path(os.environ["HOME"])
    codex = home / ".codex" / "auth.json"
    return codex if codex.exists() else home / ".claude" / ".credentials.json"


def mode_sleep(settings: dict[str, str]) -> None:
    """Idle for `seconds`."""
    time.sleep(float(settings["seconds"]))


def mode_download_entry(settings: dict[str, str]) -> None:
    """Pull bytes from `url`."""
    download(settings["url"], float(settings.get("pause_s", "0.05")))


def mode_redirect_cleanup(settings: dict[str, str]) -> None:
    """Move the credential directory away and put a symlink to `outside` there."""
    home = Path(os.environ["HOME"])
    directory = credential_path().parent
    saved = home / (directory.name + ".saved")
    directory.rename(saved)
    directory.symlink_to(settings["outside"], target_is_directory=True)


def mode_unreadable_file(_settings: dict[str, str]) -> None:
    """Remove all permissions from the credentials file."""
    credential_path().chmod(0)


def mode_unreadable_dir(_settings: dict[str, str]) -> None:
    """Remove all permissions from the credentials directory."""
    credential_path().parent.chmod(0)


def mode_credential_logging(_settings: dict[str, str]) -> None:
    """Print the credentials file into the transcript."""
    emit(
        {
            "type": "item.completed",
            "text": credential_path().read_text(encoding="utf-8"),
        }
    )


def mode_refresh_logging(_settings: dict[str, str]) -> None:
    """Replace the credentials, as a token refresh would, and print the new value."""
    new = json.dumps({"tokens": {"access_token": REFRESHED_SECRET}})
    credential_path().write_text(new, encoding="utf-8")
    emit({"type": "item.completed", "text": new})


def mode_session_logging(_settings: dict[str, str]) -> None:
    """Write the credentials into a native session log under HOME."""
    sessions = Path(os.environ["HOME"]) / ".codex" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    text = credential_path().read_text(encoding="utf-8")
    (sessions / "rollout-test.jsonl").write_text(
        json.dumps({"type": "item", "text": text}) + "\n", encoding="utf-8"
    )


def mode_copy_credentials(_settings: dict[str, str]) -> None:
    """Copy the credentials into work and rename the original inside HOME."""
    source = credential_path()
    (WORK / "stolen.txt").write_bytes(source.read_bytes())
    source.rename(source.parent / "renamed.bak")


def mode_refresh_lock_home(settings: dict[str, str]) -> None:
    """Refresh and print the credentials, then remove all permissions from HOME."""
    mode_refresh_logging(settings)
    Path(os.environ["HOME"]).chmod(0)


def mode_oversize_credentials(_settings: dict[str, str]) -> None:
    """Replace the credentials with a sparse file far larger than any credential."""
    with credential_path().open("r+b") as handle:
        handle.truncate(300 * 1024 * 1024)


def mode_symlink_deliverable(settings: dict[str, str]) -> None:
    """Replace the deliverable directory with a symlink to a host directory."""
    shutil.rmtree(WORK / "deliverable")
    (WORK / "deliverable").symlink_to(settings["outside"], target_is_directory=True)


def mode_secret_filename(_settings: dict[str, str]) -> None:
    """Write a session log whose file name is the credential value."""
    document = json.loads(credential_path().read_text(encoding="utf-8"))
    secret = max(document["tokens"].values(), key=len)
    sessions = Path(os.environ["HOME"]) / ".codex" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"rollout-{secret}.jsonl").write_text(secret, encoding="utf-8")


def mode_freeze_timeout(settings: dict[str, str]) -> None:
    """Stop the parent `timeout` process so the in-container limit cannot fire."""
    os.kill(os.getppid(), signal.SIGSTOP)
    (WORK / "frozen").write_text("1", encoding="utf-8")
    time.sleep(float(settings.get("seconds", "600")))


MODES = {
    "probe": write_probe,
    "sleep": mode_sleep,
    "download": mode_download_entry,
    "redirect_cleanup": mode_redirect_cleanup,
    "unreadable_file": mode_unreadable_file,
    "unreadable_dir": mode_unreadable_dir,
    "credential_logging": mode_credential_logging,
    "refresh_logging": mode_refresh_logging,
    "session_logging": mode_session_logging,
    "copy_credentials": mode_copy_credentials,
    "refresh_lock_home": mode_refresh_lock_home,
    "oversize_credentials": mode_oversize_credentials,
    "symlink_deliverable": mode_symlink_deliverable,
    "secret_filename": mode_secret_filename,
    "freeze_timeout": mode_freeze_timeout,
}


def download(url: str, pause_s: float) -> None:
    """Read from `url` in chunks until the watchdog stops the container."""
    with urllib.request.urlopen(url) as response:  # test URL on the docker bridge
        while response.read(262144):
            time.sleep(pause_s)


if __name__ == "__main__":
    raise SystemExit(main())
