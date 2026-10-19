"""Watch a running participant container and stop it at the run limits.

The watchdog enforces two limits: wall time and cumulative network bytes
received. It also stops the container when the counters cannot be read, because
an unreadable counter would otherwise disable the download cap. Network counters
come from the Docker Engine stats endpoint, the same source as `docker stats`,
read as exact bytes instead of the rounded strings the CLI prints. The counters
sum every non-loopback interface in the container.
"""

from __future__ import annotations

import enum
import http.client
import json
import logging
import os
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

logger = logging.getLogger(__name__)

DEFAULT_DOCKER_SOCKET = "/var/run/docker.sock"
STATS_TIMEOUT_S = 10.0
STOP_RETRY_S = 5.0


class StopReason(enum.StrEnum):
    """Why a run ended."""

    AGENT_EXIT = "agent_exit"
    WALL_TIME = "wall_time"
    DOWNLOAD_CAP = "download_cap"
    MONITOR_FAILURE = "monitor_failure"
    INTERRUPTED = "interrupted"
    RUNNER_ERROR = "runner_error"


@dataclass(frozen=True, slots=True)
class WatchdogLimits:
    """Limits and polling rate; times in seconds, rx in bytes.

    `max_failed_reads` consecutive failed counter reads stop the container.
    Failures count only after the first good read or after `startup_grace_s`,
    because the container does not exist for the first seconds of a run.
    """

    wall_s: float
    rx_bytes: int
    poll_interval_s: float
    max_failed_reads: int = 3
    startup_grace_s: float = 120


@dataclass(frozen=True, slots=True)
class NetCounters:
    """Cumulative bytes received and sent by the container."""

    rx_bytes: int
    tx_bytes: int


@dataclass(frozen=True, slots=True)
class WatchdogResult:
    """Outcome of one supervised run."""

    stop_reason: StopReason
    elapsed_s: float
    exit_code: int
    peak_rx_bytes: int
    peak_tx_bytes: int
    samples: int
    failed_reads: int = 0
    error: str | None = None


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP over the Docker unix socket."""

    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost", timeout=STATS_TIMEOUT_S)
        self._socket_path = socket_path

    def connect(self) -> None:
        """Open the unix socket instead of a TCP connection."""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(STATS_TIMEOUT_S)
        self.sock.connect(self._socket_path)


def docker_socket_path() -> str:
    """Return the Engine socket path, honouring a `unix://` DOCKER_HOST."""
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        return host.removeprefix("unix://")
    return DEFAULT_DOCKER_SOCKET


def counters_from_stats(stats: dict) -> NetCounters | None:
    """Sum rx and tx bytes over the container's interfaces, or None if absent."""
    networks = stats.get("networks")
    if not networks:
        return None
    return NetCounters(
        rx_bytes=sum(nic.get("rx_bytes", 0) for nic in networks.values()),
        tx_bytes=sum(nic.get("tx_bytes", 0) for nic in networks.values()),
    )


def read_net_counters(container: str) -> NetCounters | None:
    """Read exact network counters for a running container; None when unavailable."""
    connection = _UnixHTTPConnection(docker_socket_path())
    try:
        connection.request(
            "GET", f"/containers/{quote(container)}/stats?stream=false&one-shot=true"
        )
        response = connection.getresponse()
        body = response.read()
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()
    if response.status != http.HTTPStatus.OK:
        return None
    try:
        return counters_from_stats(json.loads(body))
    except json.JSONDecodeError:
        return None


def stop_container(container: str, grace_s: int) -> None:
    """Ask Docker to stop the container: SIGTERM, then SIGKILL after the grace time."""
    subprocess.run(
        ["docker", "stop", "--time", str(grace_s), container],
        check=False,
        capture_output=True,
    )


