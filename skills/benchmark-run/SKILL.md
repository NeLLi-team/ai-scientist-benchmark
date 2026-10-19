---
name: benchmark-run
description: Run a native Codex CLI or Claude Code participant on a benchmark case inside a pinned Docker container, with a wall-time and download watchdog and a run record, then audit the run for leaked case terms. Use when launching participant runs for a case such as 06mega-blind or when checking their transcripts and workspaces for original IDs.
---

# Benchmark Run

Use this skill to run one participant (Codex CLI or Claude Code) against a case whose participant package already exists under `ground_truth/<case>/exports/participant/`, and to audit the result. The container is the sandbox. Authoring the case stays with the other `benchmark-*` skills.

## Run contract

| Item | Value |
| --- | --- |
| Image | `benchmark-participant:2026-10-19`, built by `docker/build.sh` from a pinned Ubuntu 24.04 digest, a dated Ubuntu snapshot, pixi 0.72.0 and the Codex 0.163.0 and Claude Code 2.1.303 binaries (SHA-256 pinned). Labels `org.benchmark.*` record the pins. |
| Codex command | `codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json -m gpt-6-astra -c model_reasoning_effort=xhigh`, prompt on stdin |
| Claude command | `claude -p --model opus --effort xhigh --dangerously-skip-permissions --output-format stream-json --verbose`, prompt on stdin |
| Mounts | `<run>/input` -> `/benchmark/input` (ro), `<run>/work` -> `/benchmark/work`, `<run>/home` -> `/home/participant`, `<run>/tmp` -> `/tmp`, the database dir -> `/media/shared-expansion/db` (ro), the filtered gv-exports -> `/media/shared-expansion/db/gv-exports` (ro, hides the original). Nothing else. |
| User | Host uid and gid, no capabilities, `no-new-privileges`, `--init` |
| Limits | `--cpus 16`, `--memory 64g` (swap equal), `--pids-limit 4096`, `--shm-size 4g`, network `bridge` |
| Watchdog | Polls every 30 s. Stops the container (SIGTERM, then SIGKILL after 20 s) at 10,800 s wall time, at 10,000,000,000 received bytes, or after 3 consecutive failed counter reads (`monitor_failure`; failures count once a read has worked or after 120 s). |
| Backstops | First, the agent runs under `timeout` inside the container (wall limit plus 60 s, `--kill-after=30`), so the container ends by itself with exit code 124 if the runner is killed. Second, a detached host process (`container_reaper.py`, own session) stops and removes the container at the wall limit plus 120 s. A participant can freeze the in-container `timeout` with SIGSTOP; it cannot reach the host reaper. The runner cancels the reaper after a normal end. |
| Credentials | A 0600 copy of only that setup's credentials file goes into `<run>/home`; the host files are never mounted. Cleanup runs after the container is gone, before any scanning, and always (see below). |

The runner passes no host environment into the container. Codex and Claude Code write their own session logs under `<run>/home`; keep them with the run.

## Build the image

```bash
skills/benchmark-run/docker/build.sh              # tag benchmark-participant:2026-10-19
docker image inspect benchmark-participant:2026-10-19 --format '{{.Id}}'
```

The build fails if a binary does not match its pinned SHA-256 or version. To change a pin, edit the constants in `docker/build.sh` and the default tag in `scripts/run_participant.py`.

## Run one participant

```bash
uv run python skills/benchmark-run/scripts/run_participant.py \
  --setup codex \
  --case-dir ground_truth/06mega-blind \
  --run-dir <new-or-empty-dir-on-a-posix-filesystem>
```

Defaults: prompt `<case>/exports/participant/participant_prompt.md`, starting data `<case>/exports/participant/files` (the layout `blind_case.py` writes), credentials `~/.codex/auth.json` or `~/.claude/.credentials.json`, database `/media/shared-expansion/db`. `--gv-exports-filtered` is required: pass the case-specific filtered copy built with `filter_reference_db.py`. Override the defaults with `--prompt-file`, `--input-dir`, `--credentials`, `--db-dir`, `--gv-exports-filtered`. Limits have flags too (`--wall-limit-s`, `--rx-limit-bytes`, `--poll-interval-s`, `--max-failed-reads`, `--startup-grace-s`, `--container-timeout-margin-s`, `--reaper-margin-s`, `--cpus`, `--memory`, `--pids-limit`, `--shm-size`).

