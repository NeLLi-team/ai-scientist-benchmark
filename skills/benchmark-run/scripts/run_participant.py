#!/usr/bin/env python3
"""Run one native Codex CLI or Claude Code participant inside a Docker container.

One run is a setup (codex or claude), a case directory and a run directory. The
container is the sandbox: it sees only the mounts built here, runs as the host
user, and a watchdog stops it at the wall-time and download limits. The run
directory ends up with the transcript, stderr, the work tree and `run.json`.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import logging
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import container_watchdog
import run_credentials
import run_report
from container_watchdog import WatchdogLimits, WatchdogResult

logger = logging.getLogger("run_participant")

DEFAULT_IMAGE = "benchmark-participant:2026-10-19"
DEFAULT_DB_DIR = Path("/media/shared-expansion/db")
DB_TARGET = "/media/shared-expansion/db"
CONTAINER_HOME = "/home/participant"
INPUT_TARGET = "/benchmark/input"
WORK_TARGET = "/benchmark/work"
CONTAINER_ENV = (
    f"HOME={CONTAINER_HOME}",
    "USER=participant",
    "LOGNAME=participant",
    "LANG=C.UTF-8",
    "TERM=dumb",
    "DISABLE_AUTOUPDATER=1",
)
SCHEMA_VERSION = 1
DEFAULT_REPEAT = 3
CONTAINER_KILL_AFTER_S = 30

CODEX_CONFIG = """\
model = "gpt-6-astra"
model_reasoning_effort = "xhigh"
approval_policy = "never"
sandbox_mode = "danger-full-access"
check_for_update_on_startup = false
"""
CLAUDE_STATE = '{"hasCompletedOnboarding": true}\n'
CLAUDE_SETTINGS = '{"skipDangerousModePermissionPrompt": true}\n'


class RunnerError(ValueError):
    """Raised when a run cannot start or its inputs are unusable."""


@dataclass(frozen=True, slots=True)
class SetupSpec:
    """One participant setup: the CLI, its requested model, and its home files."""

    name: str
    binary: str
    args: tuple[str, ...]
    model: str
    effort: str
    credentials_relpath: str
    config_files: Mapping[str, str]

    def command(self) -> list[str]:
        """The command line run inside the container."""
        return [self.binary, *self.args]


SETUPS: dict[str, SetupSpec] = {
    "codex": SetupSpec(
        name="codex",
        binary="/opt/codex/bin/codex",
        args=(
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
            "-m",
            "gpt-6-astra",
            "-c",
            "model_reasoning_effort=xhigh",
        ),
        model="gpt-6-astra",
        effort="xhigh",
        credentials_relpath=".codex/auth.json",
        config_files={".codex/config.toml": CODEX_CONFIG},
    ),
    "claude": SetupSpec(
        name="claude",
        binary="/opt/claude/bin/claude",
        args=(
            "-p",
            "--model",
            "opus",
            "--effort",
            "xhigh",
            "--dangerously-skip-permissions",
            "--output-format",
            "stream-json",
            "--verbose",
        ),
        model="opus",
        effort="xhigh",
        credentials_relpath=".claude/.credentials.json",
        config_files={
            ".claude.json": CLAUDE_STATE,
            ".claude/settings.json": CLAUDE_SETTINGS,
        },
    ),
}


@dataclass(frozen=True, slots=True)
class Limits:
    """Run limits: watchdog thresholds and container resources."""

    wall_s: float = 10_800
    rx_bytes: int = 10_000_000_000
    poll_interval_s: float = 30
    stop_grace_s: int = 20
    cpus: int = 16
    memory: str = "64g"
    pids_limit: int = 4096
    shm_size: str = "4g"
    max_failed_reads: int = 3
    startup_grace_s: float = 120
    container_timeout_margin_s: int = 60
    reaper_margin_s: int = 120

    def watchdog(self) -> WatchdogLimits:
        """The subset the watchdog enforces."""
        return WatchdogLimits(
            wall_s=self.wall_s,
            rx_bytes=self.rx_bytes,
            poll_interval_s=self.poll_interval_s,
            max_failed_reads=self.max_failed_reads,
            startup_grace_s=self.startup_grace_s,
        )

    def container_timeout_s(self) -> int:
        """Seconds after which the container ends the agent by itself.

        This backs up the watchdog: if the runner dies, the container still
        stops at the wall limit plus a margin instead of running on.
        """
        return math.ceil(self.wall_s) + self.container_timeout_margin_s


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Everything the container command depends on."""

    setup: SetupSpec
    run_dir: Path
    image: str
    db_dir: Path
    gv_exports_filtered: Path
    limits: Limits
    agent_override: Path | None
    uid: int
    gid: int

    @property
    def input_dir(self) -> Path:
        """Read-only copy of the participant starting data."""
        return self.run_dir / "input"

    @property
    def work_dir(self) -> Path:
        """The agent's workspace; holds the deliverable."""
        return self.run_dir / "work"

    @property
    def home_dir(self) -> Path:
        """Backs the container HOME; holds the credentials copy during the run."""
        return self.run_dir / "home"

    @property
    def tmp_dir(self) -> Path:
        """Backs /tmp so scratch files do not fill the Docker layer."""
        return self.run_dir / "tmp"


