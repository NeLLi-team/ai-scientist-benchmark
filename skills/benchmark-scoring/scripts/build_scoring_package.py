#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import yaml


WEIGHTS = {
    "claim_coverage": 35,
    "evidence_and_method_alignment": 25,
    "entities_and_datasets": 15,
    "quantitative_result_fidelity": 15,
    "limitations_and_uncertainty": 10,
}

CRITICALITY_WEIGHTS = {
    "core": 2.0,
    "major": 1.5,
    "supporting": 1.0,
    "background": 0.5,
}

REQUIRED_EVIDENCE_STRENGTH = {
    "core": "moderate",
    "major": "moderate",
    "supporting": "decisive",
    "background": "",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the evaluator-facing scoring package for one benchmark case.")
    parser.add_argument("case_dir", type=Path, help="Path to ground_truth/<case>")
    return parser.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def assertion_weight(assertion: dict) -> float:
    criticality = assertion.get("criticality")
    if criticality in CRITICALITY_WEIGHTS:
        return CRITICALITY_WEIGHTS[criticality]
    role = assertion.get("claim_role")
    if role == "objective":
        return 0.5
    if role in {"discovery", "conclusion"}:
        return 1.5
    if role == "result_claim":
        return 1.25
    return 1.0


def assertion_required(assertion: dict) -> bool:
    criticality = assertion.get("criticality")
    if criticality in CRITICALITY_WEIGHTS:
        return criticality != "background"
    return assertion.get("claim_role") != "objective"


def required_evidence_strength(assertion: dict) -> str:
    criticality = assertion.get("criticality")
    if criticality in REQUIRED_EVIDENCE_STRENGTH:
        return REQUIRED_EVIDENCE_STRENGTH[criticality]
    if assertion.get("claim_role") in {"discovery", "conclusion", "result_claim"}:
        return "decisive"
    return ""


def evidence_links_by_assertion(extraction: dict) -> dict[str, list[dict]]:
    links_by_assertion: dict[str, list[dict]] = {}
    for link in extraction.get("evidence_links", []):
        assertion_id = link.get("assertion")
        if assertion_id:
            links_by_assertion.setdefault(assertion_id, []).append(link)
    return links_by_assertion


def build_schema(case: str, extraction: dict, research_question: str, starting_manifest: dict) -> dict:
    assertions = extraction.get("assertions", [])
    entities = extraction.get("entities", [])
    datasets = extraction.get("datasets", [])
    critiques = extraction.get("critiques", [])
    links_by_assertion = evidence_links_by_assertion(extraction)

    return {
        "case_id": case,
        "title": extraction.get("title", ""),
        "research_question": research_question,
        "evaluation_input": {
            "participant_final_output": "manuscript PDF produced by an independent participant agent",
            "intermediate_step": "an evaluator agent independently converts the participant manuscript PDF into a candidate knowledge artifact",
        },
        "score_range": {"min": 0, "max": 100},
        "dimensions": [
            {"id": key, "weight": value}
            for key, value in WEIGHTS.items()
        ],
        "assertion_checks": [
            {
                "assertion_id": item.get("id"),
                "label": item.get("label"),
                "claim_role": item.get("claim_role"),
                "criticality": item.get("criticality", ""),
                "assertion_text": item.get("assertion_text"),
                "falsification_criteria": item.get("falsification_criteria", []),
                "evidence_link_ids": [
                    link.get("id") for link in links_by_assertion.get(item.get("id"), []) if link.get("id")
                ],
                "evidence_strengths": [
                    link.get("strength", "") for link in links_by_assertion.get(item.get("id"), [])
                ],
                "required_evidence_strength": required_evidence_strength(item),
                "weight_multiplier": assertion_weight(item),
                "required": assertion_required(item),
            }
            for item in assertions
        ],
        "entity_checks": [
            {
                "entity_id": item.get("id"),
                "label": item.get("label"),
                "category": item.get("entity_category"),
            }
            for item in entities
        ],
        "dataset_checks": [
            {
                "dataset_id": item.get("id"),
                "label": item.get("label"),
                "repository": item.get("repository", ""),
                "accession": item.get("accession", ""),
                "dataset_url": item.get("dataset_url", ""),
            }
            for item in datasets
        ],
        "limitation_checks": [
            {
                "critique_id": item.get("id"),
                "label": item.get("label"),
                "description": item.get("description", ""),
            }
            for item in critiques
        ],
        "starting_data_visible_items": [
            item for item in starting_manifest.get("items", []) if item.get("participant_visible", True)
        ],
        "penalties": {
            "hallucinated_major_claim": -10,
            "contradiction_of_ground_truth": -10,
            "use_of_non_visible_data_without_justification": -10,
        },
    }


def build_rubric(schema: dict) -> str:
    lines = [
        "# Scoring Rubric",
        "",
        f"## Case",
        "",
        schema["case_id"],
        "",
        "## Weights",
        "",
    ]
    for dim in schema["dimensions"]:
        lines.append(f"- {dim['id']}: {dim['weight']}%")
    lines.extend(
        [
            "",
            "## Evaluation Guidance",
            "",
            "- Score claim coverage against the ground-truth assertion set, with stronger emphasis on discoveries and conclusions.",
            "- Treat core and major assertions as load-bearing; missing or contradicting them should dominate claim-coverage decisions.",
            "- Use falsification criteria to identify participant statements or omissions that would weaken otherwise plausible claim matches.",
            "- Score evidence and method alignment based on whether the participant manuscript supports its claims with methods and evidence compatible with the ground-truth artifact.",
            "- Score entity and dataset coverage on whether the participant work identifies the key biological objects and starting-data anchors.",
            "- Score quantitative fidelity when the participant manuscript reproduces or faithfully discusses major quantitative relationships.",
            "- Score limitations and uncertainty on whether the participant manuscript acknowledges the major caveats present in the ground truth.",
            "- Apply hallucination and contradiction penalties after the positive rubric is computed.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_evaluator_instructions(schema: dict) -> str:
    return (
        "# Evaluator Instructions\n\n"
        "1. Take the participant manuscript PDF as input.\n"
        "2. Independently convert it into a candidate knowledge artifact.\n"
        "3. Compare the candidate artifact to the ground-truth scoring schema in `scoring_schema.json`.\n"
        "4. Score the candidate across the weighted dimensions.\n"
        "5. Apply contradiction and hallucination penalties after the base score is computed.\n\n"
        "The participant package is intentionally incomplete with respect to the ground truth. Score only against the output manuscript and its independently derived candidate artifact, not against hidden intermediate notes.\n"
    )


def main() -> int:
    args = parse_args()
    case_dir = args.case_dir.expanduser().resolve()
    case = case_dir.name

    extraction = load_json(case_dir / "csag" / "paper_extraction.json")
    research_question = (case_dir / "prompt" / "research_question.md").read_text(encoding="utf-8").split("\n", 2)[-1].strip()
    starting_manifest = load_yaml(case_dir / "starting_data" / "manifest.yaml")

    scoring_dir = case_dir / "scoring"
    export_dir = case_dir / "exports" / "evaluator"
    scoring_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

    schema = build_schema(case, extraction, research_question, starting_manifest)
    schema_path = scoring_dir / "scoring_schema.json"
    rubric_path = scoring_dir / "scoring_rubric.md"
    instructions_path = scoring_dir / "evaluator_instructions.md"

    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    rubric_path.write_text(build_rubric(schema), encoding="utf-8")
    instructions_path.write_text(build_evaluator_instructions(schema), encoding="utf-8")

    shutil.copy2(schema_path, export_dir / "scoring_schema.json")
    shutil.copy2(rubric_path, export_dir / "scoring_rubric.md")
    shutil.copy2(instructions_path, export_dir / "evaluator_instructions.md")

    print(schema_path)
    print(rubric_path)
    print(instructions_path)
    print(export_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
