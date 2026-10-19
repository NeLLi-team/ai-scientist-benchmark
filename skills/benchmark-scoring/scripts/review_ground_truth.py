#!/usr/bin/env python3
"""Review a ground-truth CSAG by hand and record the signed result.

`packet` writes a Markdown review sheet with one section per assertion. A
researcher fills in the decision, criticality and note lines. `sign` parses the
filled sheet and writes `<case>/csag/signoff.json`, which pins the sign-off to
the SHA-256 of `paper_extraction.json`. The scorer refuses an unsigned or
changed ground truth, so only a researcher's sheet makes a case scoreable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DECISIONS = ("approve", "edit", "reject")
CRITICALITIES = ("core", "major", "supporting", "background")
EXTRACTION_RELPATH = Path("csag") / "paper_extraction.json"
SIGNOFF_RELPATH = Path("csag") / "signoff.json"

SECTION_HEADING = re.compile(r"(?m)^## (\S+)[ \t]*$")
SHEET_HASH = re.compile(r"(?m)^- ground_truth_sha256: (\S+)[ \t]*$")
DECISION_LINE = re.compile(r"(?m)^decision:[ \t]*(.*?)[ \t]*$")
CRITICALITY_LINE = re.compile(r"(?m)^criticality:[ \t]*(.*?)[ \t]*$")
NOTE_BLOCK = re.compile(r"(?ms)^note:[ \t]*(.*)\Z")

SHEET_INTRO = """\
For each assertion below, fill in the three lines at the end of its section:

- `decision:` one of `approve`, `edit` or `reject`. `approve` keeps the assertion
  and its criticality as shown. `edit` keeps it with the criticality you enter
  and is for any change you judge necessary (explain it in the note). `reject`
  drops the assertion from scoring.
- `criticality:` one of `core`, `major`, `supporting` or `background`. It starts
  at the current value. Change it only with `edit`.
- `note:` free text, optional. It may run over several lines.