@dataclass(frozen=True, slots=True)
class CaseFiles:
    """The two case files a run reads: the prompt and the starting-data dir."""

    prompt: Path
    input_dir: Path


@dataclass(frozen=True, slots=True)
class Mount:
    """A bind mount from the host into the container."""

    source: Path
    target: str
    read_only: bool

    def spec(self) -> str:
        """The value for `docker run --mount`."""
        if "," in str(self.source):
            raise RunnerError(f"mount source must not contain a comma: {self.source}")
        suffix = ",readonly" if self.read_only else ""
        return f"type=bind,src={self.source},dst={self.target}{suffix}"


@dataclass(frozen=True, slots=True)
class Execution:
    """What happened when the container ran."""

    command: list[str]
    started: datetime
    ended: datetime
    outcome: WatchdogResult
    container: dict
    reaper: dict | None = None


@dataclass(frozen=True, slots=True)
class Reaper:
    """The detached host process that stops the container at a deadline."""

    process: subprocess.Popen
    deadline_utc: str


def mounts(config: RunConfig) -> list[Mount]:
    """The complete mount list; the filtered gv-exports sits over the database."""
    listed = [
        Mount(config.input_dir, INPUT_TARGET, read_only=True),
        Mount(config.work_dir, WORK_TARGET, read_only=False),
        Mount(config.home_dir, CONTAINER_HOME, read_only=False),
        Mount(config.tmp_dir, "/tmp", read_only=False),  # container path
        Mount(config.db_dir, DB_TARGET, read_only=True),
        Mount(config.gv_exports_filtered, f"{DB_TARGET}/gv-exports", read_only=True),
    ]
    if config.agent_override is not None:
        listed.append(Mount(config.agent_override, config.setup.binary, read_only=True))
    return listed


def build_docker_command(config: RunConfig, container_name: str) -> list[str]:
    """Build the `docker run` command line for one participant."""
    limits = config.limits
    command = [
        "docker", "run", "-i", "--init",
        "--name", container_name,
        "--user", f"{config.uid}:{config.gid}",
        "--hostname", "participant",
        "--network", "bridge",
        "--log-driver", "none",
        "--cpus", str(limits.cpus),
        "--memory", limits.memory,
        "--memory-swap", limits.memory,
        "--pids-limit", str(limits.pids_limit),
        "--shm-size", limits.shm_size,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--workdir", WORK_TARGET,
    ]  # fmt: skip
    for assignment in CONTAINER_ENV:
        command += ["-e", assignment]
    for mount in mounts(config):
        command += ["--mount", mount.spec()]
    timeout = [
        "timeout",
        f"--kill-after={CONTAINER_KILL_AFTER_S}",
        str(limits.container_timeout_s()),
    ]
    return [*command, config.image, *timeout, *config.setup.command()]


