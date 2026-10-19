#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DIMENSION_WEIGHTS = {
    "claim_coverage": 35.0,
    "evidence_and_method_alignment": 25.0,
    "entities_and_datasets": 15.0,
    "quantitative_result_fidelity": 15.0,
    "limitations_and_uncertainty": 10.0,
}

TEXT_KEYS = {
    "abstract",
    "accession",
    "answer_text",
    "assertion_text",
    "caption",
    "dataset_url",
    "description",
    "evidence_text",
    "keywords",
    "label",
    "method_summary",
    "notes",
    "rationale",
    "repository",
    "title",
}
CONTENT_TOP_LEVEL_KEYS = {
    "abstract",
    "artifacts",
    "assertions",
    "critiques",
    "datasets",
    "entities",
    "evidence_items",
    "evidence_links",
    "inferences",
    "keywords",
    "title",
}
SKIP_KEYS = {
    "artifact_ref",
    "assertion_id",
    "assertion_ref",
    "created_by",
    "created_on",
    "critique_id",
    "dataset_id",
    "document_id",
    "entity_id",
    "evidence_ref",
    "generated_by",
    "id",
    "run_datetime",
    "run_id",
}


@dataclass(frozen=True)
class Criterion:
    label: str
    weight: float
    any: tuple[str, ...] = ()
    all: tuple[str, ...] = ()