The run directory must be empty and on a POSIX filesystem (chmod 0600 and symlinks work). The exFAT disk under `/media/shared-expansion` fails that check; the runner says so and stops. A directory that still holds a credential copy (for example after a SIGKILL of the runner) is refused with its own message; delete it first.

## Repeat a run

`--repeat N` (default 1) runs the same setup N times, one after the other. With N above 1, `--run-dir` is a parent: run `i` goes to `<run-dir>/rNN` (`r01` ... `r10` ...) and is a normal run with its own `run.json`, credential cleanup and exposure scan. `<run-dir>/repeats.json` then lists the runs:

```json
{"schema_version": 1, "setup": "codex", "requested": 3,
 "runs": [{"run": "r01", "run_json": "r01/run.json", "stop_reason": "agent_exit", "exit_code": 0, "elapsed_s": 5400.2}]}
```

The parent must be new or empty. The series stops at the first interrupted run, the first run that ends with `stop_reason: runner_error` or any `error` in its record (returned or raised), still writes `repeats.json` (a failed run without a `run.json` is listed with `stop_reason: runner_error` and `run_json: null`) and then exits like a single run would: 130 after an interrupt, 2 or the raised error otherwise. A run that ends on a limit (`wall_time`, `download_cap`, `monitor_failure`) does not stop the series. N = 1 behaves as before: one run in `--run-dir`, no `repeats.json`. The runner prints one summary line per run. Score the runs as one setup by passing the `rNN/work/deliverable` outputs to the scorer under names like `<setup>__r01`.

Start the two runs in parallel from separate shells or `nohup`. Ctrl-C or SIGTERM, at any point in the watchdog loop, stops the container and still writes `run.json` with `stop_reason: interrupted` (exit code 130). Once the run directory exists, `run.json` is always written, with the error recorded when the run failed (`stop_reason: runner_error`); the runner then exits with the error.

## Run directory

| File | Content |
| --- | --- |
| `transcript.jsonl` | Full stdout of the CLI (JSONL) |
| `stderr.log` | Full stderr |
| `prompt.md` | The prompt as sent, SHA-256 in `run.json` |
| `work/` | Agent workspace; `work/deliverable/manuscript.md` and `.pdf` are the deliverable |
| `home/` | Container HOME: CLI session logs and config (credentials removed) |
| `input/` | Read-only copy of the starting files |
| `reaper.json`, `reaper.log` | Pid, container and deadline of the host reaper (written at launch, so a killed runner still leaves them), and its log |
| `run.json` | Run record (below) |

`run.json` holds: setup; CLI version (`--version` inside the image); model and effort requested; models and efforts the CLI reported (Claude `system` init event; Codex session rollout, because the exec JSONL has no model); image tag, id and pinned labels; the exact `docker run` argument list; mounts; limits; start and end UTC; elapsed seconds; `stop_reason` (`agent_exit`, `wall_time`, `download_cap`, `monitor_failure`, `interrupted`, `runner_error`) and `error`; container exit code and OOM flag; peak RX and TX with the number of samples and failed reads (integers; 0 when nothing was sampled); token usage (Codex `turn.completed` sums; Claude `result` totals, falling back to assistant messages if the run was cut); input file hashes; deliverable presence, size and SHA-256; the reaper (pid, deadline, whether it was cancelled); credential metadata (see below).

## Network accounting

RX comes from the Docker Engine stats endpoint (`GET /containers/<id>/stats?one-shot=true`), the source behind `docker stats`, as exact bytes summed over the container's non-loopback interfaces. It counts everything the container receives: TLS overhead, DNS, and traffic to the model API. Two limits to remember:

- The cap is checked once per poll, so a fast download can pass 10 GB by up to one poll of traffic (at 100 MB/s and 30 s, about 3 GB). Use `--poll-interval-s 5` to tighten it.
- If the counters cannot be read three times in a row, the run stops with `monitor_failure` instead of running without a cap.
- Docker reports zero after the container exits, so the recorded peak is the last sample before exit, a lower bound. A run shorter than one poll has no sample (`samples: 0`), and RX and TX are then 0.