def stage_input(source: Path, target: Path) -> list[Path]:
    """Copy the participant starting data and make each file read-only."""
    if not source.is_dir():
        raise RunnerError(f"starting-data directory not found: {source}")
    shutil.copytree(source, target)
    staged = sorted(path for path in target.rglob("*") if path.is_file())
    for path in staged:
        path.chmod(0o444)
    return staged


def write_agent_home(
    setup: SetupSpec, home: Path, credentials_src: Path
) -> run_credentials.CredentialCopy:
    """Write the setup's minimal config and a 0600 copy of its credentials."""
    for relative, text in setup.config_files.items():
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    try:
        return run_credentials.copy_credentials(
            credentials_src, home, setup.credentials_relpath
        )
    except (OSError, ValueError) as err:
        raise RunnerError(str(err)) from err


def check_run_dir(run_dir: Path) -> None:
    """Refuse a run directory that is not empty, or that still holds credentials.

    A runner killed with SIGKILL leaves its credential copy behind. Starting a
    new run in that directory would hide it, so the message names the problem.
    """
    if not run_dir.exists():
        return
    leftovers = run_credentials.credential_files_in(run_dir)
    if leftovers:
        raise RunnerError(
            f"run directory {run_dir} still holds credential copies "
            f"({', '.join(leftovers)}); delete them and the directory first"
        )
    if any(run_dir.iterdir()):
        raise RunnerError(f"run directory {run_dir} is not empty")


def prepare_run_dir(config: RunConfig) -> None:
    """Create the run tree and confirm its filesystem supports modes and symlinks."""
    config.run_dir.mkdir(parents=True, exist_ok=True)
    probe, link = config.run_dir / ".fs_probe", config.run_dir / ".fs_probe_link"
    try:
        probe.write_text("", encoding="utf-8")
        probe.chmod(0o600)
        link.symlink_to(probe.name)
        probe_mode = probe.stat().st_mode & 0o777
    except OSError as err:
        raise RunnerError(f"run directory needs a POSIX filesystem: {err}") from err
    finally:
        probe.unlink(missing_ok=True)
        link.unlink(missing_ok=True)
    if probe_mode != 0o600:
        raise RunnerError(f"run directory {config.run_dir} cannot hold mode 0600 files")
    for directory in (config.work_dir, config.tmp_dir):
        directory.mkdir()
    config.home_dir.mkdir(mode=0o700)


def check_host(config: RunConfig) -> str:
    """Confirm Docker answers, the image exists and the mount sources exist."""
    for name, path in (
        ("database", config.db_dir),
        ("filtered gv-exports", config.gv_exports_filtered),
    ):
        if not path.is_dir():
            raise RunnerError(f"{name} directory not found: {path}")
    if config.agent_override is not None and not config.agent_override.is_file():
        raise RunnerError(f"agent override not found: {config.agent_override}")
    done = _docker(["version", "--format", "{{.Server.Version}}"])
    if done.returncode != 0:
        raise RunnerError("cannot reach the Docker daemon")
    if _docker(["image", "inspect", config.image]).returncode != 0:
        raise RunnerError(
            f"image {config.image} not found; build it with docker/build.sh"
        )
    return done.stdout.strip()


def read_cli_version(config: RunConfig) -> str:
    """Ask the CLI in the image for its version; the first output line is kept."""
    command = [
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        f"{config.uid}:{config.gid}",
        "-e",
        f"HOME={CONTAINER_HOME}",
    ]
    if config.agent_override is not None:
        command += [
            "--mount",
            Mount(config.agent_override, config.setup.binary, read_only=True).spec(),
        ]
    command += ["--entrypoint", config.setup.binary, config.image, "--version"]
    done = _docker(command)
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        raise RunnerError(f"{config.setup.binary} --version failed in the image")
    return lines[0].strip()


