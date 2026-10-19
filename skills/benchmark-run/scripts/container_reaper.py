#!/usr/bin/env python3
"""Stop and remove a participant container at a deadline, even if the runner died.

The runner starts this process in its own session before it starts the
container, and cancels it when the run ends normally. It is the backstop that a
participant cannot reach: the container's own `timeout` can be frozen with
SIGSTOP from inside, and the runner can be killed from outside, but this process
runs on the host, outside the container's process namespace.
"""

from __future__ import annotations

import argparse
import subprocess
import time
from collections.abc import Callable, Sequence

SLICE_S = 30.0
DEFAULT_ATTEMPTS = 5
DEFAULT_PAUSE_S = 5.0


def wait_until(
    deadline_epoch: float,
    *,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    slice_s: float = SLICE_S,
) -> None:
    """Sleep in short slices until the wall clock reaches the deadline."""
    while (remaining := deadline_epoch - clock()) > 0:
        sleep(min(remaining, slice_s))


def reap(
    container: str,
    grace_s: int,
    *,
    run_docker: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    pause_s: float = DEFAULT_PAUSE_S,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Stop and remove the container; repeat until it is gone. True when it is gone."""
    docker = run_docker or _docker
    for attempt in range(attempts):
        docker(["stop", "--time", str(grace_s), container])
        docker(["rm", "--force", container])
        if docker(["container", "inspect", container]).returncode != 0:
            return True
        if attempt + 1 < attempts:
            sleep(pause_s)
    return False


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--container", required=True, help="Container name.")
    parser.add_argument(
        "--deadline-epoch",
        type=float,
        required=True,
        help="Unix time at which the container is stopped and removed.",
    )
    parser.add_argument("--grace-s", type=int, default=20, help="Docker stop grace.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Wait for the deadline, then reap; exit 0 when the container is gone."""
    args = parse_args(argv)
    wait_until(args.deadline_epoch)
    return 0 if reap(args.container, args.grace_s) else 1


def _docker(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False
    )


if __name__ == "__main__":
    raise SystemExit(main())
