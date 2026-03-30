#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_TOP_LEVEL_KEYS = {
    "paper_id",
    "title",
    "entities",
    "hypotheses",
    "evidence_items",
    "evidence_links",
    "discoveries",
    "gaps",
    "conclusions",
    "confidence",
}

ENTITY_TYPES = {
    "taxon",
    "gene",
    "protein",
    "pathway",
    "environment",
    "host",
    "phenotype",
    "assay",
    "method",
    "chemical",
    "dataset",
    "virus",
    "contig",
    "mag",
    "plasmid",
    "phage",
    "sampling_location",
    "concept",
    "process",
    "other",
}

EVIDENCE_TYPES = {
    "expression",
    "phylogeny",
    "homology",
    "comparative_genomics",
    "metagenomics",
    "ecological_observation",
    "biochemical_assay",
    "infection_assay",
    "imaging",
    "computational_prediction",
    "structure_prediction",
    "literature_prior",
    "other",
}

ID_PATTERNS = {
    "entities": ("entity_id", re.compile(r"^ent\d{2,3}$")),
    "hypotheses": ("hypothesis_id", re.compile(r"^hyp\d{2,3}$")),
    "evidence_items": ("evidence_id", re.compile(r"^ev\d{2,3}$")),
    "evidence_links": ("link_id", re.compile(r"^link\d{2,3}$")),
    "discoveries": ("discovery_id", re.compile(r"^disc\d{2,3}$")),
    "gaps": ("gap_id", re.compile(r"^gap\d{2,3}$")),
    "conclusions": ("conclusion_id", re.compile(r"^conc\d{2,3}$")),
}

PROVENANCE_COLLECTIONS = [
    "entities",
    "hypotheses",
    "evidence_items",
    "evidence_links",
    "discoveries",
    "gaps",
    "conclusions",
]

COUNT_LIMITS = {
    "hypotheses": (1, 4),
    "evidence_items": (2, 10),
    "discoveries": (1, 4),
    "gaps": (1, 4),
    "conclusions": (1, 3),
}


def load_json(path: Path):
    with path.open() as handle:
        return json.load(handle)


def sections_from_raw(raw_paper: dict):
    paper = raw_paper.get("paper", {})
    return {
        "abstract": paper.get("abstract") or paper.get("abstract_text") or "",
        "full_text": paper.get("full_text") or "",
    }


def validate_provenance(item_id, collection, provenance, sections, errors):
    if not provenance:
        errors.append(f"{collection}:{item_id}: missing provenance")
        return
    if not isinstance(provenance, list):
        errors.append(f"{collection}:{item_id}: provenance must be a list")
        return
    for idx, prov in enumerate(provenance, start=1):
        if not isinstance(prov, dict):
            errors.append(f"{collection}:{item_id}: provenance[{idx}] must be an object")
            continue
        section = prov.get("section")
        quote = prov.get("quote")
        if section not in {"abstract", "full_text"}:
            errors.append(f"{collection}:{item_id}: provenance[{idx}] invalid section {section!r}")
        if not isinstance(quote, str) or not quote.strip():
            errors.append(f"{collection}:{item_id}: provenance[{idx}] missing quote")
            continue
        if "..." in quote or "…" in quote:
            errors.append(f"{collection}:{item_id}: provenance[{idx}] quote must not contain ellipses")
        if section in sections and quote not in sections[section]:
            errors.append(f"{collection}:{item_id}: provenance[{idx}] quote not found exactly in {section}")