def describe_image(image: str) -> dict:
    """Image id, creation time and the org.benchmark.* labels pinned at build."""
    done = _docker(["image", "inspect", "--format", "{{json .}}", image])
    info = _loads(done.stdout)
    labels = info.get("Config", {}).get("Labels") or {}
    return {
        "tag": image,
        "id": info.get("Id"),
        "created": info.get("Created"),
        "repo_digests": info.get("RepoDigests", []),
        "labels": {k: v for k, v in labels.items() if k.startswith("org.benchmark.")},
    }


def inspect_container(name: str) -> dict | None:
    """Exit code, OOM flag and timestamps of a finished container, if it exists."""
    done = _docker(["container", "inspect", "--format", "{{json .State}}", name])
    if done.returncode != 0:
        return None
    state = _loads(done.stdout)
    return {
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled"),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
    }


def reaper_command(
    config: RunConfig, container_name: str, *, now: float | None = None
) -> list[str]:
    """The command line of the host-side reaper for one container."""
    limits = config.limits
    deadline = (
        (time.time() if now is None else now) + limits.wall_s + limits.reaper_margin_s
    )
    return [
        sys.executable,
        str(Path(__file__).with_name("container_reaper.py")),
        "--container",
        container_name,
        "--deadline-epoch",
        str(deadline),
        "--grace-s",
        str(limits.stop_grace_s),
    ]


def start_reaper(config: RunConfig, container_name: str) -> Reaper:
    """Start the reaper in its own session, so it outlives a killed runner.

    The in-container `timeout` is the first line of defence, but a participant can
    freeze it with SIGSTOP. The reaper runs on the host, out of the participant's
    reach, and stops and removes the container at the wall limit plus a margin.
    Its pid goes to `reaper.json` at once, because a killed runner writes no
    `run.json`.
    """
    command = reaper_command(config, container_name)
    deadline = float(command[command.index("--deadline-epoch") + 1])
    with (config.run_dir / "reaper.log").open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    deadline_utc = datetime.fromtimestamp(deadline, UTC).isoformat(timespec="seconds")
    info = {
        "pid": process.pid,
        "container": container_name,
        "deadline_utc": deadline_utc,
    }
    (config.run_dir / "reaper.json").write_text(
        json.dumps(info) + "\n", encoding="utf-8"
    )
    return Reaper(process=process, deadline_utc=deadline_utc)


def cancel_reaper(reaper: Reaper) -> bool:
    """Stop the reaper after a normal end; False if it had already ended."""
    if reaper.process.poll() is not None:
        return False
    reaper.process.terminate()
    try:
        reaper.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        reaper.process.kill()
        reaper.process.wait()
    return True


def execute(config: RunConfig, container_name: str) -> Execution:
    """Run the container with the prompt on stdin and supervise it to the end."""
    command = build_docker_command(config, container_name)
    reaper = start_reaper(config, container_name)
    try:
        execution = _supervised_run(config, container_name, command)
    finally:
        cancelled = cancel_reaper(reaper)
    info = {
        "pid": reaper.process.pid,
        "deadline_utc": reaper.deadline_utc,
        "cancelled": cancelled,
    }
    return dataclasses.replace(execution, reaper=info)


def _supervised_run(
    config: RunConfig, container_name: str, command: list[str]
) -> Execution:
    """Start `docker run`, supervise it, and remove the container afterwards."""
    started = datetime.now(UTC)
    grace_s = config.limits.stop_grace_s
    with (
        (config.run_dir / "prompt.md").open("rb") as prompt,
        (config.run_dir / "transcript.jsonl").open("wb") as stdout,
        (config.run_dir / "stderr.log").open("wb") as stderr,
    ):
        proc = subprocess.Popen(
            command, stdin=prompt, stdout=stdout, stderr=stderr, start_new_session=True
        )
        logger.info("started container %s", container_name)
        try:
            outcome = container_watchdog.supervise(
                proc,
                container=container_name,
                limits=config.limits.watchdog(),
                read_counters=container_watchdog.read_net_counters,
                stop=lambda name: container_watchdog.stop_container(name, grace_s),
            )
        except BaseException:
            container_watchdog.stop_until_exit(
                proc,
                container_name,
                lambda name: container_watchdog.stop_container(name, grace_s),
            )
            raise
        finally:
            state = inspect_container(container_name)
            _docker(["rm", "--force", container_name])
    if state is None:
        raise RunnerError(
            f"docker run failed (exit {outcome.exit_code}); "
            f"see {config.run_dir}/stderr.log"
        )
    return Execution(command, started, datetime.now(UTC), outcome, state)


