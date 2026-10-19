#!/usr/bin/env python3
"""Search participant run outputs for private benchmark terms.

The lead keeps a private terms file (original IDs, paper-only names, the paper
title). This script scans transcripts and workspaces for those terms and prints
each hit as file, line, term id and a redacted excerpt. It never prints a term:
hits carry ids such as T03 that point at the line number in the terms file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

DEFAULT_MAX_FILE_MB = 64
BINARY_SNIFF_BYTES = 8192
EXCERPT_WIDTH = 120
PRUNED_DIRS = frozenset(
    {
        ".pixi",
        ".git",
        ".cache",
        ".conda",
        ".rattler",
        ".npm",
        "node_modules",
        "__pycache__",
        "site-packages",
    }
)
JSON_SUFFIXES = frozenset({".json", ".jsonl"})
RUN_DIR_TARGETS = ("transcript.jsonl", "stderr.log", "work", "home")


class AuditError(ValueError):
    """Raised for unusable audit inputs, such as a missing terms file."""


@dataclass(frozen=True, slots=True)
class Hit:
    """One term match: where it is and a redacted view of the line.

    `display_path` is the path with every term replaced by its id; reports print
    it, never `path`, because a file name can hold a private term.
    """

    path: Path
    display_path: str
    line: int
    term_id: str
    excerpt: str


@dataclass(slots=True)
class AuditResult:
    """Everything the audit found, plus what it could not read."""

    n_terms: int
    hits: list[Hit] = field(default_factory=list)
    files_scanned: int = 0
    skipped_binary: int = 0
    skipped_large: int = 0


def load_terms(path: Path) -> list[str]:
    """Read one term per line; blank lines and `#` comments are ignored."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as err:
        raise AuditError(f"cannot read terms file: {err.strerror}") from err
    terms = [
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    ]
    if not terms:
        raise AuditError("terms file holds no terms")
    return terms


def audit(
    targets: Sequence[Path],
    terms: Sequence[str],
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_MB * 1024 * 1024,
    skip: Path | None = None,
) -> AuditResult:
    """Scan files and directories for the terms; `skip` is never scanned."""
    patterns = [_compile(term) for term in terms]
    result = AuditResult(n_terms=len(terms))
    skip_resolved = skip.resolve() if skip else None
    for path in _iter_files(targets):
        if skip_resolved is not None and path.resolve() == skip_resolved:
            continue
        _scan_file(path, patterns, max_file_bytes, result)
    return result


def _compile(term: str) -> re.Pattern[str]:
    """Match a term case-insensitively, letting any whitespace join its words."""
    words = [re.escape(word) for word in term.split()]
    return re.compile(r"\s+".join(words), re.IGNORECASE)


def _iter_files(targets: Sequence[Path]) -> Iterator[Path]:
    """Yield regular files under the targets, pruning environment directories."""
    for target in targets:
        if target.is_file():
            yield target
            continue
        for directory, subdirs, names in os.walk(target):
            subdirs[:] = sorted(name for name in subdirs if name not in PRUNED_DIRS)
            for name in sorted(names):
                path = Path(directory) / name
                if path.is_file() and not path.is_symlink():
                    yield path


def _scan_file(
    path: Path,
    patterns: Sequence[re.Pattern[str]],
    max_file_bytes: int,
    result: AuditResult,
) -> None:
    """Scan one file, updating the result counters and hit list."""
    if path.stat().st_size > max_file_bytes:
        result.skipped_large += 1
        return
    raw = path.read_bytes()
    if b"\0" in raw[:BINARY_SNIFF_BYTES]:
        result.skipped_binary += 1
        return
    result.files_scanned += 1
    text = raw.decode("utf-8", errors="replace")
    seen: set[tuple[int, str]] = set()
    for line_of, chunk in _chunks(path, text):
        for hit in _scan_chunk(path, line_of, chunk, patterns):
            if (hit.line, hit.term_id) not in seen:
                seen.add((hit.line, hit.term_id))
                result.hits.append(hit)


