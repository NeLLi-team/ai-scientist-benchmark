"""Tests for the host-side container reaper (no Docker needed)."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1] / "skills" / "benchmark-run" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))
import container_reaper  # noqa: E402  # needs the scripts dir on sys.path first


def completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, "", "")


class FakeDocker:
    """Records docker calls; the container disappears after `removals` rm calls."""

    def __init__(self, exists_for_checks: int) -> None:
        self.calls: list[list[str]] = []
        self.exists_for_checks = exists_for_checks

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if args[:2] == ["container", "inspect"]:
            self.exists_for_checks -= 1
            return completed(0 if self.exists_for_checks >= 0 else 1)
        return completed(0)


class ReapTest(unittest.TestCase):
    def test_reap_stops_then_removes_the_container(self) -> None:
        docker = FakeDocker(exists_for_checks=0)

        reaped = container_reaper.reap(
            "c1", 20, run_docker=docker, sleep=lambda _s: None
        )

        self.assertTrue(reaped)
        self.assertEqual(
            docker.calls[:2],
            [["stop", "--time", "20", "c1"], ["rm", "--force", "c1"]],
        )

    def test_reap_repeats_until_the_container_is_gone(self) -> None:
        docker = FakeDocker(exists_for_checks=2)

        reaped = container_reaper.reap(
            "c1", 20, run_docker=docker, sleep=lambda _s: None
        )

        self.assertTrue(reaped)
        self.assertEqual(sum(call[0] == "stop" for call in docker.calls), 3)

    def test_reap_gives_up_after_the_attempt_limit(self) -> None:
        docker = FakeDocker(exists_for_checks=99)

        reaped = container_reaper.reap(
            "c1", 20, run_docker=docker, attempts=3, sleep=lambda _s: None
        )

        self.assertFalse(reaped)
        self.assertEqual(sum(call[0] == "stop" for call in docker.calls), 3)


class WaitUntilTest(unittest.TestCase):
    def test_wait_until_sleeps_in_slices_up_to_the_deadline(self) -> None:
        now = [0.0]
        naps: list[float] = []

        def sleep(seconds: float) -> None:
            naps.append(seconds)
            now[0] += seconds

        container_reaper.wait_until(
            70.0, clock=lambda: now[0], sleep=sleep, slice_s=30.0
        )

        self.assertEqual(naps, [30.0, 30.0, 10.0])

    def test_wait_until_returns_at_once_for_a_past_deadline(self) -> None:
        naps: list[float] = []

        container_reaper.wait_until(5.0, clock=lambda: 10.0, sleep=naps.append)

        self.assertEqual(naps, [])


class MainTest(unittest.TestCase):
    def test_main_waits_then_reaps_and_returns_zero_when_the_container_is_gone(
        self,
    ) -> None:
        with (
            mock.patch.object(container_reaper, "wait_until") as wait,
            mock.patch.object(container_reaper, "reap", return_value=True) as reap,
        ):
            code = container_reaper.main(
                ["--container", "c1", "--deadline-epoch", "123.5", "--grace-s", "7"]
            )

        self.assertEqual(code, 0)
        wait.assert_called_once_with(123.5)
        reap.assert_called_once_with("c1", 7)

    def test_main_returns_one_when_the_container_could_not_be_removed(self) -> None:
        with (
            mock.patch.object(container_reaper, "wait_until"),
            mock.patch.object(container_reaper, "reap", return_value=False),
        ):
            code = container_reaper.main(["--container", "c1", "--deadline-epoch", "1"])

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