def build_record(
    config: RunConfig,
    execution: Execution | None,
    facts: Mapping[str, object],
    error: BaseException | None,
) -> dict:
    """Assemble run.json; `execution` is None when the run failed before it started."""
    return {
        "schema_version": SCHEMA_VERSION,
        "setup": config.setup.name,
        "run_dir": str(config.run_dir),
        "cli_version": facts.get("cli_version"),
        "model_requested": config.setup.model,
        "effort_requested": config.setup.effort,
        "models_reported": run_report.reported_models(
            config.setup.name, config.run_dir / "transcript.jsonl", config.home_dir
        ),
        "agent_override": config.agent_override is not None,
        "image": facts.get("image"),
        "host": {
            "hostname": platform.node(),
            "docker_server_version": facts.get("docker_version"),
        },
        "docker_command": execution.command if execution else None,
        "reaper": execution.reaper if execution else None,
        "mounts": [
            {"source": str(m.source), "target": m.target, "read_only": m.read_only}
            for m in mounts(config)
        ],
        "limits": _limits_record(config.limits),
        **_outcome_record(execution, error),
        "usage": run_report.parse_usage(
            config.setup.name, config.run_dir / "transcript.jsonl"
        ),
        "prompt": facts.get("prompt"),
        "input_files": facts.get("input_files"),
        "deliverable": run_report.deliverable_report(config.work_dir),
        "credentials": facts["credentials"],
    }


def _limits_record(limits: Limits) -> dict:
    return {
        "wall_s": limits.wall_s,
        "rx_bytes": limits.rx_bytes,
        "poll_interval_s": limits.poll_interval_s,
        "max_failed_reads": limits.max_failed_reads,
        "startup_grace_s": limits.startup_grace_s,
        "container_timeout_s": limits.container_timeout_s(),
        "reaper_margin_s": limits.reaper_margin_s,
        "cpus": limits.cpus,
        "memory": limits.memory,
        "pids_limit": limits.pids_limit,
        "shm_size": limits.shm_size,
    }


def _outcome_record(execution: Execution | None, error: BaseException | None) -> dict:
    """Timing, stop reason, container state, network counters and any error."""
    if execution is None:
        interrupted = isinstance(error, KeyboardInterrupt)
        return {
            "started_utc": None,
            "ended_utc": None,
            "elapsed_s": None,
            "stop_reason": "interrupted" if interrupted else "runner_error",
            "exit_code": None,
            "oom_killed": None,
            "container_started_at": None,
            "container_finished_at": None,
            "network": _network_record(None),
            "error": _describe(error),
        }
    outcome, state = execution.outcome, execution.container
    return {
        "started_utc": execution.started.isoformat(timespec="milliseconds"),
        "ended_utc": execution.ended.isoformat(timespec="milliseconds"),
        "elapsed_s": round(outcome.elapsed_s, 3),
        "stop_reason": str(outcome.stop_reason),
        "exit_code": state["exit_code"],
        "oom_killed": state["oom_killed"],
        "container_started_at": state["started_at"],
        "container_finished_at": state["finished_at"],
        "network": _network_record(outcome),
        "error": _describe(error) or outcome.error,
    }


def _network_record(outcome: WatchdogResult | None) -> dict:
    return {
        "rx_bytes_peak": outcome.peak_rx_bytes if outcome else 0,
        "tx_bytes_peak": outcome.peak_tx_bytes if outcome else 0,
        "samples": outcome.samples if outcome else 0,
        "failed_reads": outcome.failed_reads if outcome else 0,
        "source": "Docker Engine stats API, networks.*.rx_bytes summed",
        "note": "last sample before exit; a lower bound of the true totals",
    }


