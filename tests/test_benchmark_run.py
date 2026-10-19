"""Tests for the benchmark-run skill: command line, watchdog, report, container.

The first groups need no Docker. `ContainerRunTest` and the groups after it run
a stub agent inside the real participant image and skip when the image is
missing. No test calls a model.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import http.client
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "benchmark-run" / "scripts"
STUB_AGENT = Path(__file__).resolve().parent / "fixtures" / "stub_agent.py"
IMAGE = os.environ.get("BENCHMARK_RUN_IMAGE", "benchmark-participant:2026-10-19")

sys.path.insert(0, str(SCRIPTS_DIR))
import container_watchdog  # noqa: E402  # needs the scripts dir on sys.path first
import run_participant  # noqa: E402  # needs the scripts dir on sys.path first
import run_report  # noqa: E402  # needs the scripts dir on sys.path first

BLIND_CASE_SCRIPT = (
    REPO_ROOT / "skills" / "benchmark-prompt-package" / "scripts" / "blind_case.py"
)

CODEX_SECRET = "SENTINEL-CODEX-ACCESS-TOKEN-0123456789abcdef"  # noqa: S105  # fake test value
CLAUDE_SECRET = "SENTINEL-CLAUDE-ACCESS-TOKEN-0123456789abcdef"  # noqa: S105  # fake test value
CODEX_COMMAND = [
    "exec",
    "--dangerously-bypass-approvals-and-sandbox",
    "--skip-git-repo-check",
    "--json",
    "-m",
    "gpt-6-astra",
    "-c",
    "model_reasoning_effort=xhigh",
]
TIMEOUT_PREFIX = ["timeout", "--kill-after=30", "10860"]
CLAUDE_COMMAND = [
    "-p",
    "--model",
    "opus",
    "--effort",
    "xhigh",
    "--dangerously-skip-permissions",
    "--output-format",
    "stream-json",
    "--verbose",
]


def image_available() -> bool:
    """Report whether Docker answers and the participant image exists."""
    try:
        done = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def mount_table(command: list[str]) -> dict[str, tuple[str, bool]]:
    """Map each --mount destination to (source, is_read_only)."""
    table = {}
    for index, argument in enumerate(command):
        if argument != "--mount":
            continue
        fields = dict(
            part.partition("=")[::2]
            for part in command[index + 1].split(",")
            if "=" in part
        )
        table[fields["dst"]] = (
            fields["src"],
            "readonly" in command[index + 1].split(","),
        )
    return table


def make_config(run_dir: Path, setup: str = "codex") -> run_participant.RunConfig:
    """A RunConfig with placeholder host paths, enough to build a command line."""
    return run_participant.RunConfig(
        setup=run_participant.SETUPS[setup],
        run_dir=run_dir,
        image=IMAGE,
        db_dir=Path("/srv/db"),
        gv_exports_filtered=Path("/srv/filtered"),
        limits=run_participant.Limits(),
        agent_override=None,
        uid=1234,
        gid=2345,
    )


class DockerCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.run_dir = Path("/srv/runs/r1")
        self.command = run_participant.build_docker_command(
            make_config(self.run_dir), "bench-test"
        )

    def test_command_runs_as_host_user_with_limits(self) -> None:
        for pair in (
            ("--user", "1234:2345"),
            ("--cpus", "16"),
            ("--memory", "64g"),
            ("--network", "bridge"),
            ("--workdir", "/benchmark/work"),
        ):
            with self.subTest(pair=pair):
                index = self.command.index(pair[0])
                self.assertEqual(self.command[index + 1], pair[1])

    def test_command_sets_a_pids_limit_and_drops_privileges(self) -> None:
        self.assertIn("--pids-limit", self.command)
        self.assertIn("--init", self.command)
        self.assertIn("no-new-privileges", self.command)
        self.assertEqual(self.command[self.command.index("--cap-drop") + 1], "ALL")

    def test_command_mounts_input_and_databases_read_only(self) -> None:
        table = mount_table(self.command)

        self.assertEqual(table["/benchmark/input"], (f"{self.run_dir}/input", True))
        self.assertEqual(table["/media/shared-expansion/db"], ("/srv/db", True))
        self.assertEqual(
            table["/media/shared-expansion/db/gv-exports"], ("/srv/filtered", True)
        )

    def test_command_mounts_work_home_and_tmp_writable(self) -> None:
        table = mount_table(self.command)

        self.assertEqual(table["/benchmark/work"], (f"{self.run_dir}/work", False))
        self.assertEqual(table["/home/participant"], (f"{self.run_dir}/home", False))
        self.assertEqual(table["/tmp"], (f"{self.run_dir}/tmp", False))

    def test_command_mounts_nothing_else(self) -> None:
        self.assertEqual(
            sorted(mount_table(self.command)),
            [
                "/benchmark/input",
                "/benchmark/work",
                "/home/participant",
                "/media/shared-expansion/db",
                "/media/shared-expansion/db/gv-exports",
                "/tmp",
            ],
        )

    def test_command_overlay_mount_comes_after_database_mount(self) -> None:
        destinations = [
            token.split("dst=")[1].split(",")[0]
            for token in self.command
            if token.startswith("type=bind")
        ]

        self.assertLess(
            destinations.index("/media/shared-expansion/db"),
            destinations.index("/media/shared-expansion/db/gv-exports"),
        )

    def test_command_passes_only_explicit_environment(self) -> None:
        env_values = [
            self.command[i + 1] for i, t in enumerate(self.command) if t == "-e"
        ]

        self.assertIn("HOME=/home/participant", env_values)
        self.assertTrue(all("=" in value for value in env_values))
        self.assertFalse(
            any("TOKEN" in value or "KEY" in value for value in env_values)
        )

    def test_codex_command_line_matches_spec(self) -> None:
        index = self.command.index(IMAGE)

        self.assertEqual(
            self.command[index + 1 :],
            [*TIMEOUT_PREFIX, "/opt/codex/bin/codex", *CODEX_COMMAND],
        )

    def test_claude_command_line_matches_spec(self) -> None:
        command = run_participant.build_docker_command(
            make_config(self.run_dir, "claude"), "bench-test"
        )

        index = command.index(IMAGE)
        self.assertEqual(
            command[index + 1 :],
            [*TIMEOUT_PREFIX, "/opt/claude/bin/claude", *CLAUDE_COMMAND],
        )

    def test_command_ends_the_agent_by_itself_after_the_wall_limit_plus_margin(
        self,
    ) -> None:
        config = dataclasses.replace(
            make_config(self.run_dir),
            limits=run_participant.Limits(wall_s=100.5, container_timeout_margin_s=15),
        )

        command = run_participant.build_docker_command(config, "bench-test")

        index = command.index(IMAGE)
        self.assertEqual(
            command[index + 1 : index + 4], ["timeout", "--kill-after=30", "116"]
        )

    def test_agent_override_is_mounted_read_only_over_the_binary(self) -> None:
        override = dataclasses.replace(
            make_config(self.run_dir), agent_override=Path("/srv/stub.py")
        )

        table = mount_table(
            run_participant.build_docker_command(override, "bench-test")
        )

        self.assertEqual(table["/opt/codex/bin/codex"], ("/srv/stub.py", True))


class ReaperLaunchTest(unittest.TestCase):
    def test_reaper_command_names_the_container_and_a_deadline_after_the_wall_limit(
        self,
    ) -> None:
        config = dataclasses.replace(
            make_config(Path("/srv/runs/r1")),
            limits=run_participant.Limits(wall_s=100, reaper_margin_s=25),
        )

        command = run_participant.reaper_command(config, "bench-x", now=1000.0)

        self.assertEqual(command[1].rsplit("/", 1)[-1], "container_reaper.py")
        self.assertEqual(command[command.index("--container") + 1], "bench-x")
        self.assertEqual(command[command.index("--deadline-epoch") + 1], "1125.0")

    def test_reaper_starts_in_its_own_session_so_it_survives_the_runner(self) -> None:
        config = make_config(Path("/srv/runs/r1"))
        with tempfile.TemporaryDirectory() as tmp:
            config = dataclasses.replace(config, run_dir=Path(tmp))
            with mock.patch.object(run_participant.subprocess, "Popen") as popen:
                popen.return_value.pid = 4242
                run_participant.start_reaper(config, "bench-x")

        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_reaper_is_cancelled_by_terminating_it(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        reaper = run_participant.Reaper(process=process, deadline_utc="x")

        cancelled = run_participant.cancel_reaper(reaper)

        self.assertTrue(cancelled)
        process.terminate.assert_called_once()

    def test_reaper_that_already_ended_is_not_reported_as_cancelled(self) -> None:
        process = mock.Mock()
        process.poll.return_value = 0
        reaper = run_participant.Reaper(process=process, deadline_utc="x")

        self.assertFalse(run_participant.cancel_reaper(reaper))


class AgentHomeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.credentials = self.root / "source.json"
        self.credentials.write_text(
            json.dumps({"token": CODEX_SECRET}),  # fake test value
            encoding="utf-8",
        )
        self.credentials.chmod(0o644)
        self.home = self.root / "home"
        self.home.mkdir()

    def test_codex_credentials_are_copied_with_mode_0600(self) -> None:
        run_participant.write_agent_home(
            run_participant.SETUPS["codex"], self.home, self.credentials
        )

        copied = self.home / ".codex" / "auth.json"
        self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
        self.assertEqual(copied.read_bytes(), self.credentials.read_bytes())

    def test_credentials_directory_is_private(self) -> None:
        run_participant.write_agent_home(
            run_participant.SETUPS["codex"], self.home, self.credentials
        )

        self.assertEqual((self.home / ".codex").stat().st_mode & 0o777, 0o700)

    def test_source_credentials_are_left_unchanged(self) -> None:
        run_participant.write_agent_home(
            run_participant.SETUPS["codex"], self.home, self.credentials
        )

        self.assertEqual(self.credentials.stat().st_mode & 0o777, 0o644)

    def test_codex_config_sets_model_and_effort(self) -> None:
        run_participant.write_agent_home(
            run_participant.SETUPS["codex"], self.home, self.credentials
        )

        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "gpt-6-astra"', config)
        self.assertIn('model_reasoning_effort = "xhigh"', config)

    def test_claude_credentials_go_to_the_claude_directory_only(self) -> None:
        run_participant.write_agent_home(
            run_participant.SETUPS["claude"], self.home, self.credentials
        )

        self.assertTrue((self.home / ".claude" / ".credentials.json").exists())
        self.assertFalse((self.home / ".codex").exists())

    def test_missing_credentials_raise_a_runner_error(self) -> None:
        with self.assertRaises(run_participant.RunnerError):
            run_participant.write_agent_home(
                run_participant.SETUPS["codex"], self.home, self.root / "absent.json"
            )

    def test_unchanged_credentials_report_no_refresh(self) -> None:
        copy = run_participant.write_agent_home(
            run_participant.SETUPS["codex"], self.home, self.credentials
        )

        self.assertFalse(copy.scrub([self.home]).changed)

    def test_rewritten_credentials_report_a_refresh(self) -> None:
        copy = run_participant.write_agent_home(
            run_participant.SETUPS["codex"], self.home, self.credentials
        )
        (self.home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")

        self.assertTrue(copy.scrub([self.home]).changed)

    def test_scrub_removes_the_copied_credentials(self) -> None:
        copy = run_participant.write_agent_home(
            run_participant.SETUPS["codex"], self.home, self.credentials
        )

        copy.scrub([self.home])

        self.assertFalse((self.home / ".codex" / "auth.json").exists())


class StageInputTest(unittest.TestCase):
    def test_stage_input_copies_files_and_makes_them_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "files"
            source.mkdir()
            (source / "assembly_A.fna").write_text(
                ">assembly_A_contig_0001\nACGT\n", encoding="utf-8"
            )
            target = Path(tmp) / "input"

            staged = run_participant.stage_input(source, target)

            self.assertEqual([path.name for path in staged], ["assembly_A.fna"])
            self.assertEqual((target / "assembly_A.fna").stat().st_mode & 0o222, 0)

    def test_stage_input_rejects_a_missing_source(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(run_participant.RunnerError),
        ):
            run_participant.stage_input(Path(tmp) / "nope", Path(tmp) / "input")


class FakeProc:
    """Process stand-in whose wait() advances a fake clock."""

    def __init__(self, clock: FakeClock, exits_at: float | None = None) -> None:
        self.clock = clock
        self.exits_at = exits_at
        self.stopped = False

    def wait(self, timeout: float | None = None) -> int:
        if self.stopped:
            return 143
        deadline = self.clock.now + timeout if timeout is not None else float("inf")
        if self.exits_at is not None and self.exits_at <= deadline:
            self.clock.now = self.exits_at
            return 0
        self.clock.now = deadline
        raise subprocess.TimeoutExpired("docker", timeout)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class WatchdogTest(unittest.TestCase):
    def supervise(self, proc, clock, *, rx_per_poll=0, limits=None):
        reads = []

        def read_counters(_name: str):
            reads.append(clock.now)
            total = rx_per_poll * len(reads)
            return container_watchdog.NetCounters(rx_bytes=total, tx_bytes=total // 10)

        def stop(_name: str) -> None:
            proc.stopped = True

        result = container_watchdog.supervise(
            proc,
            container="c1",
            limits=limits
            or container_watchdog.WatchdogLimits(
                wall_s=100, rx_bytes=10_000_000_000, poll_interval_s=30
            ),
            read_counters=read_counters,
            stop=stop,
            clock=clock,
        )
        return result, reads

    def test_wall_time_stops_the_container_at_the_limit(self) -> None:
        clock = FakeClock()

        result, _ = self.supervise(FakeProc(clock), clock)

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.WALL_TIME)
        self.assertEqual(result.elapsed_s, 100)

    def test_wall_time_polls_every_interval(self) -> None:
        clock = FakeClock()

        _, reads = self.supervise(FakeProc(clock), clock)

        self.assertEqual(reads, [30, 60, 90, 100])

    def test_download_cap_stops_the_container_when_rx_reaches_the_limit(self) -> None:
        clock = FakeClock()

        result, _ = self.supervise(FakeProc(clock), clock, rx_per_poll=4_000_000_000)

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.DOWNLOAD_CAP)
        self.assertEqual(result.elapsed_s, 90)
        self.assertEqual(result.peak_rx_bytes, 12_000_000_000)

    def test_download_cap_triggers_at_exactly_the_limit(self) -> None:
        clock = FakeClock()

        result, _ = self.supervise(FakeProc(clock), clock, rx_per_poll=5_000_000_000)

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.DOWNLOAD_CAP)
        self.assertEqual(result.peak_rx_bytes, 10_000_000_000)

    def test_agent_exit_before_any_limit_is_reported_as_agent_exit(self) -> None:
        clock = FakeClock()

        result, _ = self.supervise(FakeProc(clock, exits_at=45), clock, rx_per_poll=1)

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.AGENT_EXIT)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.elapsed_s, 45)

    def test_peak_counters_survive_a_failed_read(self) -> None:
        clock = FakeClock()
        proc = FakeProc(clock, exits_at=95)
        answers = iter([container_watchdog.NetCounters(500, 50), None, None])

        result = container_watchdog.supervise(
            proc,
            container="c1",
            limits=container_watchdog.WatchdogLimits(100, 10_000_000_000, 30),
            read_counters=lambda _name: next(answers),
            stop=lambda _name: None,
            clock=clock,
        )

        self.assertEqual(result.peak_rx_bytes, 500)
        self.assertEqual(result.peak_tx_bytes, 50)


class WatchdogStopRetryTest(unittest.TestCase):
    def test_stop_is_repeated_when_the_container_does_not_exist_yet(self) -> None:
        clock = FakeClock()
        proc = FakeProc(clock)
        calls = []

        def stop(name: str) -> None:
            calls.append(name)
            proc.stopped = len(calls) >= 3

        result = container_watchdog.supervise(
            proc,
            container="c1",
            limits=container_watchdog.WatchdogLimits(10, 10_000_000_000, 30),
            read_counters=lambda _name: None,
            stop=stop,
            clock=clock,
        )

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.WALL_TIME)
        self.assertEqual(calls, ["c1", "c1", "c1"])


class InterruptedProc(FakeProc):
    """Raises KeyboardInterrupt on the first wait, like Ctrl-C at the runner."""

    def wait(self, timeout: float | None = None) -> int:
        if not self.stopped and not hasattr(self, "interrupted"):
            self.interrupted = True
            raise KeyboardInterrupt
        return super().wait(timeout)


class InterruptTest(unittest.TestCase):
    def test_interrupt_stops_the_container_and_is_reported(self) -> None:
        clock = FakeClock()
        proc = InterruptedProc(clock)
        stopped = []

        def stop(name: str) -> None:
            stopped.append(name)
            proc.stopped = True

        result = container_watchdog.supervise(
            proc,
            container="c1",
            limits=container_watchdog.WatchdogLimits(100, 10_000_000_000, 30),
            read_counters=lambda _name: None,
            stop=stop,
            clock=clock,
        )

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.INTERRUPTED)
        self.assertEqual(stopped, ["c1"])


class WatchdogMonitorTest(unittest.TestCase):
    def supervise(self, read_counters, *, limits, exits_at=None):
        clock = FakeClock()
        proc = FakeProc(clock, exits_at=exits_at)
        stopped = []

        def stop(name: str) -> None:
            stopped.append(name)
            proc.stopped = True

        result = container_watchdog.supervise(
            proc,
            container="c1",
            limits=limits,
            read_counters=read_counters,
            stop=stop,
            clock=clock,
        )
        return result, stopped

    def test_three_failed_reads_stop_the_container_as_monitor_failure(self) -> None:
        limits = container_watchdog.WatchdogLimits(
            wall_s=1000,
            rx_bytes=10_000_000_000,
            poll_interval_s=30,
            max_failed_reads=3,
            startup_grace_s=0,
        )

        result, stopped = self.supervise(lambda _name: None, limits=limits)

        self.assertEqual(
            result.stop_reason, container_watchdog.StopReason.MONITOR_FAILURE
        )
        self.assertEqual(result.elapsed_s, 90)
        self.assertEqual((result.samples, result.failed_reads), (0, 3))
        self.assertEqual(stopped, ["c1"])

    def test_a_successful_read_resets_the_failure_count(self) -> None:
        answers = iter(
            [None, None, container_watchdog.NetCounters(1, 1)] * 2
            + [None, None, container_watchdog.NetCounters(2, 2)]
        )
        limits = container_watchdog.WatchdogLimits(
            wall_s=1000,
            rx_bytes=10_000_000_000,
            poll_interval_s=30,
            max_failed_reads=3,
            startup_grace_s=0,
        )

        result, _ = self.supervise(
            lambda _name: next(answers), limits=limits, exits_at=250
        )

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.AGENT_EXIT)
        self.assertEqual(result.failed_reads, 6)

    def test_failures_during_the_startup_grace_period_do_not_stop_the_run(self) -> None:
        limits = container_watchdog.WatchdogLimits(
            wall_s=100,
            rx_bytes=10_000_000_000,
            poll_interval_s=30,
            max_failed_reads=3,
            startup_grace_s=120,
        )

        result, _ = self.supervise(lambda _name: None, limits=limits)

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.WALL_TIME)

    def test_interrupt_inside_the_counter_read_is_reported_and_stops_the_container(
        self,
    ) -> None:
        def read_counters(_name: str):
            raise KeyboardInterrupt

        limits = container_watchdog.WatchdogLimits(100, 10_000_000_000, 30)

        result, stopped = self.supervise(read_counters, limits=limits)

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.INTERRUPTED)
        self.assertEqual(stopped, ["c1"])

    def test_unexpected_error_in_the_counter_read_stops_the_container_and_is_recorded(
        self,
    ) -> None:
        def read_counters(_name: str):
            raise RuntimeError("boom")

        limits = container_watchdog.WatchdogLimits(100, 10_000_000_000, 30)

        result, stopped = self.supervise(read_counters, limits=limits)

        self.assertEqual(result.stop_reason, container_watchdog.StopReason.RUNNER_ERROR)
        self.assertIn("boom", result.error)
        self.assertEqual(stopped, ["c1"])


class StatsParsingTest(unittest.TestCase):
    def test_counters_sum_all_interfaces(self) -> None:
        stats = {
            "networks": {
                "eth0": {"rx_bytes": 700, "tx_bytes": 70},
                "eth1": {"rx_bytes": 300, "tx_bytes": 30},
            }
        }

        counters = container_watchdog.counters_from_stats(stats)

        self.assertEqual(counters, container_watchdog.NetCounters(1000, 100))

    def test_counters_are_none_without_networks(self) -> None:
        self.assertIsNone(
            container_watchdog.counters_from_stats({"read": "0001-01-01"})
        )


def jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


class UsageParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, text: str) -> Path:
        path = self.root / "transcript.jsonl"
        path.write_text(text, encoding="utf-8")
        return path

    def test_codex_usage_sums_turn_completed_events(self) -> None:
        path = self.write(
            jsonl(
                {"type": "thread.started", "thread_id": "t"},
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 4,
                        "output_tokens": 2,
                        "reasoning_output_tokens": 1,
                    },
                },
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 5,
                        "cached_input_tokens": 0,
                        "output_tokens": 3,
                        "reasoning_output_tokens": 2,
                    },
                },
            )
        )

        usage = run_report.parse_usage("codex", path)

        self.assertEqual(usage["input_tokens"], 15)
        self.assertEqual(usage["cached_input_tokens"], 4)
        self.assertEqual(usage["output_tokens"], 5)
        self.assertEqual(usage["reasoning_output_tokens"], 3)
        self.assertEqual(usage["turns_completed"], 2)
        self.assertEqual(usage["source"], "turn.completed")

    def test_codex_usage_without_turns_is_marked_missing(self) -> None:
        path = self.write(jsonl({"type": "thread.started", "thread_id": "t"}))

        usage = run_report.parse_usage("codex", path)

        self.assertEqual(usage["source"], "none")
        self.assertEqual(usage["turns_completed"], 0)

    def test_claude_usage_reads_the_result_event(self) -> None:
        result = {
            "type": "result",
            "subtype": "success",
            "usage": {
                "input_tokens": 7,
                "output_tokens": 9,
                "cache_creation_input_tokens": 3,
                "cache_read_input_tokens": 11,
            },
            "modelUsage": {"claude-opus-5-5": {}, "claude-haiku-5": {}},
            "total_cost_usd": 1.5,
            "num_turns": 4,
            "duration_ms": 1200,
        }
        path = self.write(
            jsonl(
                {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
                result,
            )
        )

        usage = run_report.parse_usage("claude", path)

        self.assertEqual(usage["input_tokens"], 7)
        self.assertEqual(usage["cache_read_input_tokens"], 11)
        self.assertEqual(usage["total_cost_usd"], 1.5)
        self.assertEqual(usage["models_used"], ["claude-haiku-5", "claude-opus-5-5"])
        self.assertEqual(usage["source"], "result")

    def test_claude_usage_falls_back_to_assistant_messages_when_killed(self) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                },
            },
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "usage": {"input_tokens": 5, "output_tokens": 8},
                },
            },
            {
                "type": "assistant",
                "message": {
                    "id": "m2",
                    "usage": {"input_tokens": 6, "output_tokens": 2},
                },
            },
        ]
        path = self.write(jsonl(*events))

        usage = run_report.parse_usage("claude", path)

        self.assertEqual(usage["input_tokens"], 11)
        self.assertEqual(usage["output_tokens"], 10)
        self.assertEqual(usage["source"], "assistant_messages")

    def test_truncated_last_line_is_counted_and_skipped(self) -> None:
        path = self.write(
            jsonl({"type": "turn.completed", "usage": {"input_tokens": 1}})
            + '{"type": "turn.compl'
        )

        usage = run_report.parse_usage("codex", path)

        self.assertEqual(usage["input_tokens"], 1)
        self.assertEqual(usage["unparsed_lines"], 1)


class MissingTranscriptTest(unittest.TestCase):
    def test_usage_of_a_missing_transcript_is_reported_as_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            usage = run_report.parse_usage("codex", Path(tmp) / "absent.jsonl")

        self.assertEqual(usage["source"], "none")

    def test_models_of_a_missing_transcript_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = run_report.reported_models(
                "claude", Path(tmp) / "absent.jsonl", Path(tmp) / "home"
            )

        self.assertEqual(report["models"], [])


class ModelReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_claude_model_comes_from_the_init_event(self) -> None:
        transcript = self.root / "t.jsonl"
        transcript.write_text(
            jsonl({"type": "system", "subtype": "init", "model": "claude-opus-5-5"}),
            encoding="utf-8",
        )

        report = run_report.reported_models("claude", transcript, self.root / "home")

        self.assertEqual(report["models"], ["claude-opus-5-5"])
        self.assertEqual(report["source"], "transcript")

    def test_codex_model_comes_from_the_session_rollout(self) -> None:
        transcript = self.root / "t.jsonl"
        transcript.write_text(
            jsonl({"type": "thread.started", "thread_id": "x"}), encoding="utf-8"
        )
        sessions = self.root / "home" / ".codex" / "sessions" / "2026" / "10" / "19"
        sessions.mkdir(parents=True)
        rollout = jsonl(
            {"type": "session_meta", "payload": {"cli_version": "0.163.0"}},
            {
                "type": "turn_context",
                "payload": {"model": "gpt-6-astra", "effort": "xhigh"},
            },
            {
                "type": "turn_context",
                "payload": {"model": "gpt-6-astra", "effort": "xhigh"},
            },
        )
        (sessions / "rollout-1.jsonl").write_text(rollout, encoding="utf-8")

        report = run_report.reported_models("codex", transcript, self.root / "home")

        self.assertEqual(report["models"], ["gpt-6-astra"])
        self.assertEqual(report["efforts"], ["xhigh"])
        self.assertEqual(report["source"], "codex session rollout")

    def test_no_model_in_transcript_gives_an_empty_list(self) -> None:
        transcript = self.root / "t.jsonl"
        transcript.write_text("", encoding="utf-8")

        report = run_report.reported_models("claude", transcript, self.root / "home")

        self.assertEqual(report["models"], [])


class DeliverableTest(unittest.TestCase):
    def test_deliverable_report_hashes_present_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "deliverable").mkdir()
            (work / "deliverable" / "manuscript.md").write_bytes(b"# Title\n")

            report = run_report.deliverable_report(work)

        self.assertTrue(report["manuscript.md"]["present"])
        self.assertEqual(
            report["manuscript.md"]["sha256"], hashlib.sha256(b"# Title\n").hexdigest()
        )
        self.assertEqual(report["manuscript.md"]["bytes"], 8)
        self.assertEqual(report["manuscript.pdf"], {"present": False})


class DeliverableSymlinkTest(unittest.TestCase):
    def test_deliverable_report_ignores_a_symlinked_deliverable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            outside = Path(tmp) / "outside"
            work.mkdir()
            outside.mkdir()
            (outside / "manuscript.md").write_text("host file", encoding="utf-8")
            (work / "deliverable").symlink_to(outside, target_is_directory=True)

            report = run_report.deliverable_report(work)

        self.assertEqual(report["manuscript.md"], {"present": False})

    def test_deliverable_report_ignores_a_symlinked_manuscript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            (work / "deliverable").mkdir(parents=True)
            host_file = Path(tmp) / "host.txt"
            host_file.write_text("host file", encoding="utf-8")
            (work / "deliverable" / "manuscript.md").symlink_to(host_file)

            report = run_report.deliverable_report(work)

        self.assertEqual(report["manuscript.md"], {"present": False})


class PreflightTest(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = run_participant.main(argv)
        return code, err.getvalue()

    def test_non_empty_run_dir_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "old.txt").write_text("x", encoding="utf-8")

            code, message = self.run_main(
                [
                    "--setup",
                    "codex",
                    "--case-dir",
                    tmp,
                    "--run-dir",
                    str(run_dir),
                    "--gv-exports-filtered",
                    tmp,
                ]
            )

        self.assertEqual(code, 2)
        self.assertIn("not empty", message)

    def test_missing_filtered_reference_copy_is_rejected(self) -> None:
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_participant.main(
                ["--setup", "codex", "--case-dir", "x", "--run-dir", "y"]
            )

    def test_unknown_setup_is_rejected(self) -> None:
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_participant.main(
                ["--setup", "gemini", "--case-dir", "x", "--run-dir", "y"]
            )


class RunDirCredentialTest(unittest.TestCase):
    def test_run_dir_still_holding_a_credential_copy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "home" / ".codex").mkdir(parents=True)
            (run_dir / "home" / ".codex" / "auth.json").write_text(
                "{}", encoding="utf-8"
            )

            with self.assertRaises(run_participant.RunnerError) as raised:
                run_participant.check_run_dir(run_dir)

        self.assertIn("credential", str(raised.exception))

    def test_run_dir_with_a_renamed_credential_directory_is_still_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "home" / ".claude").mkdir(parents=True)
            (run_dir / "home" / ".claude" / ".credentials.json").write_text(
                "{}", encoding="utf-8"
            )

            with self.assertRaises(run_participant.RunnerError) as raised:
                run_participant.check_run_dir(run_dir)

        self.assertIn("credential", str(raised.exception))


class BlindedCaseLayoutTest(unittest.TestCase):
    def test_default_input_dir_accepts_the_layout_the_blinding_helper_writes(
        self,
    ) -> None:
        spec = importlib.util.spec_from_file_location("blind_case", BLIND_CASE_SCRIPT)
        blind_case = importlib.util.module_from_spec(spec)
        sys.modules["blind_case"] = blind_case  # dataclasses look the module up by name
        spec.loader.exec_module(blind_case)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "ground_truth" / "src"
            (source / "starting_data" / "files").mkdir(parents=True)
            (source / "csag").mkdir()
            (source / "scoring").mkdir()
            (source / "csag" / "paper_extraction.json").write_text(
                "{}", encoding="utf-8"
            )
            (source / "scoring" / "scoring_schema.json").write_text(
                "{}", encoding="utf-8"
            )
            for name in ("binone", "bintwo"):
                (source / "starting_data" / "files" / f"{name}.fna").write_text(
                    f">{name}_1\nACGT\n", encoding="utf-8"
                )
            prompt = root / "participant_prompt.md"
            prompt.write_text("# Prompt\n", encoding="utf-8")
            dest = root / "ground_truth" / "dest"
            blind_case.blind_case(source, dest, "seed", prompt, "A question?")

            args = run_participant.parse_args(
                [
                    "--setup",
                    "codex",
                    "--case-dir",
                    str(dest),
                    "--run-dir",
                    str(root / "run"),
                    "--gv-exports-filtered",
                    str(root),
                ]
            )
            _, case, _ = run_participant.config_from_args(args)
            staged = run_participant.stage_input(case.input_dir, root / "input")

        self.assertEqual(
            [path.name for path in staged], ["assembly_A.fna", "assembly_B.fna"]
        )
        self.assertEqual(case.prompt.name, "participant_prompt.md")


class World:
    """Fake case, database, credentials and a runner invocation for one test class."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.case = root / "case"
        participant = self.case / "exports" / "participant"
        files = participant / "files"
        files.mkdir(parents=True)
        for name in ("assembly_A.fna", "assembly_B.fna"):
            (files / name).write_text(f">{name}_contig_0001\nACGT\n", encoding="utf-8")
        (participant / "research_question.md").write_text(
            "not part of the input mount", encoding="utf-8"
        )
        (self.case / "csag").mkdir()
        (self.case / "csag" / "paper_extraction.json").write_text(
            "{}", encoding="utf-8"
        )
        self.db = root / "db"
        (self.db / "gv-exports").mkdir(parents=True)
        (self.db / "gv-exports" / "ORIGINAL_ONLY.txt").write_text(
            "original", encoding="utf-8"
        )
        (self.db / "unified_db").mkdir()
        self.filtered = root / "filtered"
        self.filtered.mkdir()
        (self.filtered / "FILTERED_ONLY.txt").write_text("filtered", encoding="utf-8")
        self.codex_credentials = root / "codex_auth.json"
        self.codex_credentials.write_text(
            json.dumps({"tokens": {"access_token": CODEX_SECRET}}), encoding="utf-8"
        )
        self.claude_credentials = root / "claude_credentials.json"
        self.claude_credentials.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": CLAUDE_SECRET}}),
            encoding="utf-8",
        )

    def write_prompt(self, *lines: str) -> None:
        prompt = self.case / "exports" / "participant" / "participant_prompt.md"
        prompt.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def argv(self, setup: str, name: str, *extra: str) -> list[str]:
        """Runner arguments for a run directory called `name` under the world root."""
        credentials = {
            "codex": self.codex_credentials,
            "claude": self.claude_credentials,
        }
        return [
            "--setup", setup,
            "--case-dir", str(self.case),
            "--run-dir", str(self.root / name),
            "--image", IMAGE,
            "--db-dir", str(self.db),
            "--gv-exports-filtered", str(self.filtered),
            "--credentials", str(credentials[setup]),
            "--agent-override", str(STUB_AGENT),
            *extra,
        ]  # fmt: skip

    def run(self, setup: str, name: str, *extra: str) -> tuple[int, Path, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = run_participant.main(self.argv(setup, name, *extra))
        return code, self.root / name, out.getvalue()


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class ContainerRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.world = World(Path(cls._tmp.name))
        hidden = [str(Path.home()), str(Path.home() / "dev"), cls._tmp.name]
        cls.world.write_prompt("mode=probe", *[f"hidden={path}" for path in hidden])
        cls.hidden = hidden
        cls.code, cls.run_dir, cls.summary = cls.world.run("codex", "run-codex")
        cls.probe = json.loads(
            (cls.run_dir / "work" / "probe.json").read_text(encoding="utf-8")
        )
        cls.meta = json.loads((cls.run_dir / "run.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_runner_exits_zero(self) -> None:
        self.assertEqual(self.code, 0)

    def test_input_mount_is_read_only(self) -> None:
        self.assertEqual(self.probe["writes"]["input"], "EROFS")

    def test_input_holds_only_the_participant_files(self) -> None:
        self.assertEqual(
            self.probe["input_files"], ["assembly_A.fna", "assembly_B.fna"]
        )

    def test_database_mount_is_read_only(self) -> None:
        self.assertEqual(self.probe["writes"]["db"], "EROFS")

    def test_gv_exports_overlay_is_read_only(self) -> None:
        self.assertEqual(self.probe["writes"]["gv_exports"], "EROFS")

    def test_gv_exports_overlay_hides_the_original(self) -> None:
        self.assertEqual(self.probe["gv_exports_entries"], ["FILTERED_ONLY.txt"])

    def test_database_root_is_still_listed(self) -> None:
        self.assertEqual(self.probe["db_entries"], ["gv-exports", "unified_db"])

    def test_work_home_and_tmp_are_writable(self) -> None:
        writes = self.probe["writes"]
        self.assertEqual(
            (writes["work"], writes["home"], writes["tmp"]), ("ok", "ok", "ok")
        )

    def test_host_home_dev_and_test_root_are_not_visible(self) -> None:
        self.assertEqual(set(self.probe["hidden_visible"].values()), {False})
        self.assertEqual(len(self.probe["hidden_visible"]), 3)

    def test_container_home_directory_listing_has_only_participant(self) -> None:
        self.assertEqual(self.probe["home_dirs"], ["participant"])

    def test_agent_runs_as_the_host_user(self) -> None:
        self.assertEqual(
            (self.probe["uid"], self.probe["gid"]), (os.getuid(), os.getgid())
        )

    def test_agent_starts_in_the_work_directory_with_container_home(self) -> None:
        self.assertEqual(self.probe["cwd"], "/benchmark/work")
        self.assertEqual(self.probe["env_home"], "/home/participant")

    def test_agent_receives_the_codex_command_line(self) -> None:
        self.assertEqual(self.probe["argv"], CODEX_COMMAND)

    def test_credentials_have_mode_0600_inside_the_container(self) -> None:
        info = self.probe["credential_files"][".codex/auth.json"]
        self.assertEqual((info["mode"], info["uid"]), ("0o600", os.getuid()))

    def test_only_the_codex_credentials_are_present(self) -> None:
        self.assertEqual(list(self.probe["credential_files"]), [".codex/auth.json"])

    def test_credentials_are_scrubbed_from_the_run_dir_afterwards(self) -> None:
        self.assertFalse((self.run_dir / "home" / ".codex" / "auth.json").exists())
        self.assertFalse(self.meta["credentials"]["left_in_run_dir"])

    def test_credentials_never_appear_in_logs_or_metadata(self) -> None:
        for name in ("transcript.jsonl", "stderr.log", "run.json"):
            with self.subTest(file=name):
                text = (self.run_dir / name).read_text(encoding="utf-8")
                self.assertNotIn(CODEX_SECRET, text)
                self.assertNotIn(CLAUDE_SECRET, text)

    def test_credentials_hit_count_is_zero(self) -> None:
        self.assertEqual(self.meta["credentials"]["exposure_hits"], 0)

    def test_transcript_and_stderr_are_captured(self) -> None:
        lines = (
            (self.run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual(
            [json.loads(line)["type"] for line in lines],
            ["thread.started", "turn.completed"],
        )
        self.assertTrue((self.run_dir / "stderr.log").exists())

    def test_prompt_is_copied_into_the_run_dir(self) -> None:
        copied = (self.run_dir / "prompt.md").read_bytes()
        self.assertEqual(
            self.meta["prompt"]["sha256"], hashlib.sha256(copied).hexdigest()
        )

    def test_run_json_records_setup_and_requested_model(self) -> None:
        self.assertEqual(self.meta["setup"], "codex")
        self.assertEqual(self.meta["model_requested"], "gpt-6-astra")
        self.assertEqual(self.meta["effort_requested"], "xhigh")

    def test_run_json_records_the_cli_version(self) -> None:
        self.assertIn("0.0.1-stub", self.meta["cli_version"])

    def test_run_json_records_the_image(self) -> None:
        image = self.meta["image"]
        self.assertTrue(image["id"].startswith("sha256:"))
        self.assertEqual(image["tag"], IMAGE)
        self.assertIn("org.benchmark.codex.sha256", image["labels"])

    def test_run_json_records_mounts_and_limits(self) -> None:
        targets = {mount["target"]: mount["read_only"] for mount in self.meta["mounts"]}
        self.assertTrue(targets["/benchmark/input"])
        self.assertFalse(targets["/benchmark/work"])
        self.assertEqual(self.meta["limits"]["wall_s"], 10800)
        self.assertEqual(self.meta["limits"]["rx_bytes"], 10_000_000_000)
        self.assertEqual(self.meta["limits"]["cpus"], 16)

    def test_run_json_records_a_clean_exit(self) -> None:
        self.assertEqual(self.meta["stop_reason"], "agent_exit")
        self.assertEqual(self.meta["exit_code"], 0)
        self.assertGreater(self.meta["elapsed_s"], 0)
        self.assertLess(self.meta["started_utc"], self.meta["ended_utc"])

    def test_run_json_records_usage_from_the_transcript(self) -> None:
        usage = self.meta["usage"]
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (100, 20))

    def test_run_json_records_network_counters_as_numbers(self) -> None:
        network = self.meta["network"]
        self.assertIsInstance(network["rx_bytes_peak"], int)
        self.assertIsInstance(network["tx_bytes_peak"], int)
        self.assertIn("samples", network)
        self.assertIn("failed_reads", network)

    def test_run_json_records_the_reaper_and_it_is_cancelled_on_exit(self) -> None:
        reaper = self.meta["reaper"]

        self.assertTrue(reaper["cancelled"])
        self.assertIsInstance(reaper["pid"], int)
        with self.assertRaises(ProcessLookupError):
            os.kill(reaper["pid"], 0)

    def test_reaper_pid_file_is_written_at_launch(self) -> None:
        info = json.loads((self.run_dir / "reaper.json").read_text(encoding="utf-8"))

        self.assertEqual(info["pid"], self.meta["reaper"]["pid"])

    def test_run_json_hashes_the_manuscript(self) -> None:
        expected = hashlib.sha256(b"# Stub manuscript\n").hexdigest()
        self.assertEqual(self.meta["deliverable"]["manuscript.md"]["sha256"], expected)
        self.assertFalse(self.meta["deliverable"]["manuscript.pdf"]["present"])

    def test_run_json_marks_the_agent_override(self) -> None:
        self.assertTrue(self.meta["agent_override"])

    def test_run_json_hashes_the_input_files(self) -> None:
        self.assertEqual(
            [item["name"] for item in self.meta["input_files"]],
            ["assembly_A.fna", "assembly_B.fna"],
        )

    def test_stdout_summary_names_the_stop_reason(self) -> None:
        self.assertIn("agent_exit", self.summary)


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class ClaudeRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        world = World(Path(cls._tmp.name))
        world.write_prompt("mode=probe")
        cls.code, cls.run_dir, _ = world.run("claude", "run-claude")
        cls.probe = json.loads(
            (cls.run_dir / "work" / "probe.json").read_text(encoding="utf-8")
        )
        cls.meta = json.loads((cls.run_dir / "run.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_agent_receives_the_claude_command_line(self) -> None:
        self.assertEqual(self.probe["argv"], CLAUDE_COMMAND)

    def test_only_the_claude_credentials_are_present(self) -> None:
        self.assertEqual(
            list(self.probe["credential_files"]), [".claude/.credentials.json"]
        )
        self.assertEqual(
            self.probe["credential_files"][".claude/.credentials.json"]["mode"], "0o600"
        )

    def test_run_json_records_model_reported_in_the_transcript(self) -> None:
        self.assertEqual(self.meta["model_requested"], "opus")
        self.assertEqual(self.meta["models_reported"]["models"], ["claude-stub-1"])

    def test_run_json_records_result_usage(self) -> None:
        self.assertEqual(self.meta["usage"]["source"], "result")


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class ImagePermissionTest(unittest.TestCase):
    def test_cli_helpers_are_usable_by_the_participant_user(self) -> None:
        # Codex spawns codex-code-mode-host for model tool calls; a root-only
        # mode made every tool call fail with "Permission denied".
        script = (
            "find /opt/codex /opt/claude -type f ! -perm -o+r -print; "
            "find /opt/codex/bin /opt/claude/bin -type f ! -perm -o+x -print"
        )

        done = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "1000:1000",
                "--entrypoint",
                "/bin/sh",
                IMAGE,
                "-c",
                script,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(done.stdout, "")


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class WallLimitTest(unittest.TestCase):
    def test_container_is_stopped_at_the_wall_time_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            world.write_prompt("mode=sleep", "seconds=120")

            code, run_dir, _ = world.run(
                "codex", "run-wall", "--wall-limit-s", "6", "--poll-interval-s", "1"
            )
            meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

            self.assertEqual(code, 0)
            self.assertEqual(meta["stop_reason"], "wall_time")
            self.assertGreaterEqual(meta["elapsed_s"], 6)
            self.assertLess(meta["elapsed_s"], 30)
            self.assertNotEqual(meta["exit_code"], 0)
            self.assertTrue(meta["deliverable"]["manuscript.md"]["present"])


def start_blob_server(add_cleanup: Callable[..., None]) -> str:
    """Run an endless-download HTTP server in a container; return its URL.

    The container is removed through `add_cleanup`, registered before anything
    that can fail, so a failed start does not leave it running.
    """
    code = (
        "import http.server\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers()\n"
        "        chunk = bytes(262144)\n"
        "        while True:\n"
        "            self.wfile.write(chunk)\n"
        "    def log_message(self, *a): pass\n"
        "http.server.ThreadingHTTPServer(('0.0.0.0', 8000), H).serve_forever()\n"
    )
    name = f"bench-blob-{os.getpid()}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, IMAGE, "python3", "-c", code],
        check=True,
        capture_output=True,
    )
    add_cleanup(
        subprocess.run, ["docker", "rm", "-f", name], capture_output=True, check=False
    )
    ip = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            '{{(index .NetworkSettings.Networks "bridge").IPAddress}}',
            name,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    for _ in range(50):
        try:
            connection = http.client.HTTPConnection(ip, 8000, timeout=1)
            connection.request("HEAD", "/")
            connection.close()
            break
        except OSError:
            time.sleep(0.2)
    return f"http://{ip}:8000/blob"


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class DownloadCapTest(unittest.TestCase):
    def test_container_is_stopped_when_received_bytes_reach_the_cap(self) -> None:
        url = start_blob_server(self.addCleanup)
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            world.write_prompt("mode=download", f"url={url}", "pause_s=0.05")

            code, run_dir, _ = world.run(
                "claude",
                "run-cap",
                "--rx-limit-bytes",
                "20000000",
                "--poll-interval-s",
                "1",
            )
            meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

            self.assertEqual(code, 0)
            self.assertEqual(meta["stop_reason"], "download_cap")
            self.assertGreaterEqual(meta["network"]["rx_bytes_peak"], 20_000_000)
            self.assertLess(meta["network"]["rx_bytes_peak"], 200_000_000)
            self.assertLess(meta["elapsed_s"], 60)


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class HostileParticipantTest(unittest.TestCase):
    """A participant that tampers with its credentials must not defeat cleanup."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.world = World(Path(self._tmp.name))
        self.outside = self.world.root / "host_only"
        self.outside.mkdir()
        self.canary = self.outside / "auth.json"
        self.canary.write_text("host-only credentials", encoding="utf-8")
        self.addCleanup(self.make_removable)

    def make_removable(self) -> None:
        """Undo a participant's chmod 000 so the temporary tree can be deleted."""
        for home in self.world.root.glob("*/home"):
            if home.is_dir() and not home.is_symlink():
                home.chmod(0o700)
        for directory in self.world.root.glob("*/home/.codex"):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o700)
        for hidden in self.outside.glob("*"):
            hidden.chmod(0o600)

    def run_hostile(self, mode: str, *extra: str) -> tuple[int, Path, dict]:
        self.world.write_prompt(f"mode={mode}", f"outside={self.outside}")
        code, run_dir, _ = self.world.run("codex", "run-hostile", *extra)
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        return code, run_dir, meta

    def copies_of_credentials(self, run_dir: Path) -> list[Path]:
        secret = self.world.codex_credentials.read_bytes()
        return [
            path
            for path in run_dir.rglob("*")
            if path.is_file() and not path.is_symlink() and path.read_bytes() == secret
        ]

    def test_symlinked_credentials_directory_does_not_delete_host_files(self) -> None:
        self.run_hostile("redirect_cleanup")

        self.assertTrue(self.canary.exists())

    def test_symlinked_credentials_directory_leaves_no_copy_in_the_run_dir(
        self,
    ) -> None:
        _, run_dir, meta = self.run_hostile("redirect_cleanup")

        self.assertEqual(self.copies_of_credentials(run_dir), [])
        self.assertFalse(meta["credentials"]["left_in_run_dir"])

    def test_renamed_and_copied_credentials_are_removed(self) -> None:
        _, run_dir, meta = self.run_hostile("copy_credentials")

        self.assertEqual(self.copies_of_credentials(run_dir), [])
        self.assertFalse(meta["credentials"]["left_in_run_dir"])

    def test_credentials_file_with_mode_000_still_gives_a_run_record_and_no_copy(
        self,
    ) -> None:
        code, run_dir, meta = self.run_hostile("unreadable_file")

        self.assertEqual(code, 0)
        self.assertEqual(self.copies_of_credentials(run_dir), [])
        self.assertFalse(meta["credentials"]["left_in_run_dir"])

    def test_credentials_directory_with_mode_000_still_gives_a_run_record_and_no_copy(
        self,
    ) -> None:
        code, run_dir, meta = self.run_hostile("unreadable_dir")

        self.assertEqual(code, 0)
        self.assertEqual(self.copies_of_credentials(run_dir), [])
        self.assertFalse(meta["credentials"]["left_in_run_dir"])

    def test_credentials_printed_in_the_transcript_are_counted(self) -> None:
        _, _, meta = self.run_hostile("credential_logging")

        self.assertEqual(
            meta["credentials"]["exposure"]["by_location"]["transcript"], 1
        )
        self.assertEqual(meta["credentials"]["exposure_hits"], 1)

    def test_refreshed_credentials_printed_in_the_transcript_are_counted(self) -> None:
        _, run_dir, meta = self.run_hostile("refresh_logging")

        self.assertEqual(
            meta["credentials"]["exposure"]["by_location"]["transcript"], 1
        )
        self.assertTrue(meta["credentials"]["changed_during_run"])
        self.assertFalse((run_dir / "home" / ".codex" / "auth.json").exists())

    def test_credentials_in_a_native_session_log_are_counted(self) -> None:
        _, _, meta = self.run_hostile("session_logging")

        self.assertEqual(meta["credentials"]["exposure"]["by_location"]["home"], 1)

    def test_run_json_never_contains_a_credential_value(self) -> None:
        _, run_dir, _ = self.run_hostile("refresh_logging")

        text = (run_dir / "run.json").read_text(encoding="utf-8")
        self.assertNotIn(CODEX_SECRET, text)
        self.assertNotIn("SENTINEL-REFRESHED", text)

    def test_refresh_then_chmod_000_home_leaves_no_copy_and_counts_the_exposure(
        self,
    ) -> None:
        _, run_dir, meta = self.run_hostile("refresh_lock_home")

        credentials = meta["credentials"]
        self.assertFalse((run_dir / "home" / ".codex" / "auth.json").exists())
        self.assertFalse(credentials["left_in_run_dir"])
        self.assertTrue(credentials["changed_during_run"])
        self.assertEqual(credentials["exposure"]["by_location"]["transcript"], 1)

    def test_oversized_credential_replacement_is_deleted_and_recorded(self) -> None:
        code, run_dir, meta = self.run_hostile("oversize_credentials")

        self.assertEqual(code, 0)
        self.assertFalse((run_dir / "home" / ".codex" / "auth.json").exists())
        self.assertFalse(meta["credentials"]["left_in_run_dir"])
        self.assertTrue(
            any(
                "larger than" in error
                for error in meta["credentials"]["cleanup_errors"]
            )
        )

    def test_symlinked_deliverable_does_not_touch_or_read_host_files(self) -> None:
        hidden = self.outside / "notes.txt"
        hidden.write_text(CODEX_SECRET, encoding="utf-8")
        hidden.chmod(0)

        _, _, meta = self.run_hostile("symlink_deliverable")

        self.assertEqual(hidden.stat().st_mode & 0o777, 0)
        self.assertEqual(
            meta["credentials"]["exposure"]["by_location"]["deliverable"], 0
        )
        self.assertEqual(meta["deliverable"]["manuscript.md"], {"present": False})

    def test_credential_in_a_file_name_is_redacted_in_run_json(self) -> None:
        _, run_dir, meta = self.run_hostile("secret_filename")

        text = (run_dir / "run.json").read_text(encoding="utf-8")
        self.assertNotIn(CODEX_SECRET, text)
        self.assertEqual(meta["credentials"]["exposure"]["by_location"]["home"], 1)
        self.assertTrue(
            any(
                "[credential]" in name
                for name in meta["credentials"]["exposure"]["files"]
            )
        )

    def test_failed_stats_reads_stop_the_run_as_monitor_failure(self) -> None:
        self.world.write_prompt("mode=sleep", "seconds=120")

        with mock.patch.object(
            container_watchdog, "read_net_counters", return_value=None
        ):
            code, run_dir, _ = self.world.run(
                "codex",
                "run-monitor",
                "--poll-interval-s", "1",
                "--startup-grace-s", "0",
                "--max-failed-reads", "3",
            )  # fmt: skip
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(meta["stop_reason"], "monitor_failure")
        self.assertGreaterEqual(meta["network"]["failed_reads"], 3)
        self.assertEqual(meta["network"]["samples"], 0)
        self.assertLess(meta["elapsed_s"], 60)

    def test_interrupt_in_counter_read_still_writes_run_json_and_removes_credentials(
        self,
    ) -> None:
        self.world.write_prompt("mode=sleep", "seconds=120")

        def interrupt(_name: str):
            raise KeyboardInterrupt

        with mock.patch.object(container_watchdog, "read_net_counters", interrupt):
            code, run_dir, _ = self.world.run(
                "codex", "run-int", "--poll-interval-s", "1"
            )
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 130)
        self.assertEqual(meta["stop_reason"], "interrupted")
        self.assertEqual(self.copies_of_credentials(run_dir), [])

    def test_error_before_the_container_starts_is_recorded_and_credentials_are_removed(
        self,
    ) -> None:
        self.world.write_prompt("mode=probe")

        with mock.patch.object(
            run_participant,
            "read_cli_version",
            side_effect=run_participant.RunnerError("boom"),
        ):
            code, run_dir, _ = self.world.run("codex", "run-error")
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 2)
        self.assertIn("boom", meta["error"])
        self.assertEqual(meta["stop_reason"], "runner_error")
        self.assertEqual(self.copies_of_credentials(run_dir), [])

    def test_unexpected_exception_is_recorded_before_it_propagates(self) -> None:
        self.world.write_prompt("mode=probe")

        with (
            mock.patch.object(
                run_participant, "execute", side_effect=RuntimeError("bug")
            ),
            self.assertRaises(RuntimeError),
        ):
            self.world.run("codex", "run-bug")
        meta = json.loads(
            (self.world.root / "run-bug" / "run.json").read_text(encoding="utf-8")
        )

        self.assertIn("bug", meta["error"])
        self.assertEqual(self.copies_of_credentials(self.world.root / "run-bug"), [])


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class RunnerDeathTest(unittest.TestCase):
    def test_container_ends_by_itself_when_the_runner_is_killed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            world.write_prompt("mode=sleep", "seconds=300")
            runner = subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "run_participant.py"),
                    *world.argv(
                        "codex",
                        "run-death",
                        "--wall-limit-s",
                        "3",
                        "--container-timeout-margin-s",
                        "3",
                        "--poll-interval-s",
                        "600",
                    ),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.addCleanup(runner.wait)
            name = wait_for_container("bench-codex-")
            self.addCleanup(
                subprocess.run,
                ["docker", "rm", "-f", name],
                capture_output=True,
                check=False,
            )
            wait_for_file(world.root / "run-death" / "reaper.json")
            self.addCleanup(kill_process, reaper_pid(world.root / "run-death"))
            runner.kill()

            waited = subprocess.run(
                ["docker", "wait", name],
                capture_output=True,
                text=True,
                check=False,
                timeout=90,
            )

            self.assertEqual(waited.stdout.strip(), "124")

    def test_a_new_run_in_the_same_directory_is_refused_while_credentials_remain(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            world.write_prompt("mode=probe")
            run_dir = world.root / "run-left"
            (run_dir / "home" / ".codex").mkdir(parents=True)
            (run_dir / "home" / ".codex" / "auth.json").write_text(
                "{}", encoding="utf-8"
            )

            code, _, _ = world.run("codex", "run-left")

        self.assertEqual(code, 2)


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class FrozenTimeoutTest(unittest.TestCase):
    def test_host_reaper_stops_a_frozen_container_when_the_runner_dies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            world.write_prompt("mode=freeze_timeout", "seconds=600")
            runner = subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "run_participant.py"),
                    *world.argv(
                        "codex",
                        "run-frozen",
                        "--wall-limit-s",
                        "3",
                        "--container-timeout-margin-s",
                        "2",
                        "--reaper-margin-s",
                        "25",
                        "--poll-interval-s",
                        "600",
                    ),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.addCleanup(runner.wait)
            name = wait_for_container("bench-codex-")
            self.addCleanup(
                subprocess.run,
                ["docker", "rm", "-f", name],
                capture_output=True,
                check=False,
            )
            run_dir = world.root / "run-frozen"
            wait_for_file(run_dir / "work" / "frozen")
            self.addCleanup(kill_process, reaper_pid(run_dir))
            time.sleep(6)  # longer than the 5 s in-container limit
            still_running = container_state(name, "{{.State.Running}}")
            runner.kill()

            gone = wait_until_container_is_gone(name, timeout_s=150)

        self.assertEqual(still_running, "true")
        self.assertTrue(gone)


def wait_for_file(path: Path, timeout_s: float = 90) -> None:
    """Block until `path` exists."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.2)
    raise AssertionError(f"{path} did not appear")


def container_state(name: str, template: str) -> str:
    """One field of `docker inspect` for a container, or an empty string."""
    done = subprocess.run(
        ["docker", "inspect", "-f", template, name],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip()


def wait_until_container_is_gone(name: str, timeout_s: float) -> bool:
    """True once `docker ps -a` no longer lists the container."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        done = subprocess.run(
            ["docker", "ps", "-a", "-q", "--filter", f"name=^{name}$"],
            capture_output=True,
            text=True,
            check=False,
        )
        if not done.stdout.strip():
            return True
        time.sleep(1)
    return False


