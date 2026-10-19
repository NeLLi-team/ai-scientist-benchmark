#!/usr/bin/env python3
"""Write a blinded ground truth for a case that blind_case.py already blinded.

The scoring judges read the assertion and evidence text of the ground truth. This
script applies hand-written rewrites to the source extraction, so no name remains
that a participant cannot find in the provided data. It refuses to write anything
while a forbidden term is left in a judge-visible field. It then copies the
scoring schema and points the blinded `case.yaml` at the new files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CSAG_RELATIVE = Path("csag/paper_extraction.json")
SCHEMA_RELATIVE = Path("scoring/scoring_schema.json")
ALLOWED_FIELDS = {
    "assertions": ("assertion_text", "falsification_criteria"),
    "evidence_items": ("evidence_text",),
}
# Every field that reaches a judge prompt, per skills/benchmark-scoring/scripts:
# score_assertions._references and assertion_grading.build_packet.
JUDGE_FIELDS = {
    "assertions": ("label", "assertion_text", "falsification_criteria", "claim_role"),
    "evidence_items": ("evidence_text",),
    "evidence_links": ("polarity", "strength"),
}
GROUND_TRUTH_KEYS = frozenset({"reused_ground_truth", "blinded_ground_truth"})
DESCRIPTION = (
    "Blinded copy of the source case. The ground truth is a blinded rewrite of "
    "the source extraction and the scoring schema is copied from the source "
    "case; evaluator-side de-blinding uses private/blinding_map.json."
)


class RewriteError(ValueError):
    """The rewrites or the blinded text break a rule, so nothing is written."""


def blind_ground_truth(
    source_case: Path, blind_case: Path, rewrites_file: Path, terms_file: Path
) -> tuple[int, int]:
    """Write the blinded ground truth; return the rewritten (assertions, evidence).

    Every check and every output is built in memory first. The three files are
    written only after all checks pass, so a refusal leaves the blind case as it was.
    """
    if source_case.resolve() == blind_case.resolve():
        raise RewriteError(
            f"source and blind case are the same directory: {blind_case}"
        )
    source_csag = _read_bytes(source_case / CSAG_RELATIVE)
    schema = _read_bytes(source_case / SCHEMA_RELATIVE)
    rewrites_raw = _read_bytes(rewrites_file)
    terms = _load_terms(terms_file)
    case_path = blind_case / "case.yaml"
    case = _load_case(case_path)

    extraction = _parse_json(source_csag, source_case / CSAG_RELATIVE)
    rewrites = _parse_json(rewrites_raw, rewrites_file)
    _check_structure(extraction)
    _apply_rewrites(extraction, rewrites)
    _check_value_types(extraction)
    _assert_no_forbidden_terms(extraction, terms)

    csag_out = (json.dumps(extraction, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    root = blind_case.parent.parent
    record: dict[str, object] = {"source_case": source_case.name}
    for name, path, data in (
        ("source_csag", source_case / CSAG_RELATIVE, source_csag),
        ("rewrites", rewrites_file, rewrites_raw),
        ("csag", blind_case / CSAG_RELATIVE, csag_out),
        ("scoring_schema", blind_case / SCHEMA_RELATIVE, schema),
    ):
        record[name] = {
            "path": os.path.relpath(path, root),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    case_out = _replace_ground_truth_block(case, record)

    for path, data in (
        (blind_case / CSAG_RELATIVE, csag_out),
        (blind_case / SCHEMA_RELATIVE, schema),
        (case_path, case_out),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return len(rewrites.get("assertions", {})), len(rewrites.get("evidence_items", {}))


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as err:
        raise RewriteError(f"cannot read {path}: {err.strerror}") from err


def _parse_json(data: bytes, path: Path) -> dict:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except ValueError as err:  # JSONDecodeError and UnicodeDecodeError
        raise RewriteError(f"cannot parse {path} as JSON: {err}") from err
    if not isinstance(parsed, dict):
        raise RewriteError(f"{path} must hold a JSON object")
    return parsed


def _load_case(path: Path) -> dict:
    """Read the blind `case.yaml`; it must hold a ground-truth block to replace."""
    try:
        case = yaml.safe_load(_read_bytes(path).decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as err:
        raise RewriteError(f"cannot parse {path} as YAML: {err}") from err
    if not isinstance(case, dict) or not GROUND_TRUTH_KEYS & set(case):
        raise RewriteError(
            f"{path} must be a mapping with a reused_ground_truth or "
            "blinded_ground_truth block"
        )
    return case


def _check_structure(extraction: dict) -> None:
    """Raise RewriteError unless the sections are lists of identifiable records."""
    for section in ALLOWED_FIELDS:
        records = extraction.get(section)
        if not isinstance(records, list) or not all(
            isinstance(record, dict) and (record.get("label") or record.get("id"))
            for record in records
        ):
            raise RewriteError(
                f"{section} must be a list of records with a label or id"
            )
        labels = [_record_label(record) for record in records]
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        if duplicates:
            raise RewriteError(f"ambiguous {section} labels: {duplicates}")


def _record_label(record: dict) -> str:
    """Return the record's `label`, or the last path segment of its `id`."""
    return record.get("label") or record["id"].rsplit("/", 1)[-1]


