#!/usr/bin/env python3
import argparse
import json
import re
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path

from validate_csag_light import infer_raw_paper_path, validate_artifact


ENTITY_TYPE_MAP = {
    "mag": "mag",
    "metabolite": "chemical",
    "mobile_genetic_element": "other",
    "nutrient": "chemical",
    "protein_family": "protein",
    "subcellular_structure": "concept",
    "trait": "concept",
    "virus_clade": "virus",
    "virus_lineage": "virus",
    "evolutionary_process": "process",
    "host_process": "process",
}

EVIDENCE_TYPE_MAP = {
    "author_stated_limitation": "other",
    "comparative_evolutionary_analysis": "comparative_genomics",
    "cytoskeleton_perturbation": "infection_assay",
    "damage_tolerance_assay": "biochemical_assay",
    "experimental": "other",
    "genome_content_analysis": "comparative_genomics",
    "method_development": "other",
    "metagenomic_survey": "metagenomics",
    "mutational_analysis": "biochemical_assay",
    "perturbation_assay": "infection_assay",
    "phylogenomic_reconciliation": "phylogeny",
    "proteomics": "other",
    "sequence_analysis": "computational_prediction",
    "single_cell_metagenomics": "metagenomics",
    "transcriptomics": "expression",
}

ID_SPECS = {
    "entities": ("entity_id", "ent"),
    "hypotheses": ("hypothesis_id", "hyp"),
    "evidence_items": ("evidence_id", "ev"),
    "evidence_links": ("link_id", "link"),
    "discoveries": ("discovery_id", "disc"),
    "gaps": ("gap_id", "gap"),
    "conclusions": ("conclusion_id", "conc"),
}


def load_json(path: Path):
    with path.open() as handle:
        return json.load(handle)


def dump_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def normalize_text(value: str) -> str:
    value = value.lower().replace("…", " ").replace("...", " ")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def split_candidates(section_text: str):
    pieces = re.split(r"(?<=[.!?])\s+|\n+", section_text)
    return [piece.strip() for piece in pieces if piece.strip()]


def repair_quote(section_text: str, quote: str):
    quote = (quote or "").strip()
    if not quote:
        return quote
    if "..." in quote or "…" in quote:
        quote = quote.replace("...", " ").replace("…", " ")
    if quote in section_text:
        return quote
    norm_quote = normalize_text(quote)
    if not norm_quote:
        return quote
    best = None
    best_score = 0.0
    for candidate in split_candidates(section_text):
        candidate_norm = normalize_text(candidate)
        if not candidate_norm:
            continue
        score = SequenceMatcher(None, norm_quote, candidate_norm).ratio()
        if norm_quote in candidate_norm:
            score += 0.25
        if score > best_score:
            best = candidate
            best_score = score
    if best and best_score >= 0.5:
        return best
    return quote


def infer_provenance(search_text: str, sections):
    search_text = (search_text or "").strip()
    if not search_text:
        return []
    norm_search = normalize_text(search_text)
    best = None
    best_score = 0.0
    for section_name in ("abstract", "full_text"):
        for candidate in split_candidates(sections.get(section_name, "")):
            candidate_norm = normalize_text(candidate)
            if not candidate_norm:
                continue
            score = SequenceMatcher(None, norm_search, candidate_norm).ratio()
            if norm_search in candidate_norm:
                score += 0.3
            if score > best_score:
                best = {"section": section_name, "quote": candidate}
                best_score = score
    if best and best_score >= 0.3:
        return [best]
    return []


def normalize_provenance(item, sections):
    provenance = item.get("provenance") or []
    normalized = []
    for prov in provenance:
        if not isinstance(prov, dict):
            continue
        section = prov.get("section")
        if section not in {"abstract", "full_text"}:
            section = "full_text"
        quote = repair_quote(sections.get(section, ""), prov.get("quote", ""))
        if quote and quote in sections.get(section, ""):
            normalized.append({"section": section, "quote": quote})
    item["provenance"] = normalized


def ensure_provenance(item, sections, search_fields):
    normalize_provenance(item, sections)
    if item.get("provenance"):
        return
    for field in search_fields:
        provenance = infer_provenance(item.get(field, ""), sections)
        if provenance:
            item["provenance"] = provenance
            return


def normalize_type(value, mapping):
    value = (value or "other").strip()
    lowered = value.lower()
    if lowered in mapping:
        return mapping[lowered]
    return lowered