def _describe(error: BaseException | None) -> str | None:
    return None if error is None else f"{type(error).__name__}: {error}"


def run_participant(config: RunConfig, case: CaseFiles, credentials_src: Path) -> dict:
    """Prepare, run and record one participant; returns the run.json content.

    Once the run directory exists, `run.json` is always written, with the error
    recorded when the run failed, and the credential copy is always removed. A
    failure is raised again after that.
    """
    check_run_dir(config.run_dir)
    if not case.prompt.is_file():
        raise RunnerError(f"participant prompt not found: {case.prompt}")
    facts: dict[str, object] = {"docker_version": check_host(config)}
    credentials: run_credentials.CredentialCopy | None = None
    execution: Execution | None = None
    error: BaseException | None = None
    try:
        prepare_run_dir(config)
        prompt_copy = config.run_dir / "prompt.md"
        staged = stage_input(case.input_dir, config.input_dir)
        shutil.copyfile(case.prompt, prompt_copy)
        credentials = write_agent_home(config.setup, config.home_dir, credentials_src)
        facts |= _gather_facts(config, staged, prompt_copy)
        execution = execute(config, f"bench-{config.setup.name}-{uuid.uuid4().hex[:8]}")
    except BaseException as err:  # noqa: BLE001  # recorded in run.json, then raised again below
        error = err
    with _signals_blocked():
        facts["credentials"] = _close_credentials(config, credentials)
        record = build_record(config, execution, facts, error)
        run_report.write_json_atomic(config.run_dir / "run.json", record)
    if error is not None:
        raise error
    return record


class RunResult(NamedTuple):
    """One finished run: its directory and the run.json content."""

    run_dir: Path
    record: dict


def run_series(
    config: RunConfig, case: CaseFiles, credentials_src: Path, *, repeat: int
) -> list[RunResult]:
    """Run the participant `repeat` times in a row.

    One repeat runs in `config.run_dir` itself. More than one run in
    `r01` ... `rNN` below it, each a normal run with its own `run.json`, and
    `repeats.json` lists them. The series stops at the first interrupted or
    failed run; `repeats.json` is still written and the failure is raised again.
    """
    if repeat == 1:
        return [
            RunResult(config.run_dir, run_participant(config, case, credentials_src))
        ]
    parent = config.run_dir
    check_run_dir(parent)
    parent.mkdir(parents=True, exist_ok=True)
    results: list[RunResult] = []
    entries: list[dict] = []
    failure: BaseException | None = None
    for index in range(1, repeat + 1):
        name = f"r{index:02d}"
        run_config = dataclasses.replace(config, run_dir=parent / name)
        try:
            record = run_participant(run_config, case, credentials_src)
        except BaseException as err:  # noqa: BLE001  # listed in repeats.json, then raised again below
            failure = err
            entries.append(_failed_entry(name, run_config.run_dir))
            break
        results.append(RunResult(run_config.run_dir, record))
        entries.append(_series_entry(name, record))
        if record["stop_reason"] in ("interrupted", "runner_error") or record["error"]:
            break
    with _signals_blocked():
        run_report.write_json_atomic(
            parent / "repeats.json",
            {
                "schema_version": SCHEMA_VERSION,
                "setup": config.setup.name,
                "requested": repeat,
                "runs": entries,
            },
        )
    if failure is not None:
        raise failure
    return results


def _series_entry(name: str, record: dict) -> dict:
    """The repeats.json row for one run."""
    return {
        "run": name,
        "run_json": f"{name}/run.json",
        "stop_reason": record["stop_reason"],
        "exit_code": record["exit_code"],
        "elapsed_s": record["elapsed_s"],
    }


def _failed_entry(name: str, run_dir: Path) -> dict:
    """The repeats.json row for a run that raised; `run.json` may be missing."""
    run_json = run_dir / "run.json"
    if not run_json.is_file():
        return {
            "run": name,
            "run_json": None,
            "stop_reason": "runner_error",
            "exit_code": None,
            "elapsed_s": None,
        }
    return _series_entry(name, _loads(run_json.read_text(encoding="utf-8")))