def _apply_rewrites(extraction: dict, rewrites: dict) -> None:
    """Replace the allowed fields in place; raise RewriteError on any unknown name."""
    unknown_sections = sorted(set(rewrites) - set(ALLOWED_FIELDS))
    if unknown_sections:
        raise RewriteError(f"unknown rewrite sections: {unknown_sections}")
    for section, entries in rewrites.items():
        records = {_record_label(record): record for record in extraction[section]}
        if not isinstance(entries, dict):
            raise RewriteError(f"{section} rewrites must be an object")
        unknown_labels = sorted(set(entries) - set(records))
        if unknown_labels:
            raise RewriteError(f"unknown {section} labels: {unknown_labels}")
        for label, fields in entries.items():
            if not isinstance(fields, dict):
                raise RewriteError(f"rewrite for {label} must be an object")
            unknown_fields = sorted(set(fields) - set(ALLOWED_FIELDS[section]))
            if unknown_fields:
                raise RewriteError(f"unknown fields for {label}: {unknown_fields}")
            records[label].update(fields)


def _check_value_types(extraction: dict) -> None:
    """Raise RewriteError for judge text that is not a non-empty string (or list)."""
    problems: list[str] = []
    for section, fields in ALLOWED_FIELDS.items():
        for record in extraction[section]:
            for field in fields:
                value = record.get(field)
                if not _is_valid_text(field, value):
                    problems.append(f"{_record_label(record)}.{field}: {value!r}")
    if problems:
        raise RewriteError(
            "need a non-empty string (a list of them for falsification_criteria):\n"
            + "\n".join(problems)
        )


def _is_valid_text(field: str, value: object) -> bool:
    if field == "falsification_criteria":
        return isinstance(value, list) and all(_is_text(item) for item in value)
    return _is_text(value)


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _load_terms(path: Path) -> list[str]:
    """Read one term per line; blank lines and `#` comments are ignored."""
    try:
        lines = _read_bytes(path).decode("utf-8").splitlines()
    except UnicodeDecodeError as err:
        raise RewriteError(f"cannot read {path} as UTF-8: {err}") from err
    terms = [
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    ]
    if not terms:
        raise RewriteError(f"terms file holds no terms: {path}")
    return terms


def _assert_no_forbidden_terms(extraction: dict, terms: Sequence[str]) -> None:
    """Raise RewriteError listing every term left in a field a judge prompt shows."""
    hits: list[str] = []
    for section, fields in JUDGE_FIELDS.items():
        for record in extraction.get(section, []):
            for field in fields:
                text = _field_text(record.get(field)).lower()
                hits.extend(
                    f"{_record_label(record)}.{field}: {term}"
                    for term in terms
                    if term.lower() in text
                )
    if hits:
        raise RewriteError("forbidden terms remain:\n" + "\n".join(hits))


def _field_text(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return "" if value is None else str(value)


def _replace_ground_truth_block(case: dict, record: dict[str, object]) -> bytes:
    """Return `case.yaml` with the ground-truth block and description replaced."""
    updated = {}
    for key, value in case.items():
        if key in GROUND_TRUTH_KEYS:
            updated["blinded_ground_truth"] = record
        else:
            updated[key] = DESCRIPTION if key == "description" else value
    return yaml.safe_dump(updated, sort_keys=False, width=88).encode("utf-8")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-case",
        type=Path,
        required=True,
        help="Case with csag/paper_extraction.json and scoring/scoring_schema.json",
    )
    parser.add_argument(
        "--blind-case",
        type=Path,
        required=True,
        help="Case made by blind_case.py; receives the blinded ground truth",
    )
    parser.add_argument(
        "--rewrites", type=Path, required=True, help="JSON file with the rewrites"
    )
    parser.add_argument(
        "--terms-file",
        type=Path,
        required=True,
        help="Forbidden terms, one per line; blank lines and # comments are ignored",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Write the blinded ground truth; return 0, or 2 when the input is refused."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        n_assertions, n_evidence = blind_ground_truth(
            args.source_case.resolve(),
            args.blind_case.resolve(),
            args.rewrites.resolve(),
            args.terms_file,
        )
    except RewriteError as err:
        logger.error("%s", err)
        return 2
    logger.info("rewrote %d assertions and %d evidence items", n_assertions, n_evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
