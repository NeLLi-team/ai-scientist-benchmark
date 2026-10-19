#!/usr/bin/env python3
"""Stand-in for the claude and codex CLIs, used by the assertion scorer tests.

The tests symlink this file as `claude` and `codex` into a directory at the
front of PATH. Each model call reads the judge packet from stdin, answers like
a literal judge (same text is matched, a negated copy is contradicted, anything
else is missing) and logs the call to `calls.jsonl` next to the symlink. Options
come from `stub_config.json` in the same directory, keyed by CLI name. No model
is called.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

STUB_DIR = Path(sys.argv[0]).parent
AGENT = Path(sys.argv[0]).name
FAKE_VERSIONS = {"claude": "9.9.9-stub (Claude Code)", "codex": "codex-cli 0.0.1-stub"}
FAKE_MODELS = {"claude": "claude-stub-sonnet", "codex": "gpt-stub-astra"}
UNCITED_QUOTE = "text that the candidate never wrote"
REFRESHED_ACCESS = "REFRESHED-ACCESS-TOKEN"


def main() -> int:
    """Answer `--version`, or judge the packet on stdin like the real CLI."""
    if "--version" in sys.argv[1:]:
        print(FAKE_VERSIONS[AGENT])
        return 0
    packet_text = sys.stdin.read()
    options = applicable_options(load_options(), packet_text)
    log_call(packet_text)
    if AGENT == "codex" and options.get("refresh_auth"):
        refresh_login(options["refresh_auth"])
    if options.get("fail"):
        print("stub judge failure", file=sys.stderr)
        return 3
    payload = json.loads(packet_text.split("<packet>")[1].split("</packet>")[0])
    answer = "not json at all" if options.get("garbage") else judge(payload, options)
    if AGENT == "claude":
        emit_claude(answer)
    else:
        emit_codex(answer)
    return 0


def load_options() -> dict:
    """Options for this CLI from `stub_config.json`, or none."""
    path = STUB_DIR / "stub_config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get(AGENT, {})


def applicable_options(options: dict, packet_text: str) -> dict:
    """The options, or none when `when` names text the candidate does not contain."""
    marker = options.get("when")
    candidate_part = packet_text.rsplit('"candidate":', maxsplit=1)[-1]
    return options if marker is None or marker in candidate_part else {}


def log_call(packet_text: str) -> None:
    """Append what this call saw: argv, cwd, environment names, home and packet."""
    codex_home = os.environ.get("CODEX_HOME")
    auth = Path(codex_home) / "auth.json" if codex_home else None
    entry = {
        "agent": AGENT,
        "argv": sys.argv[1:],
        "cwd": str(Path.cwd()),
        "cwd_entries": sorted(path.name for path in Path.cwd().iterdir()),
        "env": dict(os.environ),
        "auth_is_symlink": auth is not None and auth.is_symlink(),
        "auth_readable": auth is not None and auth.exists(),
        "packet": packet_text,
        "packet_sha256": hashlib.sha256(packet_text.encode("utf-8")).hexdigest(),
    }
    with (STUB_DIR / "calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def judge(payload: dict, options: dict) -> str:
    """Return the verdict JSON text for the packet, shaped by the options."""
    candidate = payload["candidate"]
    texts = {item["text"]: item["id"] for item in candidate["assertions"]}
    verdicts = []
    if options.get("empty"):
        return json.dumps({"verdicts": verdicts})
    for reference in payload["reference_assertions"]:
        if reference["assertion_id"] in options.get("drop", []):
            continue
        verdict, cited = literal_verdict(reference["text"], texts)
        verdict = options.get("force", {}).get(reference["assertion_id"], verdict)
        if verdict != "missing" and cited is None:
            cited = candidate["assertions"][0]["text"]
        quote = UNCITED_QUOTE if options.get("bad_quote") else cited or ""
        ids = [texts[cited]] if cited in texts and verdict != "missing" else []
        verdicts.append(entry(reference["assertion_id"], verdict, ids, quote))
    if options.get("unknown_id"):
        verdicts.append(entry("A9999", "matched", [], UNCITED_QUOTE))
    if options.get("invalid_candidate_id") and verdicts:
        verdicts[0]["candidate_ids"].append("CA99")
    return json.dumps({"verdicts": verdicts})


def literal_verdict(
    reference_text: str, texts: dict[str, str]
) -> tuple[str, str | None]:
    """Matched for the same text, contradicted for a wrapped copy, else missing."""
    if reference_text in texts:
        return "matched", reference_text
    for text in texts:
        if reference_text in text:
            return "contradicted", text
    return "missing", None


def entry(assertion_id: str, verdict: str, ids: list[str], quote: str) -> dict:
    """One verdict entry in the judge output schema."""
    return {
        "assertion_id": assertion_id,
        "verdict": verdict,
        "candidate_ids": ids,
        "quote": quote,
        "reason": f"stub {verdict}",
    }


def refresh_login(mode: str) -> None:
    """Replace the linked login file the way a token refresh would, as `mode` says."""
    login = Path(os.environ["CODEX_HOME"]) / "auth.json"
    current = json.loads(login.read_text(encoding="utf-8"))
    refreshed = {**current, "last_refresh": "t1"}
    refreshed["tokens"] = {**current["tokens"], "access_token": REFRESHED_ACCESS}
    content = {
        "valid": json.dumps(refreshed),
        "same": login.read_text(encoding="utf-8"),
        "empty": "",
        "garbage": "not json",
        "missing_keys": json.dumps({"auth_mode": "chatgpt"}),
    }[mode]
    replacement = login.with_name("auth.json.new")
    replacement.write_text(content, encoding="utf-8")
    replacement.replace(login)


def emit_claude(answer: str) -> None:
    """Print the single JSON result `claude -p --output-format json` prints."""
    structured = None if answer.startswith("not json") else json.loads(answer)
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": answer,
        "structured_output": structured,
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "modelUsage": {FAKE_MODELS["claude"]: {"inputTokens": 100}},
    }
    print(json.dumps(result))


def emit_codex(answer: str) -> None:
    """Write the last message and the session rollout, print the usage event."""
    argv = sys.argv[1:]
    Path(argv[argv.index("-o") + 1]).write_text(answer, encoding="utf-8")
    codex_home = Path(os.environ["CODEX_HOME"])
    rollout = codex_home / "sessions" / "2026" / "10" / "19" / "rollout-stub.jsonl"
    rollout.parent.mkdir(parents=True)
    context = {"type": "turn_context", "payload": {"model": FAKE_MODELS["codex"]}}
    rollout.write_text(json.dumps(context) + "\n", encoding="utf-8")
    print(json.dumps({"type": "thread.started", "thread_id": "stub-thread"}))
    usage = {"input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 20}
    print(json.dumps({"type": "turn.completed", "usage": usage}))


if __name__ == "__main__":
    raise SystemExit(main())