def supervise(
    proc: subprocess.Popen,
    *,
    container: str,
    limits: WatchdogLimits,
    read_counters: Callable[[str], NetCounters | None] = read_net_counters,
    stop: Callable[[str], None],
    clock: Callable[[], float] = time.monotonic,
) -> WatchdogResult:
    """Wait for `proc` (the `docker run` client), stopping the container at a limit.

    Polls every `poll_interval_s`; the wall limit is checked to the second, the
    download cap only at each poll, so received bytes can overshoot the cap by
    one interval of traffic. The last counters seen before the container exits
    are a lower bound of the true totals, because Docker reports zero once the
    network namespace is gone.

    An interrupt or an unexpected error anywhere in the loop stops the container
    and comes back as a result, never as an exception, so the caller can always
    write its run record.
    """
    watch = _Watch(proc, container, limits, read_counters, clock)
    try:
        watch.poll_until_done()
    except KeyboardInterrupt:
        watch.stop_reason = StopReason.INTERRUPTED
    except Exception as err:  # boundary: any failure must still stop the container
        logger.exception("watchdog failed")
        watch.stop_reason = StopReason.RUNNER_ERROR
        watch.error = f"{type(err).__name__}: {err}"
    if watch.exit_code is None:
        watch.exit_code = _stop_despite_interrupts(proc, container, stop)
    return watch.result()


def _stop_despite_interrupts(
    proc: subprocess.Popen, container: str, stop: Callable[[str], None]
) -> int:
    """Stop the container; a second interrupt must not abandon a running agent."""
    result: int | None = None
    while result is None:
        try:
            result = stop_until_exit(proc, container, stop)
        except KeyboardInterrupt:  # noqa: PERF203  # retrying after an interrupt is the point
            continue
    return result


def stop_until_exit(
    proc: subprocess.Popen, container: str, stop: Callable[[str], None]
) -> int:
    """Stop the container, repeating until the `docker run` client exits.

    A limit can fire while `docker run` is still creating the container. Then
    `docker stop` fails or finds a container that has not started, and the agent
    would otherwise run on, so the stop is repeated every few seconds.
    """
    while True:
        stop(container)
        try:
            return proc.wait(timeout=STOP_RETRY_S)
        except subprocess.TimeoutExpired:
            continue


class _Watch:
    """State of one supervised run: samples, failed reads and the outcome so far."""

    def __init__(
        self,
        proc: subprocess.Popen,
        container: str,
        limits: WatchdogLimits,
        read_counters: Callable[[str], NetCounters | None],
        clock: Callable[[], float],
    ) -> None:
        self.proc = proc
        self.container = container
        self.limits = limits
        self.read_counters = read_counters
        self.clock = clock
        self.start = clock()
        self.peak_rx = 0
        self.peak_tx = 0
        self.samples = 0
        self.failed_reads = 0
        self.consecutive_failures = 0
        self.stop_reason: StopReason | None = None
        self.exit_code: int | None = None
        self.error: str | None = None

    def elapsed_s(self) -> float:
        """Seconds since supervision started."""
        return self.clock() - self.start

    def poll_until_done(self) -> None:
        """Wait for the client to exit, or stop at the first limit that is reached."""
        while True:
            remaining = self.limits.wall_s - self.elapsed_s()
            timeout = max(0.0, min(self.limits.poll_interval_s, remaining))
            try:
                self.exit_code = self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                reason = self._check_limits()
                if reason is not None:
                    self.stop_reason = reason
                    return
            else:
                self.stop_reason = StopReason.AGENT_EXIT
                return

    def result(self) -> WatchdogResult:
        """Freeze the state into a result; call it after the container has exited."""
        return WatchdogResult(
            stop_reason=self.stop_reason or StopReason.RUNNER_ERROR,
            elapsed_s=self.elapsed_s(),
            exit_code=self.exit_code if self.exit_code is not None else -1,
            peak_rx_bytes=self.peak_rx,
            peak_tx_bytes=self.peak_tx,
            samples=self.samples,
            failed_reads=self.failed_reads,
            error=self.error,
        )

    def _check_limits(self) -> StopReason | None:
        """Sample the counters and return the limit that was reached, if any."""
        self._record(self.read_counters(self.container))
        if self.peak_rx >= self.limits.rx_bytes:
            return StopReason.DOWNLOAD_CAP
        if self.elapsed_s() >= self.limits.wall_s:
            return StopReason.WALL_TIME
        if self.consecutive_failures >= self.limits.max_failed_reads:
            return StopReason.MONITOR_FAILURE
        return None

    def _record(self, counters: NetCounters | None) -> None:
        """Fold one reading into the peaks and the failure counters."""
        if counters is None:
            self.failed_reads += 1
            if self.samples > 0 or self.elapsed_s() >= self.limits.startup_grace_s:
                self.consecutive_failures += 1
            return
        self.consecutive_failures = 0
        self.samples += 1
        self.peak_rx = max(self.peak_rx, counters.rx_bytes)
        self.peak_tx = max(self.peak_tx, counters.tx_bytes)