## Credential cleanup

A participant controls its run home while the container runs, so cleanup treats it as hostile. After the container is removed, and before anything is scanned or hashed:

- It restores owner access on the whole tree (`home/`, `work/`, `tmp/`), directories first and without following symlinks. A participant that runs `chmod 000 "$HOME"` hides nothing.
- It walks to the credential file one component at a time and refuses to cross a symlink. Every traversal root is checked the same way, so a symlinked `deliverable` or search root is skipped, and nothing reached through a symlink is read or chmodded.
- It reads the final credential file with a 1 MiB bound. A larger replacement is deleted by path without being read, and the report says so. Hashes and scans stream in 64 KiB to 1 MiB chunks, so a huge sparse file cannot exhaust memory.
- It deletes by path whatever the read did, then finds every other copy by size and SHA-256 (renamed or copied files included, for the original and the final content) and deletes those.
- It never raises. Problems go into `credentials.cleanup_errors`, and `left_in_run_dir` is true if a copy may remain or any part of the tree could not be checked or fixed.
- Interrupt signals are held until this and the `run.json` write are done.

Then it counts credential values, both the originals and the final (possibly refreshed) ones, in `transcript.jsonl`, `stderr.log`, every file under `home/` (the native session logs; package caches are skipped) and `work/deliverable`. `credentials.exposure` has the counts per location and relative file names; a name that contains a credential value is shown with the value replaced by `[credential]`, and error messages get the same treatment. Session logs are kept, because the Codex model is read from them; check `exposure` before sharing a run directory.

## Audit for leaked terms

```bash
uv run python skills/benchmark-run/scripts/audit_leakage.py \
  --terms-file <private-terms.txt> --run-dir <run-dir>
```

The terms file is private: one term per line, `#` comments allowed (original IDs, paper-only names, the paper title). Matching is case-insensitive, a space in a term matches any whitespace (including a line break), and JSON and JSONL files are searched on their decoded strings. The report lists `file:line  Txx  excerpt`, where `Txx` is the term's position in the terms file. The excerpt and the file path have every term replaced by its id, so terms never appear in output, even through a file name. `.json` files are read as whole documents (pretty printing is fine), `.jsonl` and malformed JSON line by line. Exit code 0 is clean, 1 means hits, 2 means bad input. Binary files, files over 64 MB and package directories (`.pixi`, `.cache`, `site-packages`, ...) are skipped and counted.

## Tests

```bash
uv run python -m unittest discover -s tests
```

`tests/test_benchmark_run.py` runs a stub agent (`tests/fixtures/stub_agent.py`, mounted over the real CLI path with `--agent-override`) inside the real image. It checks mounts, read-only enforcement, the overlay, hidden host paths, credential modes and absence from logs, the wall-time stop, the download-cap stop, the monitor-failure stop, interrupts, the runner-death backstop, hostile cleanup cases (symlinked credential directory, renamed and copied credentials, mode 000, printed and refreshed credentials, session logs), the metadata, and a two-run `--repeat` series. The repeat series logic (numbering, `repeats.json`, stop on interrupt or failure) is also tested without Docker, with the single-run function replaced. `tests/test_run_credentials.py` covers cleanup and the exposure scan, and `tests/test_container_reaper.py` the reaper, without Docker. The frozen-timeout test freezes `timeout` inside a stub and kills the runner. The Docker tests skip when the image is missing. No test calls a model.

## Known limits

- `/proc/self/mountinfo` and `findmnt` show the host-side path of each bind source. Use neutral names for the run directory and for the filtered gv-exports directory, because an agent can read them.
- Codex and Claude Code may refresh their OAuth tokens during a run. The refreshed token is deleted with the copy, so the host login may stop working if the provider rotates refresh tokens. `run.json` records `changed_during_run`.
- If the runner is killed with SIGKILL, the container still ends: by its own `timeout` at the wall limit plus 60 s, or, if a participant froze that, by the host reaper at the wall limit plus 120 s. The credential copy stays in `<run>/home` until you delete it, and the next run in that directory is refused.
- Apt packages come from a dated Ubuntu snapshot, so builds match; agents install tools on their own at run time, which counts toward the download cap.