def _chunks(path: Path, text: str) -> Iterator[tuple[Callable[[int], int], str]]:
    """Split a file into chunks to search, each with a line-number function.

    The function maps the number of newlines before a match, inside the chunk,
    to the file's line number. JSON files are searched on their decoded strings,
    so a title that a model wrapped with a newline inside a JSON string still
    matches. A `.json` file is read as one document, which handles pretty
    printing; a file that is not one document, and every `.jsonl` file, is read
    line by line. Newlines inside a decoded string become spaces so a hit keeps
    the line of its JSON value.
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            for leaf in _json_strings(document):
                yield partial(_locate_json_string, text, leaf), leaf.replace("\n", " ")
            return
    if suffix in JSON_SUFFIXES:
        for line_no, line in enumerate(text.split("\n"), start=1):
            try:
                decoded = " ".join(_json_strings(json.loads(line))).replace("\n", " ")
            except json.JSONDecodeError:
                decoded = line
            yield partial(_fixed_line, line_no), decoded
        return
    yield _plain_line, text


def _fixed_line(line_no: int, _offset: int) -> int:
    return line_no


def _plain_line(offset: int) -> int:
    return 1 + offset


def _locate_json_string(text: str, leaf: str, _offset: int) -> int:
    """Line of a string value in the raw JSON text; 1 when it cannot be found."""
    for encoded in (json.dumps(leaf), json.dumps(leaf, ensure_ascii=False)):
        index = text.find(encoded)
        if index >= 0:
            return text.count("\n", 0, index) + 1
    return 1


def _json_strings(value: object) -> Iterator[str]:
    """Yield every key and string value in a parsed JSON document."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)


def _scan_chunk(
    path: Path,
    line_of: Callable[[int], int],
    text: str,
    patterns: Sequence[re.Pattern[str]],
) -> Iterator[Hit]:
    """Yield a hit for each term match in the chunk."""
    redacted_lines = _redact(text, patterns).split("\n")
    display_path = _redact(str(path), patterns)
    for index, pattern in enumerate(patterns, start=1):
        for match in pattern.finditer(text):
            offset = text.count("\n", 0, match.start())
            yield Hit(
                path=path,
                display_path=display_path,
                line=line_of(offset),
                term_id=_term_id(index),
                excerpt=_excerpt(redacted_lines[offset]),
            )


def _redact(text: str, patterns: Sequence[re.Pattern[str]]) -> str:
    """Replace every match by its term id, keeping the line count unchanged."""
    for index, pattern in enumerate(patterns, start=1):
        label = f"[{_term_id(index)}]"
        text = pattern.sub(
            lambda m, label=label: label + "\n" * m.group().count("\n"), text
        )
    return text


def _term_id(index: int) -> str:
    return f"T{index:02d}"


def _excerpt(redacted_line: str) -> str:
    """Trim a redacted line to a short window around its first term marker."""
    line = redacted_line.strip()
    if len(line) <= EXCERPT_WIDTH:
        return line
    marker = line.find("[T")
    start = max(0, marker - EXCERPT_WIDTH // 2)
    return line[start : start + EXCERPT_WIDTH]


def format_report(result: AuditResult) -> str:
    """Render the audit as text for the terminal."""
    header = (
        f"leakage audit: {result.n_terms} terms, {result.files_scanned} files "
        f"scanned, {len(result.hits)} hits, {result.skipped_binary} binary and "
        f"{result.skipped_large} oversized files skipped"
    )
    lines = [header]
    lines.extend(
        f"  {hit.display_path}:{hit.line}  {hit.term_id}  {hit.excerpt}"
        for hit in result.hits
    )
    return "\n".join(lines) + "\n"


def report_dict(result: AuditResult) -> dict:
    """The audit as a JSON-ready dict; it holds term ids, never terms."""
    return {
        "n_terms": result.n_terms,
        "files_scanned": result.files_scanned,
        "skipped_binary": result.skipped_binary,
        "skipped_large": result.skipped_large,
        "hits": [
            {
                "path": hit.display_path,
                "line": hit.line,
                "term_id": hit.term_id,
                "excerpt": hit.excerpt,
            }
            for hit in result.hits
        ],
    }


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--terms-file",
        type=Path,
        required=True,
        help="Private file with one term per line.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        action="append",
        default=[],
        help="Run dir from run_participant.py: scans transcript, stderr, work, home.",
    )
    parser.add_argument(
        "--json-out", type=Path, help="Write the report as JSON (term ids only)."
    )
    parser.add_argument(
        "--max-file-mb",
        type=int,
        default=DEFAULT_MAX_FILE_MB,
        help="Skip files larger than this.",
    )
    parser.add_argument(
        "paths", nargs="*", type=Path, help="Extra files or directories to scan."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the audit; exit 0 when clean, 1 on hits, 2 on unusable input."""
    args = parse_args(argv)
    targets = list(args.paths)
    for run_dir in args.run_dir:
        targets.extend(
            run_dir / name for name in RUN_DIR_TARGETS if (run_dir / name).exists()
        )
    if not targets:
        print(
            "audit_leakage: nothing to scan: pass --run-dir or paths", file=sys.stderr
        )
        return 2
    try:
        result = audit(
            targets,
            load_terms(args.terms_file),
            max_file_bytes=args.max_file_mb * 1024 * 1024,
            skip=args.terms_file,
        )
    except AuditError as err:
        print(f"audit_leakage: {err}", file=sys.stderr)
        return 2
    sys.stdout.write(format_report(result))
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report_dict(result), indent=2) + "\n", encoding="utf-8"
        )
    return 1 if result.hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
