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
import shutil
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
    """Write the blinded ground truth; return the rewritten (assertions, evidence)."""
    for required in (
        source_case / CSAG_RELATIVE,
        source_case / SCHEMA_RELATIVE,
        blind_case / "case.yaml",
        rewrites_file,
        terms_file,
    ):
        if not required.is_file():
            raise RewriteError(f"missing input file: {required}")
    extraction = json.loads((source_case / CSAG_RELATIVE).read_text("utf-8"))
    rewrites = json.loads(rewrites_file.read_text(encoding="utf-8"))

    _apply_rewrites(extraction, rewrites)
    _assert_no_forbidden_terms(extraction, _load_terms(terms_file))

    csag_out = blind_case / CSAG_RELATIVE
    schema_out = blind_case / SCHEMA_RELATIVE
    csag_out.parent.mkdir(parents=True, exist_ok=True)
    schema_out.parent.mkdir(parents=True, exist_ok=True)
    csag_out.write_text(
        json.dumps(extraction, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    shutil.copyfile(source_case / SCHEMA_RELATIVE, schema_out)
    _update_case_yaml(blind_case, source_case, rewrites_file)
    return len(rewrites.get("assertions", {})), len(rewrites.get("evidence_items", {}))


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
        unknown_labels = sorted(set(entries) - set(records))
        if unknown_labels:
            raise RewriteError(f"unknown {section} labels: {unknown_labels}")
        for label, fields in entries.items():
            unknown_fields = sorted(set(fields) - set(ALLOWED_FIELDS[section]))
            if unknown_fields:
                raise RewriteError(f"unknown fields for {label}: {unknown_fields}")
            records[label].update(fields)


def _load_terms(path: Path) -> list[str]:
    """Read one term per line; blank lines and `#` comments are ignored."""
    lines = path.read_text(encoding="utf-8").splitlines()
    terms = [
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    ]
    if not terms:
        raise RewriteError(f"terms file holds no terms: {path}")
    return terms


def _assert_no_forbidden_terms(extraction: dict, terms: Sequence[str]) -> None:
    """Raise RewriteError listing every term left in a judge-visible field."""
    hits: list[str] = []
    for section, fields in ALLOWED_FIELDS.items():
        for record in extraction[section]:
            for field in fields:
                value = record.get(field)
                text = "\n".join(value) if isinstance(value, list) else value or ""
                hits.extend(
                    f"{_record_label(record)}.{field}: {term}"
                    for term in terms
                    if term.lower() in text.lower()
                )
    if hits:
        raise RewriteError("forbidden terms remain:\n" + "\n".join(hits))


def _update_case_yaml(blind_case: Path, source_case: Path, rewrites_file: Path) -> None:
    root = blind_case.parent.parent
    entries = {
        "source_csag": source_case / CSAG_RELATIVE,
        "rewrites": rewrites_file,
        "csag": blind_case / CSAG_RELATIVE,
        "scoring_schema": blind_case / SCHEMA_RELATIVE,
    }
    record: dict[str, object] = {"source_case": source_case.name}
    record.update(
        {
            name: {"path": os.path.relpath(path, root), "sha256": _sha256(path)}
            for name, path in entries.items()
        }
    )
    case_path = blind_case / "case.yaml"
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    updated = {}
    for key, value in case.items():
        if key in ("reused_ground_truth", "blinded_ground_truth"):
            updated["blinded_ground_truth"] = record
        else:
            updated[key] = DESCRIPTION if key == "description" else value
    case_path.write_text(
        yaml.safe_dump(updated, sort_keys=False, width=88), encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