CLAIM_RULES: dict[str, tuple[Criterion, ...]] = {
    "A0001": (
        Criterion("three input assemblies", 0.25, any=("three img/m", "three assemblies", "three bins", "im gs3300050451", "imgs3300050451")),
        Criterion("genomic characterization", 0.25, any=("genomic characterization", "gene-content", "assembly statistics", "gene calling", "characterised", "characterized")),
        Criterion("giant-virus identity", 0.35, any=("giant virus", "ncldv", "nucleocytoviricota", "imitervirales")),
        Criterion("saprobic fungal association", 0.15, any=("saprobic", "rhizophydium", "chytridiomycota", "fung")),
    ),
    "A0002": (
        Criterion("near-five-megabase size", 0.35, any=("4.22", "4.2", "4.63", "4.6", "near 5", "five megabase", "5 mb", "multi-megabase")),
        Criterion("giant-virus context", 0.35, any=("giant virus", "ncldv", "nucleocytoviricota", "large dna virus")),
        Criterion("record-size framing", 0.30, any=("record", "largest", "exceed", "exceeds", "surpass", "frontier", "previously reported", "unprecedented")),
    ),
    "A0003": (
        Criterion("single species or near-clonal lineage", 0.30, any=("single species", "same species", "near-clonal", "near-identical", "species-level", "99.7", "99.8", "aai", "ani")),
        Criterion("Klosneuvirinae or IM_16 placement", 0.35, any=("klosneuvirinae", "im_16", "imitervirales", "megaviricetes")),
        Criterion("Yasminevirus or Dasosvirus relationship", 0.25, any=("yasminevirus", "dasosvirus", "yasm", "g234", "catovirus")),
    ),
    "A0004": (
        Criterion("negative rRNA/cellular marker evidence", 0.25, any=("no rrna", "absence of detectable rrna", "zero universal ribosomal", "bacterial marker", "archaeal marker")),
        Criterion("viral hallmark or marker evidence", 0.30, any=("ncldv core", "gvog", "gvclass", "capsid", "dna polymerase", "rna polymerase", "packaging atpase", "hallmark")),
        Criterion("quality, contamination, or completeness check", 0.25, any=("contamination", "completeness", "quality", "mixed bin", "read coverage", "binning", "checkv")),
        Criterion("consistent composition across bins", 0.20, any=("uniform", "consistent", "nearly identical", "low gc", "coding density", "same genome")),
    ),
    "A0005": (
        Criterion("conserved gene pair spacing", 0.50, any=("conserved gene pair", "intervening genes", "gene distance", "synteny")),
        Criterion("Yasminevirus comparison", 0.25, any=("yasminevirus", "yasm")),
        Criterion("expanded genomic regions", 0.25, any=("expanded genomic", "gene-space expansion", "expanded regions", "genome expansion")),
    ),
    "A0006": (
        Criterion("RP-S30e/eS30", 0.60, any=("rp-s30", "s30e", "es30")),
        Criterion("ribosome-related proteins", 0.25, any=("ribosomal protein", "ribosome", "rpl40", "rp-s27", "rimi")),
        Criterion("novel in giant viruses", 0.15, any=("not previously", "unreported", "novel", "first reported")),
    ),
    "A0007": (
        Criterion("aminoacyl-tRNA synthetases", 0.30, any=("aminoacyl-trna", "aars", "synthetase")),
        Criterion("tRNA repertoire", 0.25, any=("trna", "trnas")),
        Criterion("translation factors", 0.25, any=("translation factor", "if-2", "ef-tu", "ef-g", "eif")),
        Criterion("ribosome-related genes", 0.20, any=("ribosomal protein", "ribosome", "rimi", "rpl40", "rp-s27")),
    ),
    "A0008": (
        Criterion("duplication or repeats", 0.35, any=("segmental duplication", "duplication", "tandem repeat", "repeat-rich")),
        Criterion("paralogization or high copy number", 0.35, any=("paralog", "high-copy", "multi-copy", "multicopy", "copy number")),
        Criterion("genome gigantism or expansion", 0.30, any=("genome gigantism", "genome expansion", "large genome", "4.2", "4.6", "5 mb")),
    ),
    "A0009": (
        Criterion("PUM-HD/Pumilio-like fold", 0.45, any=("pum", "pumilio")),
        Criterion("structure or docking model", 0.25, any=("fold", "predicted structure", "structural prediction", "docking", "structural model", "alphafold")),
        Criterion("nucleic-acid interaction", 0.30, any=("nucleic acid", "nucleic-acid", "dna binding", "rna binding")),
    ),
    "A0010": (
        Criterion("histone/chromatin compaction", 0.25, any=("histone", "chromatin", "chromosome compaction")),
        Criterion("SMC proteins", 0.20, any=("smc", "condensin")),
        Criterion("topoisomerases", 0.20, any=("topoisomerase", "topoisomerases")),
        Criterion("packaging machinery", 0.20, any=("packaging atpase", "packaging machinery", "virion", "capsid")),
        Criterion("chromatin modifiers", 0.15, any=("methyltransferase", "h3k4", "chromatin modifier")),
    ),
    "A0011": (
        Criterion("viral genome-size frontier", 0.35, any=("record", "largest", "exceed", "surpass", "frontier", "unusually large", "exceptional giant-virus genome")),
        Criterion("expanded functional capacity", 0.30, any=("functional capacity", "translation apparatus", "translation machinery", "gene-rich", "substantial genetic autonomy")),
        Criterion("virus-cell boundary framing", 0.35, any=("challenge", "boundaries", "cellular organisms", "fourth domain", "viral biology")),
    ),
    "A0012": (
        Criterion("metagenome assembly or bin status", 0.25, any=("metagenome", "metagenomic", "mag", "bin", "assembly")),
        Criterion("incomplete, fragmented, or not closed", 0.35, any=("fragmented", "incomplete", "completeness", "not closed", "n50", "contig")),
        Criterion("near-five-megabase inference", 0.20, any=("4.22", "4.2", "4.63", "4.6", "near 5", "5 mb")),
        Criterion("uncertainty/provisional framing", 0.20, any=("uncertain", "uncertainty", "provisional", "cannot be resolved", "inferred")),
    ),
}

METHOD_RULES = (
    Criterion("assembly statistics and gene calling", 0.12, any=("assembly statistics", "seqkit", "prodigal", "pyrodigal", "coding density", "cds")),
    Criterion("rRNA/tRNA screening", 0.10, any=("barrnap", "trnascan", "rrna", "trna")),
    Criterion("protein similarity or clustering", 0.12, any=("mmseqs", "diamond", "rbh", "aai", "ani", "protein cluster", "jaccard", "k-mer")),
    Criterion("viral marker phylogeny or reference placement", 0.18, any=("ncldv", "gvog", "gvclass", "phylogenetic", "phylogeny", "yasminevirus", "klosneuvirinae", "capsid", "dna polymerase")),
    Criterion("quality/completeness/binning controls", 0.14, any=("contamination", "completeness", "mixed bin", "binning", "read coverage", "quality", "no rrna")),
    Criterion("translation-repertoire annotation", 0.14, any=("aminoacyl-trna", "aars", "translation factor", "trna", "ribosomal protein")),
    Criterion("synteny or Yasminevirus gene-order comparison", 0.15, any=("conserved gene pair", "intervening genes", "gene distance", "synteny", "yasminevirus")),
    Criterion("structure, packaging, or compaction analyses", 0.15, any=("pum", "pumilio", "docking", "structural prediction", "histone", "smc", "topoisomerase", "packaging atpase", "chromatin")),
)