Do not change the headings or the `ground_truth_sha256` line. `sign` refuses a
sheet with a blank or unknown decision.
"""


class ReviewError(ValueError):
    """The ground truth or the review sheet cannot be used."""


@dataclass(frozen=True, slots=True)
class Decision:
    """One reviewer decision for one assertion."""

    decision: str
    criticality: str
    note: str


def write_packet(case_dir: Path, out: Path) -> None:
    """Write the review sheet for `case_dir` to `out`."""
    extraction, sha256 = _load_extraction(case_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_packet(extraction, case_dir.name, sha256), encoding="utf-8")


def sign_off(
    case_dir: Path,
    sheet: Path,
    *,
    reviewer: str,
    signed_at: datetime | None = None,
) -> Path:
    """Parse the filled `sheet` and write the sign-off file; returns its path.

    Raises:
        ReviewError: reviewer blank, ground truth changed since the packet, or
            any assertion with a missing, blank or invalid entry. Nothing is
            written in that case.
    """
    if not reviewer.strip():
        raise ReviewError("reviewer name is blank")
    extraction, sha256 = _load_extraction(case_dir)
    text = sheet.read_text(encoding="utf-8")
    sheet_sha = SHEET_HASH.search(text)
    if sheet_sha is None or sheet_sha.group(1) != sha256:
        raise ReviewError(
            "ground truth changed since the sheet was made (or the sheet has no "
            "ground_truth_sha256 line); write a new packet and review it again"
        )
    assertions = _current_criticality(extraction)
    decisions = parse_sheet(text, assertions)
    moment = signed_at or datetime.now(UTC)
    signoff = {
        "case_id": case_dir.name,
        "ground_truth_sha256": sha256,
        "reviewer": reviewer.strip(),
        "signed_at": moment.astimezone(UTC).isoformat(timespec="seconds"),
        "assertions": {
            label: {
                "decision": entry.decision,
                "criticality": entry.criticality,
                "note": entry.note,
            }
            for label, entry in decisions.items()
        },
    }
    path = case_dir / SIGNOFF_RELPATH
    _write_json_atomic(path, signoff)
    return path


def render_packet(extraction: Mapping, case_id: str, sha256: str) -> str:
    """Render the review sheet as Markdown."""
    items = {item["id"]: item for item in extraction.get("evidence_items", [])}
    links = extraction.get("evidence_links", [])
    parts = [
        f"# Ground-truth review: {case_id}\n",
        f"- case_id: {case_id}",
        f"- ground_truth_sha256: {sha256}",
        f"- title: {_flat(extraction.get('title', ''))}\n",
        SHEET_INTRO,
    ]
    for assertion in extraction.get("assertions", []):
        linked = [link for link in links if link.get("assertion") == assertion["id"]]
        parts.append(_render_assertion(assertion, linked, items))
    return "\n".join(parts)


def parse_sheet(
    text: str, current_criticality: Mapping[str, str]
) -> dict[str, Decision]:
    """Read every assertion section of a filled sheet.

    Args:
        text: The sheet.
        current_criticality: Assertion label to the criticality in the ground
            truth, in ground-truth order. These are the labels the sheet must
            cover exactly once.

    Raises:
        ReviewError: naming every problem found.
    """
    _preamble, *rest = SECTION_HEADING.split(text)
    sections = dict(zip(rest[::2], rest[1::2], strict=True))
    problems = _structure_problems(rest[::2], current_criticality)
    decisions: dict[str, Decision] = {}
    for label, current in current_criticality.items():
        if label not in sections:
            continue
        try:
            decisions[label] = _parse_section(sections[label], current)
        except ValueError as err:
            problems.append(f"{label}: {err}")
    if problems:
        raise ReviewError("sheet not accepted:\n- " + "\n- ".join(problems))
    return decisions


def _render_assertion(
    assertion: Mapping, linked: Sequence[Mapping], items: Mapping[str, Mapping]
) -> str:
    """One assertion section: what it claims, its evidence, and blank decision lines."""
    criticality = assertion.get("criticality", "")
    lines = [
        f"\n## {_label(assertion)}\n",
        f"- text: {_flat(assertion.get('assertion_text', ''))}",
        f"- claim_role: {assertion.get('claim_role', '')}",
        f"- current_criticality: {criticality}",
        "- falsification_criteria:",
        *(f"  - {_flat(item)}" for item in assertion.get("falsification_criteria", [])),
        "- source_quotes:",
        *_quotes(assertion, indent="  "),
        "- linked_evidence:",
    ]
    if not linked:
        lines.append("  - (none linked)")
    for link in linked:
        item = items.get(link.get("evidence_item", ""), {})
        kind = item.get("evidence_type", "")
        effect = f"{link.get('polarity', '')}, {link.get('strength', '')}"
        lines += [
            f"  - {_label(link)} [{_label(item)}, {kind}]: {effect}",
            f"    text: {_flat(item.get('evidence_text', ''))}",
            *_quotes(item, indent="    "),
        ]
    lines += [
        "",
        "<!-- decision: approve | edit | reject -->",
        "decision:",
        f"criticality: {criticality}",
        "note:",
    ]
    return "\n".join(lines)


def _quotes(record: Mapping, *, indent: str) -> list[str]:
    """Quoted source spans of an assertion or evidence item."""
    return [
        f"{indent}> {_flat(span['exact_text'])}"
        for span in record.get("text_spans", [])
        if span.get("exact_text")
    ]


def _structure_problems(
    labels: Sequence[str], current_criticality: Mapping[str, str]
) -> list[str]:
    """Sections that are repeated, unknown or missing."""
    problems = [
        f"{label}: section appears more than once"
        for label in dict.fromkeys(labels)
        if labels.count(label) > 1
    ]
    problems += [
        f"{label}: not an assertion of this ground truth"
        for label in dict.fromkeys(labels)
        if label not in current_criticality
    ]
    problems += [
        f"{label}: section is missing"
        for label in current_criticality
        if label not in labels
    ]
    return problems


def _parse_section(section: str, current: str) -> Decision:
    """Read the decision, criticality and note lines of one section."""
    for field, pattern in (
        ("decision", DECISION_LINE),
        ("criticality", CRITICALITY_LINE),
    ):
        if len(pattern.findall(section)) > 1:
            raise ValueError(f"{field} line appears more than once")
    decision_match = DECISION_LINE.search(section)
    decision = decision_match.group(1).strip().lower() if decision_match else ""
    if decision not in DECISIONS:
        raise ValueError(
            f"decision {decision!r} is not one of {', '.join(DECISIONS)} "
            "(blank or unfilled)"
        )
    criticality_match = CRITICALITY_LINE.search(section)
    criticality = (
        criticality_match.group(1).strip().lower() if criticality_match else ""
    ) or current
    if criticality not in CRITICALITIES:
        raise ValueError(
            f"criticality {criticality!r} is not one of {', '.join(CRITICALITIES)}"
        )
    if decision == "approve" and criticality != current:
        raise ValueError(
            f"approve keeps criticality {current!r}, but the sheet says "
            f"{criticality!r}; use decision edit"
        )
    note_match = NOTE_BLOCK.search(section)
    return Decision(
        decision, criticality, note_match.group(1).strip() if note_match else ""
    )


def _current_criticality(extraction: Mapping) -> dict[str, str]:
    """Assertion label to the criticality in the ground truth."""
    return {
        _label(assertion): assertion.get("criticality", "")
        for assertion in extraction.get("assertions", [])
    }


def _label(record: Mapping) -> str:
    """The short label of a CSAG record, falling back to its id suffix."""
    return record.get("label") or str(record.get("id", "")).rsplit("/", 1)[-1]


def _flat(text: str) -> str:
    """Collapse whitespace so a value stays on one line."""
    return " ".join(str(text).split())


def _load_extraction(case_dir: Path) -> tuple[dict, str]:
    """Read `paper_extraction.json`; returns its content and SHA-256."""
    path = case_dir / EXTRACTION_RELPATH
    try:
        raw = path.read_bytes()
    except OSError as err:
        raise ReviewError(f"cannot read {path}: {err.strerror}") from err
    try:
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    except json.JSONDecodeError as err:
        raise ReviewError(f"{path} is not valid JSON: {err}") from err


def _write_json_atomic(path: Path, content: Mapping) -> None:
    """Write JSON through a temporary file so a reader never sees half of it."""
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(content, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    packet = commands.add_parser("packet", help="Write the review sheet.")
    packet.add_argument(
        "--case-dir", type=Path, required=True, help="e.g. ground_truth/06mega."
    )
    packet.add_argument(
        "--out", type=Path, required=True, help="Markdown sheet to write."
    )
    sign = commands.add_parser("sign", help="Write csag/signoff.json from a sheet.")
    sign.add_argument(
        "--case-dir", type=Path, required=True, help="e.g. ground_truth/06mega."
    )
    sign.add_argument("--sheet", type=Path, required=True, help="The filled sheet.")
    sign.add_argument("--reviewer", required=True, help="Name of the researcher.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run `packet` or `sign`; returns 0 on success and 2 on a refused input."""
    args = parse_args(argv)
    try:
        if args.command == "packet":
            write_packet(args.case_dir, args.out)
            print(f"wrote {args.out}")
        else:
            path = sign_off(args.case_dir, args.sheet, reviewer=args.reviewer)
            print(f"wrote {path}")
    except (ReviewError, OSError) as err:
        print(f"review_ground_truth: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