def rebuild_ids(artifact):
    id_maps = {}
    for collection, (id_field, prefix) in ID_SPECS.items():
        id_map = {}
        for index, item in enumerate(artifact.get(collection, []), start=1):
            old_id = item.get(id_field)
            new_id = f"{prefix}{index:02d}"
            item[id_field] = new_id
            if old_id:
                id_map[old_id] = new_id
        id_maps[collection] = id_map

    entity_map = id_maps["entities"]
    evidence_map = id_maps["evidence_items"]
    combined = {}
    for collection in ID_SPECS:
        combined.update(id_maps[collection])

    for item in artifact.get("hypotheses", []):
        item["entities"] = [entity_map.get(value, value) for value in item.get("entities", [])]

    for item in artifact.get("evidence_items", []):
        item["entities"] = [entity_map.get(value, value) for value in item.get("entities", [])]

    for item in artifact.get("discoveries", []):
        item["entities"] = [entity_map.get(value, value) for value in item.get("entities", [])]
        item["supporting_evidence_ids"] = [evidence_map.get(value, value) for value in item.get("supporting_evidence_ids", [])]

    for item in artifact.get("gaps", []):
        item["related_ids"] = [combined.get(value, evidence_map.get(value, value)) for value in item.get("related_ids", [])]

    for item in artifact.get("conclusions", []):
        item["supported_by"] = [combined.get(value, evidence_map.get(value, value)) for value in item.get("supported_by", [])]
        item["limited_by"] = [combined.get(value, evidence_map.get(value, value)) for value in item.get("limited_by", [])]

    for item in artifact.get("evidence_links", []):
        item["source_evidence_id"] = evidence_map.get(item.get("source_evidence_id"), item.get("source_evidence_id"))
        item["target_id"] = combined.get(item.get("target_id"), item.get("target_id"))

    return evidence_map


def repair_artifact(artifact, raw_paper):
    artifact = deepcopy(artifact)
    paper = raw_paper.get("paper", {})
    sections = {
        "abstract": paper.get("abstract") or paper.get("abstract_text") or "",
        "full_text": paper.get("full_text") or "",
    }

    doi = (paper.get("doi") or "").strip()
    pmid = (paper.get("pmid") or "").strip()
    pmc_id = (paper.get("pmc_id") or "").strip()
    if doi:
        artifact["paper_id"] = f"doi:{doi}"
    elif pmid:
        artifact["paper_id"] = f"pmid:{pmid}"
    elif pmc_id:
        artifact["paper_id"] = f"pmc:{pmc_id}"

    artifact["title"] = paper.get("title") or artifact.get("title")

    for item in artifact.get("entities", []):
        item["type"] = normalize_type(item.get("type"), ENTITY_TYPE_MAP)
        ensure_provenance(item, sections, ["name", "canonical_form"])

    for item in artifact.get("hypotheses", []):
        ensure_provenance(item, sections, ["text"])

    for item in artifact.get("evidence_items", []):
        item["type"] = normalize_type(item.get("type"), EVIDENCE_TYPE_MAP)
        ensure_provenance(item, sections, ["text"])

    for item in artifact.get("discoveries", []):
        ensure_provenance(item, sections, ["text"])

    for item in artifact.get("gaps", []):
        item["type"] = normalize_type(item.get("type"), {"critique": "critique"})
        ensure_provenance(item, sections, ["text"])

    for item in artifact.get("conclusions", []):
        ensure_provenance(item, sections, ["text"])

    evidence_map = rebuild_ids(artifact)

    evidence_lookup = {item["evidence_id"]: item for item in artifact.get("evidence_items", [])}
    for item in artifact.get("evidence_links", []):
        normalize_provenance(item, sections)
        if not item.get("provenance"):
            source = evidence_lookup.get(item.get("source_evidence_id"))
            if source and source.get("provenance"):
                item["provenance"] = deepcopy(source["provenance"][:1])

    confidence = artifact.get("confidence")
    if not isinstance(confidence, dict):
        artifact["confidence"] = {"document_confidence": 0.5, "rationale": "repaired artifact", "flags": ["repaired"]}
    else:
        confidence.setdefault("document_confidence", 0.5)
        confidence.setdefault("rationale", "repaired artifact")
        confidence.setdefault("flags", [])

    return artifact


def repair_one(artifact_path: Path, raw_paper_path: Path, output_path: Path, passes: int):
    artifact = load_json(artifact_path)
    raw_paper = load_json(raw_paper_path)
    for _ in range(passes):
        artifact = repair_artifact(artifact, raw_paper)
        dump_json(output_path, artifact)
        report = validate_artifact(output_path, raw_paper_path)
        if report["valid"]:
            return report
    dump_json(output_path, artifact)
    return validate_artifact(output_path, raw_paper_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--raw-paper", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--passes", type=int, default=2)
    args = parser.parse_args()

    if args.run_dir:
        if not args.output_dir:
            parser.error("--run-dir requires --output-dir")
        results = []
        for artifact_path in sorted((args.run_dir / "artifacts").glob("*.json")):
            raw_paper_path = infer_raw_paper_path(args.run_dir, artifact_path)
            output_path = args.output_dir / artifact_path.name
            results.append(repair_one(artifact_path, raw_paper_path, output_path, args.passes))
        summary = {
            "run_dir": str(args.run_dir),
            "output_dir": str(args.output_dir),
            "artifact_count": len(results),
            "valid_count": sum(1 for result in results if result["valid"]),
            "invalid_count": sum(1 for result in results if not result["valid"]),
            "results": results,
        }
        print(json.dumps(summary, indent=2))
        return

    if not (args.artifact and args.raw_paper and args.output):
        parser.error("provide either --run-dir/--output-dir or --artifact/--raw-paper/--output")

    report = repair_one(args.artifact, args.raw_paper, args.output, args.passes)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