def validate_artifact(artifact_path: Path, raw_paper_path: Path):
    artifact = load_json(artifact_path)
    raw_paper = load_json(raw_paper_path)
    sections = sections_from_raw(raw_paper)
    errors = []

    missing_keys = sorted(REQUIRED_TOP_LEVEL_KEYS - set(artifact))
    if missing_keys:
        errors.append(f"missing top-level keys: {', '.join(missing_keys)}")

    for collection, (id_field, pattern) in ID_PATTERNS.items():
        for item in artifact.get(collection, []):
            item_id = item.get(id_field)
            if not isinstance(item_id, str) or not pattern.match(item_id):
                errors.append(f"{collection}:{item_id}: invalid {id_field}")

    for item in artifact.get("entities", []):
        if item.get("type") not in ENTITY_TYPES:
            errors.append(f"entities:{item.get('entity_id')}: invalid type {item.get('type')!r}")

    for item in artifact.get("evidence_items", []):
        if item.get("type") not in EVIDENCE_TYPES:
            errors.append(f"evidence_items:{item.get('evidence_id')}: invalid type {item.get('type')!r}")

    for collection in PROVENANCE_COLLECTIONS:
        id_field = ID_PATTERNS[collection][0]
        for item in artifact.get(collection, []):
            validate_provenance(item.get(id_field), collection, item.get("provenance"), sections, errors)

    ids = set()
    for collection, (id_field, _) in ID_PATTERNS.items():
        ids.update(item.get(id_field) for item in artifact.get(collection, []))
    evidence_ids = {item.get("evidence_id") for item in artifact.get("evidence_items", [])}

    for link in artifact.get("evidence_links", []):
        if link.get("source_evidence_id") not in evidence_ids:
            errors.append(f"evidence_links:{link.get('link_id')}: missing source evidence {link.get('source_evidence_id')}")
        if link.get("target_id") not in ids:
            errors.append(f"evidence_links:{link.get('link_id')}: missing target {link.get('target_id')}")

    for discovery in artifact.get("discoveries", []):
        for evidence_id in discovery.get("supporting_evidence_ids", []):
            if evidence_id not in evidence_ids:
                errors.append(
                    f"discoveries:{discovery.get('discovery_id')}: missing supporting evidence {evidence_id}"
                )

    for conclusion in artifact.get("conclusions", []):
        for ref in conclusion.get("supported_by", []):
            if ref not in ids and ref not in evidence_ids:
                errors.append(f"conclusions:{conclusion.get('conclusion_id')}: invalid supported_by {ref}")
        for ref in conclusion.get("limited_by", []):
            if ref not in ids and ref not in evidence_ids:
                errors.append(f"conclusions:{conclusion.get('conclusion_id')}: invalid limited_by {ref}")

    for collection, (minimum, maximum) in COUNT_LIMITS.items():
        count = len(artifact.get(collection, []))
        if count < minimum or count > maximum:
            errors.append(f"{collection}: count {count} outside expected range [{minimum}, {maximum}]")

    confidence = artifact.get("confidence", {})
    if not isinstance(confidence, dict):
        errors.append("confidence must be an object")
    else:
        doc_conf = confidence.get("document_confidence")
        if not isinstance(doc_conf, (int, float)) or not (0 <= doc_conf <= 1):
            errors.append("confidence.document_confidence must be a float between 0 and 1")

    return {
        "artifact": str(artifact_path),
        "raw_paper": str(raw_paper_path),
        "valid": not errors,
        "errors": errors,
    }


def infer_raw_paper_path(run_dir: Path, artifact_path: Path):
    selected_papers_path = run_dir / "raw" / "selected_papers.json"
    if selected_papers_path.exists():
        try:
            selected = load_json(selected_papers_path)
            for row in selected:
                if Path(row.get("artifact_path", "")).name == artifact_path.name:
                    return Path(row["raw_path"])
        except Exception:
            pass
    match = re.search(r"pmid(\d+)_csag_light\.json$", artifact_path.name)
    if not match:
        match = re.search(r"pmc(\d+)_csag_light\.json$", artifact_path.name)
    if not match:
        match = re.search(r"(\d+)_csag_light\.json$", artifact_path.name)
    if not match:
        raise ValueError(f"cannot infer raw paper path from artifact path {artifact_path}")
    pmid = match.group(1)
    candidates = [
        run_dir / "raw" / "papers" / f"pmid{pmid}.json",
        run_dir / "raw" / "papers" / f"PMC{pmid}.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise ValueError(f"cannot locate raw paper for artifact path {artifact_path}")


def validate_run(run_dir: Path):
    artifacts_dir = run_dir / "artifacts"
    results = []
    for artifact_path in sorted(artifacts_dir.glob("*.json")):
        raw_paper_path = infer_raw_paper_path(run_dir, artifact_path)
        results.append(validate_artifact(artifact_path, raw_paper_path))
    return {
        "run_dir": str(run_dir),
        "artifact_count": len(results),
        "valid_count": sum(1 for r in results if r["valid"]),
        "invalid_count": sum(1 for r in results if not r["valid"]),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--raw-paper", type=Path)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()

    if args.run_dir:
        report = validate_run(args.run_dir)
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["invalid_count"] == 0 else 1)

    if not args.artifact or not args.raw_paper:
        parser.error("provide either --run-dir or both --artifact and --raw-paper")

    report = validate_artifact(args.artifact, args.raw_paper)
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
