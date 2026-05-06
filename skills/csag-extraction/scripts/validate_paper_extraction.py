#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID[:\s]+(\d{6,9})\b", re.IGNORECASE)
DATASET_SIGNAL_RE = re.compile(
    r"\b(data availability|accession|project id|repository|zenodo|img/m|data portal|sra|geo|pride|available at|downloaded at)\b",
    re.IGNORECASE,
)
FIGURE_SIGNAL_RE = re.compile(
    r"^\s*(fig(?:ure)?\.?|table|supplementary figure|supplementary table)\b",
    re.IGNORECASE,
)

ASSERTION_CRITICALITIES = {"core", "major", "supporting", "background"}
DECISIVE_POLARITIES = {"supports", "refutes", "mixed"}
POLARITIES = DECISIVE_POLARITIES | {"inconclusive"}
STRENGTH_LEVELS = {"very_strong", "strong", "moderate", "weak", "very_weak", "unknown"}
ID_LIST_KEYS = (
    "artifacts",
    "datasets",
    "entities",
    "studies",
    "experiments",
    "assertions",
    "evidence_items",
    "evidence_links",
    "inferences",
    "assertion_relations",
    "critiques",
    "knowledge_gaps",
    "qa_items",
    "extraction_activities",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a CSAG PaperExtraction with repo-specific enforcement rules."
    )
    parser.add_argument("extraction_json", type=Path)
    parser.add_argument("--source-markdown", type=Path, default=None)
    parser.add_argument("--article-json", type=Path, default=None)
    parser.add_argument("--report-out", type=Path, required=True)
    parser.add_argument(
        "--profile",
        choices=("candidate", "ground_truth"),
        default="candidate",
        help="Validation strictness. Use ground_truth for benchmark answer artifacts.",
    )
    return parser.parse_args()


def load_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    return json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))


def front_matter_only(markdown: str) -> str:
    if not markdown:
        return ""
    for marker in ("\n# 1 ", "\n# Introduction", "\n## Introduction"):
        idx = markdown.find(marker)
        if idx != -1:
            return markdown[:idx]
    return markdown[:4000]