ENTITY_RULES = (
    Criterion("three visible IMG/M assemblies", 0.15, any=("imgs3300050451", "three img/m", "three assemblies", "three bins")),
    Criterion("giant-virus or Nucleocytoviricota identity", 0.15, any=("giant virus", "ncldv", "nucleocytoviricota", "large dna virus")),
    Criterion("Imitervirales/Klosneuvirinae placement", 0.15, any=("imitervirales", "klosneuvirinae", "im_16", "megaviricetes")),
    Criterion("Yasminevirus/Dasosvirus relationship", 0.10, any=("yasminevirus", "dasosvirus", "yasm", "catovirus")),
    Criterion("fungal host context", 0.08, any=("rhizophydium", "chytridiomycota", "saprobic", "fung")),
    Criterion("translation-system entities", 0.10, any=("aminoacyl-trna", "aars", "trna", "translation factor", "ribosomal protein")),
    Criterion("duplication/paralog/PUM entities", 0.07, any=("paralog", "pum", "pumilio", "tandem repeat", "segmental duplication")),
    Criterion("compaction/packaging entities", 0.05, any=("histone", "smc", "topoisomerase", "packaging atpase", "chromatin")),
)

DATASET_RULES = (
    Criterion("IMG/M assembly identifiers", 0.50, any=("3300050451", "3300050569", "3300050570", "imgs3300050451")),
    Criterion("JGI project identifiers", 0.25, any=("1342175", "1342176", "1342177", "ga0529969", "ga0529970", "ga0529971")),
    Criterion("supplementary archive or JGI portal", 0.25, any=("supplementary", "jgi data portal", "portal.nersc.gov", "img.jgi.doe.gov")),
)

QUANT_RULES = (
    Criterion("assembly size around 4.2-4.6 Mb", 0.25, any=("4.22", "4.2", "4.34", "4.63", "4.6", "near 5")),
    Criterion("GC content around 29.7-29.8%", 0.15, any=("29.7", "29.8")),
    Criterion("contig count or N50", 0.15, any=("325", "326", "336", "n50", "contigs")),
    Criterion("gene/protein count around 3.1-3.5k", 0.10, any=("3,083", "3083", "3,113", "3113", "3,390", "3390", "3,459", "3459", "3.1", "3.5")),
    Criterion("high within-case relatedness", 0.15, any=("99.5", "99.6", "99.7", "99.8", "near-clonal", "near-identical")),
    Criterion("translation repertoire quantified", 0.10, any=("30-37", "30-37", "26-31", "42-46", "21-24", "22-25", "13-14", "15-17", "all amino")),
    Criterion("record/frontier or copy-number quantities", 0.10, any=("record", "frontier", "199", "copy number", "largest")),
)

LIMITATION_RULES = (
    Criterion("fragmented/incomplete genome limitation", 0.45, any=("fragmented", "incomplete", "completeness", "not closed", "n50", "contig")),
    Criterion("binning/read/contamination uncertainty", 0.25, any=("contamination", "binning", "read coverage", "raw read", "mixed", "composite")),
    Criterion("computational functional inference limitation", 0.20, any=("computational", "annotation", "structural prediction", "docking", "biochemical", "direct assay", "pfam")),
    Criterion("taxonomy/host uncertainty", 0.10, any=("uncertain", "uncertainty", "host", "taxonomy", "provisional", "cannot be resolved")),
)