def reaper_pid(run_dir: Path) -> int:
    """The pid the runner recorded for its reaper."""
    return json.loads((run_dir / "reaper.json").read_text(encoding="utf-8"))["pid"]


def kill_process(pid: int) -> None:
    """Stop a process left over from a test, if it is still running."""
    try:
        os.kill(pid, 15)
    except OSError:
        return


def wait_for_container(prefix: str, timeout_s: float = 90) -> str:
    """Return the name of a container whose name starts with `prefix`."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        done = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={prefix}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        names = done.stdout.split()
        if names:
            return names[0]
        time.sleep(0.5)
    raise AssertionError(f"no container named {prefix}* appeared")


class RepeatArgumentTest(unittest.TestCase):
    ARGV = ("--setup", "codex", "--case-dir", "c", "--run-dir", "r")
    ARGV_REST = ("--gv-exports-filtered", "f")

    def parse(self, *extra: str):
        return run_participant.parse_args([*self.ARGV, *self.ARGV_REST, *extra])

    def test_repeat_defaults_to_one(self) -> None:
        self.assertEqual(self.parse().repeat, 1)

    def test_repeat_takes_a_count(self) -> None:
        self.assertEqual(self.parse("--repeat", "3").repeat, 3)

    def test_repeat_below_one_is_rejected(self) -> None:
        for bad in ("0", "-2", "x"):
            with (
                self.subTest(value=bad),
                self.assertRaises(SystemExit),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.parse("--repeat", bad)


def fake_record(stop_reason: str = "agent_exit", exit_code: int = 0) -> dict:
    """The few run.json fields the repeat series reads."""
    return {
        "setup": "codex",
        "stop_reason": stop_reason,
        "exit_code": exit_code,
        "elapsed_s": 1.5,
        "error": None,
        "network": {"rx_bytes_peak": 0},
    }


class RepeatSeriesTest(unittest.TestCase):
    """run_series with run_participant.run_participant replaced; no Docker."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parent = Path(self._tmp.name) / "series"
        self.config = make_config(self.parent)
        self.case = run_participant.CaseFiles(prompt=Path("p"), input_dir=Path("i"))
        self.seen: list[Path] = []

    def run_series(self, repeat: int, records: list) -> list:
        """Run the series; `records` holds one record or exception per run."""
        queue = list(records)

        def fake_run(config, _case, _credentials):
            self.seen.append(config.run_dir)
            outcome = queue.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with mock.patch.object(run_participant, "run_participant", fake_run):
            return run_participant.run_series(
                self.config, self.case, Path("c"), repeat=repeat
            )

    def repeats(self) -> dict:
        path = self.parent / "repeats.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_one_run_uses_the_run_dir_itself_and_writes_no_repeats_file(self) -> None:
        results = self.run_series(1, [fake_record()])

        self.assertEqual(self.seen, [self.parent])
        self.assertEqual([result.run_dir for result in results], [self.parent])
        self.assertFalse((self.parent / "repeats.json").exists())

    def test_repeats_run_in_order_in_numbered_subdirectories(self) -> None:
        results = self.run_series(3, [fake_record() for _ in range(3)])

        expected = [self.parent / name for name in ("r01", "r02", "r03")]
        self.assertEqual(self.seen, expected)
        self.assertEqual([result.run_dir for result in results], expected)

    def test_repeats_file_lists_each_run_with_its_stop_reason(self) -> None:
        self.run_series(
            2, [fake_record("agent_exit"), fake_record("wall_time", exit_code=124)]
        )

        listing = self.repeats()
        self.assertEqual(listing["setup"], "codex")
        self.assertEqual(listing["requested"], 2)
        self.assertEqual(
            [(run["run"], run["stop_reason"]) for run in listing["runs"]],
            [("r01", "agent_exit"), ("r02", "wall_time")],
        )
        self.assertEqual(listing["runs"][1]["run_json"], "r02/run.json")
        self.assertEqual(listing["runs"][1]["exit_code"], 124)

    def test_an_interrupted_run_ends_the_series_and_is_listed(self) -> None:
        results = self.run_series(
            3, [fake_record(), fake_record("interrupted", exit_code=137)]
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(self.seen, [self.parent / "r01", self.parent / "r02"])
        self.assertEqual(
            [run["stop_reason"] for run in self.repeats()["runs"]],
            ["agent_exit", "interrupted"],
        )

    def test_a_failed_run_is_listed_with_its_run_json_and_raised_again(self) -> None:
        records = [fake_record()]

        def fake_run(config, _case, _credentials):
            self.seen.append(config.run_dir)
            if records:
                return records.pop()
            config.run_dir.mkdir(parents=True)
            failed = fake_record("runner_error", exit_code=None)
            (config.run_dir / "run.json").write_text(
                json.dumps(failed), encoding="utf-8"
            )
            raise RuntimeError("boom")

        with (
            mock.patch.object(run_participant, "run_participant", fake_run),
            self.assertRaises(RuntimeError),
        ):
            run_participant.run_series(self.config, self.case, Path("c"), repeat=3)

        self.assertEqual(self.seen, [self.parent / "r01", self.parent / "r02"])
        self.assertEqual(
            [run["stop_reason"] for run in self.repeats()["runs"]],
            ["agent_exit", "runner_error"],
        )

    def test_a_returned_runner_error_record_ends_the_series_and_is_listed(
        self,
    ) -> None:
        failed = fake_record("runner_error", exit_code=None)
        failed["error"] = "RunnerError: docker not reachable"

        results = self.run_series(3, [fake_record(), failed])

        self.assertEqual(len(results), 2)
        self.assertEqual(self.seen, [self.parent / "r01", self.parent / "r02"])
        self.assertEqual(
            [run["stop_reason"] for run in self.repeats()["runs"]],
            ["agent_exit", "runner_error"],
        )

    def test_any_returned_record_with_an_error_ends_the_series(self) -> None:
        failed = fake_record("agent_exit")
        failed["error"] = "ValueError: could not read the transcript"

        results = self.run_series(3, [failed])

        self.assertEqual(len(results), 1)
        self.assertEqual(self.seen, [self.parent / "r01"])
        self.assertEqual(len(self.repeats()["runs"]), 1)

    def test_a_failure_before_any_run_json_exists_is_listed_as_runner_error(
        self,
    ) -> None:
        with self.assertRaises(run_participant.RunnerError):
            self.run_series(2, [run_participant.RunnerError("no prompt")])

        self.assertEqual(
            [run["stop_reason"] for run in self.repeats()["runs"]], ["runner_error"]
        )

    def test_a_non_empty_run_dir_is_refused_before_any_run(self) -> None:
        self.parent.mkdir()
        (self.parent / "old.txt").write_text("x", encoding="utf-8")

        with self.assertRaises(run_participant.RunnerError) as raised:
            self.run_series(2, [fake_record(), fake_record()])

        self.assertIn("not empty", str(raised.exception))
        self.assertEqual(self.seen, [])


class RepeatMainTest(unittest.TestCase):
    def test_main_prints_one_summary_line_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()

            def fake_run(config, _case, _credentials):
                return fake_record()

            with (
                mock.patch.object(run_participant, "run_participant", fake_run),
                contextlib.redirect_stdout(out),
            ):
                code = run_participant.main(
                    [
                        "--setup", "codex",
                        "--case-dir", tmp,
                        "--run-dir", str(Path(tmp) / "series"),
                        "--gv-exports-filtered", tmp,
                        "--repeat", "2",
                    ]
                )  # fmt: skip

        lines = out.getvalue().strip().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertIn("r01/run.json", lines[0])
        self.assertIn("r02/run.json", lines[1])

    def test_main_returns_130_when_a_repeat_was_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = iter([fake_record("interrupted", exit_code=137)])

            def fake_run(config, _case, _credentials):
                return next(records)

            with (
                mock.patch.object(run_participant, "run_participant", fake_run),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = run_participant.main(
                    [
                        "--setup", "codex",
                        "--case-dir", tmp,
                        "--run-dir", str(Path(tmp) / "series"),
                        "--gv-exports-filtered", tmp,
                        "--repeat", "2",
                    ]
                )  # fmt: skip

        self.assertEqual(code, 130)


@unittest.skipUnless(image_available(), f"image {IMAGE} not available")
class ContainerRepeatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        world = World(Path(cls._tmp.name))
        world.write_prompt("mode=probe")
        cls.code, cls.run_dir, _ = world.run("codex", "series", "--repeat", "2")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_runner_exits_zero(self) -> None:
        self.assertEqual(self.code, 0)

    def test_each_repeat_is_a_normal_run_with_its_own_record_and_deliverable(
        self,
    ) -> None:
        for name in ("r01", "r02"):
            with self.subTest(run=name):
                meta = json.loads(
                    (self.run_dir / name / "run.json").read_text(encoding="utf-8")
                )
                self.assertEqual(meta["stop_reason"], "agent_exit")
                self.assertTrue((self.run_dir / name / "work" / "deliverable").is_dir())

    def test_repeats_file_lists_both_runs(self) -> None:
        listing = json.loads(
            (self.run_dir / "repeats.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [(run["run"], run["stop_reason"]) for run in listing["runs"]],
            [("r01", "agent_exit"), ("r02", "agent_exit")],
        )


if __name__ == "__main__":
    unittest.main()
