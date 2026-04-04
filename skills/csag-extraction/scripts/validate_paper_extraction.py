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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a CSAG PaperExtraction with repo-specific enforcement rules."
    )
    parser.add_argument("extraction_json", type=Path)
    parser.add_argument("--source-markdown", type=Path, default=None)
    parser.add_argument("--article-json", type=Path, default=None)
    parser.add_argument("--report-out", type=Path, required=True)
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
        return write_report(args.report_out, extraction_path, False, errors, warnings, {})

    expect(bool(extraction.get("id")), "missing top-level id", errors)
    expect(bool(extraction.get("title")), "missing top-level title", errors)
    expect(isinstance(extraction.get("assertions"), list), "missing assertions list", errors)
    expect(isinstance(extraction.get("evidence_items"), list), "missing evidence_items list", errors)
    expect(isinstance(extraction.get("evidence_links"), list), "missing evidence_links list", errors)
    expect(isinstance(extraction.get("extraction_activities"), list) and extraction.get("extraction_activities"), "missing extraction_activities", errors)

    assertion_ids = {item.get("id") for item in extraction.get("assertions", []) if item.get("id")}
    evidence_ids = {item.get("id") for item in extraction.get("evidence_items", []) if item.get("id")}

    for item in extraction.get("assertions", []):
        expect(bool(item.get("id")), "assertion without id", errors)
        expect(bool(item.get("assertion_text")), f"assertion {item.get('id')} missing assertion_text", errors)
        expect(bool(item.get("claim_role")), f"assertion {item.get('id')} missing claim_role", errors)
        expect(bool(item.get("normalization_status")), f"assertion {item.get('id')} missing normalization_status", errors)
        expect(isinstance(item.get("contexts"), list) and len(item.get("contexts")) >= 1, f"assertion {item.get('id')} missing contexts", errors)

    for item in extraction.get("evidence_links", []):
        expect(item.get("evidence_item") in evidence_ids, f"evidence_link {item.get('id')} references missing evidence_item {item.get('evidence_item')}", errors)
        expect(item.get("assertion") in assertion_ids, f"evidence_link {item.get('id')} references missing assertion {item.get('assertion')}", errors)
        expect(bool(item.get("polarity")), f"evidence_link {item.get('id')} missing polarity", errors)

    for item in extraction.get("inferences", []):
        expect(item.get("output_assertion") in assertion_ids, f"inference {item.get('id')} references missing output_assertion {item.get('output_assertion')}", errors)

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
        "extraction_json": str(extraction_path),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "assertions": len(extraction.get("assertions", [])),
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


def write_report(report_path: Path, extraction_path: Path, ok: bool, errors: list[str], warnings: list[str], metrics: dict) -> int:
    report = {
        "ok": ok,
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