CONTRADICTION_PATTERNS = (
    "rickettsiales",
    "alphaproteobacteria",
    "same bacterial species",
    "bacterial species recovered",
    "consistent with affiliation to the rickettsiales",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score candidate CSAG artifacts against a benchmark scoring schema.")
    parser.add_argument("--case-dir", type=Path, required=True, help="Path to ground_truth/<case>.")
    parser.add_argument("--candidate-dir", type=Path, required=True, help="Directory containing *_csag.json files.")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory. Defaults to <case-dir>/scoring.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(text: str) -> str:
    for dash in ("‐", "‑", "‒", "–", "—"):
        text = text.replace(dash, "-")
    return re.sub(r"\s+", " ", text).lower()


def collect_text(value: Any, key: str = "") -> list[str]:
    if isinstance(value, dict):
        chunks: list[str] = []
        for item_key, item_value in value.items():
            if item_key in SKIP_KEYS:
                continue
            chunks.extend(collect_text(item_value, item_key))
        return chunks
    if isinstance(value, list):
        chunks = []
        for item in value:
            chunks.extend(collect_text(item, key))
        return chunks
    if isinstance(value, str) and (key in TEXT_KEYS or not key.endswith("_id")):
        return [value]
    return []


def artifact_text(artifact: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in CONTENT_TOP_LEVEL_KEYS:
        if key in artifact:
            chunks.extend(collect_text(artifact[key], key))
    return normalize_text(" ".join(chunks))


def matches(text: str, pattern: str) -> bool:
    if pattern.startswith("re:"):
        return re.search(pattern[3:], text) is not None
    return pattern.lower() in text


def criterion_matches(text: str, criterion: Criterion) -> bool:
    if criterion.all:
        return all(matches(text, pattern) for pattern in criterion.all)
    return any(matches(text, pattern) for pattern in criterion.any)


def score_criteria(text: str, criteria: tuple[Criterion, ...]) -> tuple[float, list[str], list[str]]:
    total = sum(item.weight for item in criteria)
    matched: list[str] = []
    missed: list[str] = []
    score = 0.0
    for item in criteria:
        if criterion_matches(text, item):
            score += item.weight
            matched.append(item.label)
        else:
            missed.append(item.label)
    if total == 0:
        return 0.0, matched, missed
    return min(score / total, 1.0), matched, missed


def schema_check(schema: dict[str, Any]) -> dict[str, Any]:
    dimensions = schema.get("dimensions", [])
    weight_sum = sum(float(item.get("weight", 0)) for item in dimensions)
    expected = set(DIMENSION_WEIGHTS)
    present = {item.get("id") for item in dimensions}
    return {
        "case_id": schema.get("case_id"),
        "dimension_weight_sum": weight_sum,
        "dimension_ids_ok": expected == present,
        "assertion_checks": len(schema.get("assertion_checks", [])),
        "entity_checks": len(schema.get("entity_checks", [])),
        "dataset_checks": len(schema.get("dataset_checks", [])),
        "limitation_checks": len(schema.get("limitation_checks", [])),
        "method_checks_present": bool(schema.get("method_checks")),
        "quantitative_checks_present": bool(schema.get("quantitative_checks")),
    }


def score_claims(text: str, schema: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    details: list[dict[str, Any]] = []
    weighted_score = 0.0
    total_weight = 0.0
    for check in schema.get("assertion_checks", []):
        label = check.get("label", "")
        rules = CLAIM_RULES.get(label, ())
        score, matched, missed = score_criteria(text, rules)
        weight = float(check.get("weight_multiplier", 1.0))
        weighted_score += score * weight
        total_weight += weight
        details.append(
            {
                "label": label,
                "score": round(score, 3),
                "weighted_score": round(score * weight, 3),
                "weight_multiplier": weight,
                "matched": matched,
                "missed": missed,
                "assertion_text": check.get("assertion_text", ""),
            }
        )
    if total_weight == 0:
        return 0.0, details
    return weighted_score / total_weight, details


def score_candidate(path: Path, schema: dict[str, Any]) -> dict[str, Any]:
    artifact = load_json(path)
    text = artifact_text(artifact)
    validation_path = path.with_name(path.name.replace("_csag.json", "_csag.validation.json"))
    validation = load_json(validation_path) if validation_path.exists() else {}

    claim_fraction, claim_details = score_claims(text, schema)
    method_fraction, method_matched, method_missed = score_criteria(text, METHOD_RULES)
    entity_fraction, entity_matched, entity_missed = score_criteria(text, ENTITY_RULES)
    dataset_fraction, dataset_matched, dataset_missed = score_criteria(text, DATASET_RULES)
    entity_dataset_fraction = 0.75 * entity_fraction + 0.25 * dataset_fraction
    quant_fraction, quant_matched, quant_missed = score_criteria(text, QUANT_RULES)
    limitation_fraction, limitation_matched, limitation_missed = score_criteria(text, LIMITATION_RULES)

    dimension_scores = {
        "claim_coverage": claim_fraction * DIMENSION_WEIGHTS["claim_coverage"],
        "evidence_and_method_alignment": method_fraction * DIMENSION_WEIGHTS["evidence_and_method_alignment"],
        "entities_and_datasets": entity_dataset_fraction * DIMENSION_WEIGHTS["entities_and_datasets"],
        "quantitative_result_fidelity": quant_fraction * DIMENSION_WEIGHTS["quantitative_result_fidelity"],
        "limitations_and_uncertainty": limitation_fraction * DIMENSION_WEIGHTS["limitations_and_uncertainty"],
    }
    base_score = sum(dimension_scores.values())

    penalties: list[dict[str, Any]] = []
    for pattern in CONTRADICTION_PATTERNS:
        if matches(text, pattern):
            penalties.append(
                {
                    "type": "contradiction_of_ground_truth",
                    "points": -10.0,
                    "reason": "Candidate asserts a bacterial/Rickettsiales identity that contradicts the ground-truth giant-virus identity.",
                    "matched_pattern": pattern,
                }
            )
            break

    penalty_total = sum(item["points"] for item in penalties)
    total_score = max(0.0, min(100.0, base_score + penalty_total))
    top_misses = [item["label"] for item in claim_details if item["score"] < 0.5]

    return {
        "agent": path.name.replace("_csag.json", ""),
        "path": str(path.resolve()),
        "validation_ok": bool(validation.get("ok")),
        "validation_metrics": validation.get("metrics", {}),
        "base_score": round(base_score, 1),
        "penalty_total": round(penalty_total, 1),
        "total_score": round(total_score, 1),
        "dimension_scores": {key: round(value, 1) for key, value in dimension_scores.items()},
        "claim_details": claim_details,
        "method_matched": method_matched,
        "method_missed": method_missed,
        "entity_matched": entity_matched,
        "entity_missed": entity_missed,
        "dataset_matched": dataset_matched,
        "dataset_missed": dataset_missed,
        "quant_matched": quant_matched,
        "quant_missed": quant_missed,
        "limitation_matched": limitation_matched,
        "limitation_missed": limitation_missed,
        "penalties": penalties,
        "top_misses": top_misses,
    }


def slide_note(result: dict[str, Any]) -> str:
    if result["penalties"]:
        return "Contradicts viral identity; misses hidden viral-specific claims"
    if result["top_misses"]:
        return "Misses " + ", ".join(result["top_misses"][:3])
    return "Broadly aligned with ground truth"


def markdown_report(schema_info: dict[str, Any], results: list[dict[str, Any]]) -> str:
    ranked = sorted(results, key=lambda item: item["total_score"], reverse=True)
    lines = [
        "# 06mega Agent CSAG Scoring Comparison",
        "",
        "Scored from candidate `*_csag.json` artifacts against `ground_truth/06mega/scoring/scoring_schema.json`.",
        "Scores are deterministic concept matches against the ground-truth CSAG-derived checks; they are intended for comparison and slide triage, not as a substitute for a full human adjudication.",
        "",
        "## Scoring Check",
        "",
        f"- Dimension weights sum to {schema_info['dimension_weight_sum']:.0f}/100.",
        f"- Required dimensions present: {schema_info['dimension_ids_ok']}.",
        f"- Checks available: {schema_info['assertion_checks']} assertions, {schema_info['entity_checks']} entities, {schema_info['dataset_checks']} datasets, {schema_info['limitation_checks']} limitations.",
        f"- Explicit `method_checks`: {schema_info['method_checks_present']}; explicit `quantitative_checks`: {schema_info['quantitative_checks_present']}. Method and quantitative scoring below is derived from the rubric dimensions and ground-truth assertions.",
        "",
        "## Slide-Ready Ranking",
        "",
        "| Rank | Agent | Total | Claims | Evidence/methods | Entities/data | Quant | Limits | Penalty | Slide note |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for rank, item in enumerate(ranked, start=1):
        scores = item["dimension_scores"]
        lines.append(
            "| {rank} | {agent} | {total:.1f} | {claims:.1f} | {methods:.1f} | {entities:.1f} | {quant:.1f} | {limits:.1f} | {penalty:.1f} | {note} |".format(
                rank=rank,
                agent=item["agent"],
                total=item["total_score"],
                claims=scores["claim_coverage"],
                methods=scores["evidence_and_method_alignment"],
                entities=scores["entities_and_datasets"],
                quant=scores["quantitative_result_fidelity"],
                limits=scores["limitations_and_uncertainty"],
                penalty=item["penalty_total"],
                note=slide_note(item),
            )
        )

    agents = [item["agent"] for item in ranked]
    lines.extend(
        [
            "",
            "## Side-By-Side Matrix",
            "",
            "| Dimension | " + " | ".join(agents) + " |",
            "|---|" + "|".join("---:" for _ in agents) + "|",
        ]
    )
    rows = [
        ("Overall", "total_score"),
        ("Claim coverage", ("dimension_scores", "claim_coverage")),
        ("Evidence/methods", ("dimension_scores", "evidence_and_method_alignment")),
        ("Entities/data", ("dimension_scores", "entities_and_datasets")),
        ("Quantitative fidelity", ("dimension_scores", "quantitative_result_fidelity")),
        ("Limitations", ("dimension_scores", "limitations_and_uncertainty")),
        ("Penalty", "penalty_total"),
    ]
    for label, key in rows:
        values: list[str] = []
        for item in ranked:
            if isinstance(key, tuple):
                values.append(f"{item[key[0]][key[1]]:.1f}")
            else:
                values.append(f"{item[key]:.1f}")
        lines.append("| " + label + " | " + " | ".join(values) + " |")

    lines.extend(["", "## Validation Status", "", "| Agent | CSAG valid | Metrics |", "|---|---:|---|"])
    for item in ranked:
        metrics = item.get("validation_metrics", {})
        metric_text = ", ".join(f"{key}={value}" for key, value in metrics.items())
        lines.append(f"| {item['agent']} | {item['validation_ok']} | {metric_text} |")

    return "\n".join(lines) + "\n"


def write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    ranked = sorted(results, key=lambda item: item["total_score"], reverse=True)
    fieldnames = [
        "rank",
        "agent",
        "total",
        "claim_coverage",
        "evidence_and_method_alignment",
        "entities_and_datasets",
        "quantitative_result_fidelity",
        "limitations_and_uncertainty",
        "penalty",
        "validation_ok",
        "slide_note",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rank, item in enumerate(ranked, start=1):
            scores = item["dimension_scores"]
            writer.writerow(
                {
                    "rank": rank,
                    "agent": item["agent"],
                    "total": item["total_score"],
                    "claim_coverage": scores["claim_coverage"],
                    "evidence_and_method_alignment": scores["evidence_and_method_alignment"],
                    "entities_and_datasets": scores["entities_and_datasets"],
                    "quantitative_result_fidelity": scores["quantitative_result_fidelity"],
                    "limitations_and_uncertainty": scores["limitations_and_uncertainty"],
                    "penalty": item["penalty_total"],
                    "validation_ok": item["validation_ok"],
                    "slide_note": slide_note(item),
                }
            )


def main() -> int:
    args = parse_args()
    case_dir = args.case_dir.resolve()
    candidate_dir = args.candidate_dir.resolve()
    out_dir = (args.out_dir or case_dir / "scoring").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    schema = load_json(case_dir / "scoring" / "scoring_schema.json")
    candidates = sorted(candidate_dir.glob("*_csag.json"))
    if not candidates:
        raise SystemExit(f"No *_csag.json files found in {candidate_dir}")

    schema_info = schema_check(schema)
    results = [score_candidate(path, schema) for path in candidates]
    detail = {"schema_check": schema_info, "results": sorted(results, key=lambda item: item["total_score"], reverse=True)}

    detail_path = out_dir / "agent_csag_score_details.json"
    csv_path = out_dir / "agent_csag_scores.csv"
    md_path = out_dir / "agent_csag_scores.md"

    detail_path.write_text(json.dumps(detail, indent=2) + "\n", encoding="utf-8")
    write_csv(csv_path, results)
    md_path.write_text(markdown_report(schema_info, results), encoding="utf-8")

    print(md_path)
    print(csv_path)
    print(detail_path)
    print()
    print(markdown_report(schema_info, results).split("## Validation Status", 1)[0].strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