@contextlib.contextmanager
def _signals_blocked() -> Generator[None]:
    """Hold SIGINT and SIGTERM until cleanup is done; they arrive afterwards."""
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _gather_facts(
    config: RunConfig, staged: Sequence[Path], prompt_copy: Path
) -> dict[str, object]:
    """Collect the facts recorded before the container starts."""
    return {
        "cli_version": read_cli_version(config),
        "image": describe_image(config.image),
        "prompt": {"file": "prompt.md", "sha256": run_report.sha256_file(prompt_copy)},
        "input_files": [
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": run_report.sha256_file(path),
            }
            for path in staged
        ],
    }


def _close_credentials(
    config: RunConfig, credentials: run_credentials.CredentialCopy | None
) -> dict:
    """Delete every credential copy, then scan for leaked values; never raises.

    Deleting comes first so that a failure while scanning cannot leave a copy.
    The scan covers the values the copy held before and after the run.
    """
    record: dict = {
        "file_in_container": f"~/{config.setup.credentials_relpath}",
        "mode_in_run_dir": "0600",
    }
    if credentials is None:
        return record | {"copied": False, "left_in_run_dir": False, "exposure_hits": 0}
    report = credentials.scrub([config.home_dir, config.work_dir, config.tmp_dir])
    try:
        exposure = run_credentials.exposure_report(
            config.run_dir, credentials.secrets | report.post_secrets
        )
    except Exception as err:  # noqa: BLE001  # the scan must not stop run.json from being written
        exposure = {"total": None, "error": type(err).__name__}
    return record | {
        "copied": True,
        "changed_during_run": report.changed,
        "copies_removed": report.copies_removed,
        "left_in_run_dir": report.left,
        "cleanup_errors": list(report.errors),
        "exposure_hits": exposure["total"],
        "exposure": exposure,
    }


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--setup", choices=sorted(SETUPS), required=True, help="Which CLI to run."
    )
    parser.add_argument(
        "--case-dir",
        type=Path,
        required=True,
        help="Case dir, e.g. ground_truth/06mega-blind.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="New or empty directory for this run.",
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Participant image tag.")
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=DEFAULT_DB_DIR,
        help="Reference databases (mounted read-only).",
    )
    parser.add_argument(
        "--gv-exports-filtered",
        type=Path,
        required=True,
        help="Case-specific filtered gv-exports copy, mounted read-only over "
        "<db>/gv-exports (build it with filter_reference_db.py).",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        help="Credentials file to copy (default ~/<CLI path>).",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="Default <case>/exports/participant/participant_prompt.md.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Default <case>/exports/participant/files.",
    )
    parser.add_argument(
        "--agent-override",
        type=Path,
        help="Test only: executable mounted over the real CLI.",
    )
    parser.add_argument(
        "--repeat",
        type=_positive_int,
        default=DEFAULT_REPEAT,
        help="Run N times in a row into <run-dir>/r01 ... rNN and write "
        f"<run-dir>/repeats.json (default {DEFAULT_REPEAT}; 1 runs once in "
        "<run-dir> itself).",
    )
    _add_limit_args(parser)
    return parser.parse_args(argv)