def collect_parameter_map(extraction: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for activity in extraction.get("extraction_activities", []):
        for item in activity.get("parameters", []):
            key = item.get("key")
            value = item.get("value")
            if isinstance(key, str) and isinstance(value, str):
                mapping[key] = value
    return mapping


def expect(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def nonempty_string_list(value: object) -> bool:
    return isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value)


def has_text_spans(item: dict | None) -> bool:
    return bool(isinstance(item, dict) and isinstance(item.get("text_spans"), list) and item.get("text_spans"))


def collect_ids(extraction: dict, errors: list[str]) -> dict[str, set[str]]:
    ids_by_key: dict[str, set[str]] = {}
    for key in ID_LIST_KEYS:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for item in extraction.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                continue
            if item_id in seen:
                duplicates.add(item_id)
            seen.add(item_id)
        if duplicates:
            errors.append(f"{key} contains duplicate ids: {', '.join(sorted(duplicates))}")
        ids_by_key[key] = seen
    return ids_by_key


def expect_required_ref(ref: object, known_ids: set[str], label: str, errors: list[str]) -> None:
    if not isinstance(ref, str) or not ref:
        errors.append(f"{label} is missing")
        return
    expect(ref in known_ids, f"{label} references missing id {ref}", errors)


def expect_optional_ref(ref: object, known_ids: set[str], message: str, errors: list[str]) -> None:
    if isinstance(ref, str) and ref:
        expect(ref in known_ids, message, errors)


def expect_refs(refs: object, known_ids: set[str], message_prefix: str, errors: list[str]) -> None:
    if not isinstance(refs, list):
        return
    for ref in refs:
        if isinstance(ref, str) and ref:
            expect(ref in known_ids, f"{message_prefix} references missing id {ref}", errors)


def validate_optional_assertion_metadata(extraction: dict, errors: list[str]) -> None:
    for item in extraction.get("assertions", []) or []:
        if not isinstance(item, dict):
            continue
        criticality = item.get("criticality")
        if criticality is not None:
            expect(
                criticality in ASSERTION_CRITICALITIES,
                f"assertion {item.get('id')} has invalid criticality {criticality}",
                errors,
            )
        falsification_criteria = item.get("falsification_criteria")
        if falsification_criteria is not None:
            expect(
                nonempty_string_list(falsification_criteria),
                f"assertion {item.get('id')} has invalid falsification_criteria",
                errors,
            )


def validate_cross_references(extraction: dict, ids_by_key: dict[str, set[str]], errors: list[str]) -> None:
    assertion_ids = ids_by_key.get("assertions", set())
    evidence_ids = ids_by_key.get("evidence_items", set())
    evidence_link_ids = ids_by_key.get("evidence_links", set())

    for item in extraction.get("evidence_links", []) or []:
        expect_required_ref(
            item.get("evidence_item"),
            evidence_ids,
            f"evidence_link {item.get('id')} evidence_item",
            errors,
        )
        expect_required_ref(
            item.get("assertion"),
            assertion_ids,
            f"evidence_link {item.get('id')} assertion",
            errors,
        )

    for item in extraction.get("inferences", []) or []:
        expect_required_ref(
            item.get("output_assertion"),
            assertion_ids,
            f"inference {item.get('id')} output_assertion",
            errors,
        )
        expect_refs(
            item.get("input_assertions"),
            assertion_ids,
            f"inference {item.get('id')} input_assertions",
            errors,
        )
        expect_refs(
            item.get("input_evidence_links"),
            evidence_link_ids,
            f"inference {item.get('id')} input_evidence_links",
            errors,
        )

    for item in extraction.get("assertion_relations", []) or []:
        expect_required_ref(
            item.get("from_assertion"),
            assertion_ids,
            f"assertion_relation {item.get('id')} from_assertion",
            errors,
        )
        expect_required_ref(
            item.get("to_assertion"),
            assertion_ids,
            f"assertion_relation {item.get('id')} to_assertion",
            errors,
        )

    for item in extraction.get("critiques", []) or []:
        expect_refs(
            item.get("impacted_assertions"),
            assertion_ids,
            f"critique {item.get('id')} impacted_assertions",
            errors,
        )
        expect_refs(
            item.get("impacted_evidence_items"),
            evidence_ids,
            f"critique {item.get('id')} impacted_evidence_items",
            errors,
        )

    for item in extraction.get("knowledge_gaps", []) or []:
        expect_refs(
            item.get("related_assertions"),
            assertion_ids,
            f"knowledge_gap {item.get('id')} related_assertions",
            errors,
        )

    for item in extraction.get("qa_items", []) or []:
        expect_optional_ref(
            item.get("query_assertion"),
            assertion_ids,
            f"qa_item {item.get('id')} references missing query_assertion {item.get('query_assertion')}",
            errors,
        )
        for answer in item.get("answers", []) or []:
            expect_refs(
                answer.get("supporting_assertions"),
                assertion_ids,
                f"qa_item {item.get('id')} answer supporting_assertions",
                errors,
            )
            expect_refs(
                answer.get("supporting_evidence_links"),
                evidence_link_ids,
                f"qa_item {item.get('id')} answer supporting_evidence_links",
                errors,
            )


def validate_ground_truth_profile(extraction: dict, ids_by_key: dict[str, set[str]], errors: list[str]) -> None:
    evidence_by_id = {
        item.get("id"): item
        for item in extraction.get("evidence_items", []) or []
        if isinstance(item, dict) and item.get("id")
    }
    links_by_assertion: dict[str, list[dict]] = {}
    for link in extraction.get("evidence_links", []) or []:
        if not isinstance(link, dict):
            continue
        assertion_id = link.get("assertion")
        if isinstance(assertion_id, str):
            links_by_assertion.setdefault(assertion_id, []).append(link)
        expect(
            link.get("strength") in STRENGTH_LEVELS,
            f"evidence_link {link.get('id')} missing or invalid strength",
            errors,
        )
        expect(bool(link.get("rationale")), f"evidence_link {link.get('id')} missing rationale", errors)
        expect(link.get("polarity") in POLARITIES, f"evidence_link {link.get('id')} has invalid polarity", errors)

    for assertion in extraction.get("assertions", []) or []:
        if not isinstance(assertion, dict):
            continue
        assertion_id = assertion.get("id")
        criticality = assertion.get("criticality")
        links = links_by_assertion.get(assertion_id, [])
        linked_evidence = [evidence_by_id.get(link.get("evidence_item")) for link in links]

        expect(criticality in ASSERTION_CRITICALITIES, f"assertion {assertion_id} missing criticality", errors)
        expect(
            nonempty_string_list(assertion.get("falsification_criteria")),
            f"assertion {assertion_id} missing falsification_criteria",
            errors,
        )
        if criticality != "background":
            expect(bool(links), f"assertion {assertion_id} has no evidence_links", errors)
        if criticality in {"core", "major"}:
            expect(
                any(link.get("polarity") in DECISIVE_POLARITIES for link in links),
                f"assertion {assertion_id} has no decisive evidence_link",
                errors,
            )
            expect(
                has_text_spans(assertion) or any(has_text_spans(item) for item in linked_evidence),
                f"assertion {assertion_id} lacks assertion/evidence text_spans",
                errors,
            )


def main() -> int:
    args = parse_args()
    extraction_path = args.extraction_json.expanduser().resolve()
    extraction = load_json(extraction_path)
    article = load_json(args.article_json)
    source_markdown = (
        args.source_markdown.expanduser().resolve().read_text(encoding="utf-8")
        if args.source_markdown
        else ""
    )

    errors: list[str] = []
    warnings: list[str] = []

    expect(isinstance(extraction, dict), "paper extraction must be a JSON object", errors)
    if errors:
        return write_report(args.report_out, extraction_path, False, errors, warnings, {}, args.profile)

    expect(bool(extraction.get("id")), "missing top-level id", errors)
    expect(bool(extraction.get("title")), "missing top-level title", errors)
    expect(isinstance(extraction.get("assertions"), list), "missing assertions list", errors)
    expect(isinstance(extraction.get("evidence_items"), list), "missing evidence_items list", errors)
    expect(isinstance(extraction.get("evidence_links"), list), "missing evidence_links list", errors)
    expect(isinstance(extraction.get("extraction_activities"), list) and extraction.get("extraction_activities"), "missing extraction_activities", errors)

    ids_by_key = collect_ids(extraction, errors)

    for item in extraction.get("assertions", []):
        expect(bool(item.get("id")), "assertion without id", errors)
        expect(bool(item.get("assertion_text")), f"assertion {item.get('id')} missing assertion_text", errors)
        expect(bool(item.get("claim_role")), f"assertion {item.get('id')} missing claim_role", errors)
        expect(bool(item.get("normalization_status")), f"assertion {item.get('id')} missing normalization_status", errors)
        expect(isinstance(item.get("contexts"), list) and len(item.get("contexts")) >= 1, f"assertion {item.get('id')} missing contexts", errors)

    for item in extraction.get("evidence_links", []):
        expect(bool(item.get("polarity")), f"evidence_link {item.get('id')} missing polarity", errors)

    validate_optional_assertion_metadata(extraction, errors)
    validate_cross_references(extraction, ids_by_key, errors)
    if args.profile == "ground_truth":
        validate_ground_truth_profile(extraction, ids_by_key, errors)

    param_map = collect_parameter_map(extraction)
    source_text = front_matter_only(source_markdown)
    if article is not None:
        source_text += "\n" + article.get("title", "")
        source_text += "\n" + article.get("authors", "")
        source_text += "\n" + article.get("affiliations", "")
        source_text += "\n" + article.get("abstract", "")

    source_doi = DOI_RE.search(source_text)
    source_pmid = PMID_RE.search(source_text)
    doi = extraction.get("doi", "")
    pmid = extraction.get("pmid", "")
    doi_status = param_map.get("doi_status")
    pmid_status = param_map.get("pmid_status")

    if source_doi:
        expect(bool(doi), "DOI appears recoverable from the source but extraction.doi is empty", errors)
    else:
        expect(doi_status in {"resolved", "unresolved"}, "missing explicit doi_status parameter in extraction_activities", errors)
    if doi:
        expect(doi_status in {None, "resolved"}, "extraction.doi is populated but doi_status is not resolved", errors)

    if source_pmid:
        expect(bool(pmid), "PMID appears recoverable from the source but extraction.pmid is empty", errors)
    else:
        expect(pmid_status in {"resolved", "unresolved"}, "missing explicit pmid_status parameter in extraction_activities", errors)
    if pmid:
        expect(pmid_status in {None, "resolved"}, "extraction.pmid is populated but pmid_status is not resolved", errors)

    figure_legends = article.get("figure_legends", []) if isinstance(article, dict) else []
    figure_signals = bool(figure_legends)
    if not figure_signals and source_markdown:
        figure_signals = any(FIGURE_SIGNAL_RE.match(line) for line in source_markdown.splitlines())
    if figure_signals:
        artifacts = extraction.get("artifacts", [])
        expect(isinstance(artifacts, list) and len(artifacts) > 0, "figure/table captions are present in the source but extraction.artifacts is empty", errors)
        for artifact in artifacts:
            expect(bool(artifact.get("id")), "artifact without id", errors)
            expect(bool(artifact.get("artifact_type")), f"artifact {artifact.get('id')} missing artifact_type", errors)
            expect(bool(artifact.get("artifact_label")) or bool(artifact.get("caption")), f"artifact {artifact.get('id')} missing artifact_label/caption", errors)

    dataset_signals = bool(source_markdown and DATASET_SIGNAL_RE.search(source_markdown))
    if dataset_signals:
        datasets = extraction.get("datasets", [])
        expect(isinstance(datasets, list) and len(datasets) > 0, "dataset/data-availability signals are present in the source but extraction.datasets is empty", errors)
        for dataset in datasets:
            expect(bool(dataset.get("id")), "dataset without id", errors)
            expect(
                bool(dataset.get("accession")) or bool(dataset.get("repository")) or bool(dataset.get("dataset_url")),
                f"dataset {dataset.get('id')} missing accession/repository/dataset_url",
                errors,
            )

    report = {
        "ok": not errors,
        "profile": args.profile,
        "extraction_json": str(extraction_path),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "assertions": len(extraction.get("assertions", [])),
            "assertions_with_criticality": sum(
                1 for item in extraction.get("assertions", []) if item.get("criticality")
            ),
            "assertions_with_falsification_criteria": sum(
                1 for item in extraction.get("assertions", []) if nonempty_string_list(item.get("falsification_criteria"))
            ),
            "evidence_items": len(extraction.get("evidence_items", [])),
            "evidence_links": len(extraction.get("evidence_links", [])),
            "artifacts": len(extraction.get("artifacts", [])),
            "datasets": len(extraction.get("datasets", [])),
        },
    }
    args.report_out.expanduser().resolve().write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK")
    return 0


def write_report(
    report_path: Path,
    extraction_path: Path,
    ok: bool,
    errors: list[str],
    warnings: list[str],
    metrics: dict,
    profile: str = "candidate",
) -> int:
    report = {
        "ok": ok,
        "profile": profile,
        "extraction_json": str(extraction_path),
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }
    report_path.expanduser().resolve().write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
