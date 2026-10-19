"""Read and check a ground-truth `signoff.json` before the scorer trusts it.

`review_ground_truth.py sign` is the only tool that should write the file. This
module applies the same checks when the scorer loads it, because a file can be
edited by hand or copied from another case after it was signed. The decision
values come from `review_ground_truth`; the rule that `approve` keeps the current
criticality mirrors its `_parse_section`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from review_ground_truth import CRITICALITIES, DECISIONS

__all__ = ["SignoffError", "read_signoff"]


class SignoffError(ValueError):
    """Raised when `signoff.json` is missing, stale or fails the signer's checks."""


def read_signoff(
    path: Path,
    *,
    sha256: str,
    case_id: str,
    current_criticality: Mapping[str, str],
) -> dict[str, dict]:
    """Return the sign-off decisions by assertion label after checking the file.

    Args:
        path: The `signoff.json` file.
        sha256: SHA-256 of the `paper_extraction.json` that is being scored.
        case_id: Name of the case directory; the sign-off must be made for it.
        current_criticality: Assertion label to its criticality in the ground
            truth. The sign-off must decide every one of these labels, once.

    Raises:
        SignoffError: The file is missing or unreadable, was signed for another
            ground truth, or has a problem; the message names every problem.
    """
    if not path.exists():
        raise SignoffError(f"no signoff.json at {path}")
    try:
        signoff = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise SignoffError(f"{path} is not readable JSON: {err}") from err
    if not isinstance(signoff, dict):
        raise SignoffError(f"{path} is not a JSON object")
    if signoff.get("ground_truth_sha256") != sha256:
        raise SignoffError(
            f"{path} was signed for another ground truth (sha256 differs)"
        )
    problems = _header_problems(signoff, case_id) + _decision_problems(
        signoff.get("assertions"), current_criticality
    )
    if problems:
        raise SignoffError(f"{path} is not valid:\n- " + "\n- ".join(problems))
    return signoff["assertions"]


def _header_problems(signoff: Mapping, case_id: str) -> list[str]:
    problems = []
    reviewer = signoff.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        problems.append("reviewer is missing or blank")
    if signoff.get("case_id") != case_id:
        problems.append(f"case_id is {signoff.get('case_id')!r}, expected {case_id!r}")
    if not _is_timestamp(signoff.get("signed_at")):
        problems.append("signed_at is missing or not an ISO-8601 time")
    return problems


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _decision_problems(
    assertions: object, current_criticality: Mapping[str, str]
) -> list[str]:
    if not isinstance(assertions, dict):
        return ["assertions is missing or not an object"]
    problems = [
        f"{label}: not an assertion of this ground truth"
        for label in assertions
        if label not in current_criticality
    ]
    for label, current in current_criticality.items():
        if label not in assertions:
            problems.append(f"{label}: no decision")
        else:
            problems.extend(_entry_problems(label, assertions[label], current))
    return problems


def _entry_problems(label: str, entry: object, current: str) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{label}: the decision is not an object"]
    problems = []
    decision, criticality = entry.get("decision"), entry.get("criticality")
    if decision not in DECISIONS:
        problems.append(
            f"{label}: decision {decision!r} is not one of {', '.join(DECISIONS)}"
        )
    if criticality not in CRITICALITIES:
        problems.append(
            f"{label}: criticality {criticality!r} is not one of "
            f"{', '.join(CRITICALITIES)}"
        )
    elif decision == "approve" and criticality != current:
        problems.append(
            f"{label}: approve keeps criticality {current!r}, but the sign-off says "
            f"{criticality!r}; a change needs decision edit"
        )
    if not isinstance(entry.get("note", ""), str):
        problems.append(f"{label}: note is not text")
    return problems