def _positive_int(text: str) -> int:
    """Parse an argparse value that must be a whole number of at least one."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is below 1")
    return value


def _add_limit_args(parser: argparse.ArgumentParser) -> None:
    """Add the limit options; defaults come from the Limits dataclass."""
    defaults = Limits()
    parser.add_argument(
        "--wall-limit-s",
        type=float,
        default=defaults.wall_s,
        help="Wall time limit in seconds.",
    )
    parser.add_argument(
        "--rx-limit-bytes",
        type=int,
        default=defaults.rx_bytes,
        help="Network bytes received limit.",
    )
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        default=defaults.poll_interval_s,
        help="Watchdog poll interval.",
    )
    parser.add_argument(
        "--cpus", type=int, default=defaults.cpus, help="Container CPU limit."
    )
    parser.add_argument(
        "--memory",
        default=defaults.memory,
        help="Container memory limit (swap equals memory).",
    )
    parser.add_argument(
        "--pids-limit",
        type=int,
        default=defaults.pids_limit,
        help="Container process limit.",
    )
    parser.add_argument(
        "--shm-size", default=defaults.shm_size, help="Container /dev/shm size."
    )
    parser.add_argument(
        "--max-failed-reads",
        type=int,
        default=defaults.max_failed_reads,
        help="Consecutive failed network counter reads that stop the container.",
    )
    parser.add_argument(
        "--startup-grace-s",
        type=float,
        default=defaults.startup_grace_s,
        help="Failed counter reads do not count before a good read or this long.",
    )
    parser.add_argument(
        "--reaper-margin-s",
        type=int,
        default=defaults.reaper_margin_s,
        help="Seconds after the wall limit when the host reaper removes the container.",
    )
    parser.add_argument(
        "--container-timeout-margin-s",
        type=int,
        default=defaults.container_timeout_margin_s,
        help="Seconds after the wall limit when the container ends the agent itself.",
    )


def config_from_args(args: argparse.Namespace) -> tuple[RunConfig, CaseFiles, Path]:
    """Turn parsed arguments into the run config, case files and credentials path."""
    setup = SETUPS[args.setup]
    participant = args.case_dir / "exports" / "participant"
    case = CaseFiles(
        prompt=args.prompt_file or participant / "participant_prompt.md",
        input_dir=args.input_dir or participant / "files",
    )
    config = RunConfig(
        setup=setup,
        run_dir=args.run_dir.resolve(),
        image=args.image,
        db_dir=args.db_dir.resolve(),
        gv_exports_filtered=args.gv_exports_filtered.resolve(),
        limits=Limits(
            wall_s=args.wall_limit_s,
            rx_bytes=args.rx_limit_bytes,
            poll_interval_s=args.poll_interval_s,
            cpus=args.cpus,
            memory=args.memory,
            pids_limit=args.pids_limit,
            shm_size=args.shm_size,
            max_failed_reads=args.max_failed_reads,
            startup_grace_s=args.startup_grace_s,
            container_timeout_margin_s=args.container_timeout_margin_s,
            reaper_margin_s=args.reaper_margin_s,
        ),
        agent_override=args.agent_override.resolve() if args.agent_override else None,
        uid=os.getuid(),
        gid=os.getgid(),
    )
    credentials = args.credentials or Path.home() / setup.credentials_relpath
    return config, case, credentials


def main(argv: Sequence[str] | None = None) -> int:
    """Run one participant, or `--repeat` of them in a row.

    Returns 0 when the run finished and `run.json` was written, 130 when it was
    interrupted, 1 when the watchdog hit an internal error, and 2 on a setup error.
    """
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(message)s", force=True
    )
    previous_handler = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        config, case, credentials = config_from_args(args)
        results = run_series(config, case, credentials, repeat=args.repeat)
    except RunnerError as err:
        print(f"run_participant: {err}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("run_participant: interrupted", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    for run_dir, record in results:
        print(
            f"setup={record['setup']} stop_reason={record['stop_reason']} "
            f"exit_code={record['exit_code']} elapsed_s={record['elapsed_s']} "
            f"rx_bytes_peak={record['network']['rx_bytes_peak']} "
            f"run_json={run_dir / 'run.json'}"
        )
    if any(record["stop_reason"] == "interrupted" for _, record in results):
        return 130
    return 1 if any(record["error"] for _, record in results) else 0


def _raise_interrupt(_signum: int, _frame: object) -> None:
    """Turn SIGTERM into KeyboardInterrupt so the watchdog stops the container."""
    raise KeyboardInterrupt


def _docker(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False
    )


def _loads(text: str) -> dict:
    return json.loads(text) if text.strip() else {}


if __name__ == "__main__":
    raise SystemExit(main())
