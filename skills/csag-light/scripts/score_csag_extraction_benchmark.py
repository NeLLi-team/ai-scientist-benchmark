#!/usr/bin/env python3
import argparse
import json
import re
from collections import Counter
from pathlib import Path


FIELDS = [
    ("entities", "name"),
    ("hypotheses", "text"),
    ("evidence_items", "text"),
    ("discoveries", "text"),
    ("gaps", "text"),
    ("conclusions", "text"),
]


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def safe_json_loads(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def bag_f1(pred_items, gold_items):
    pred_counts = Counter(pred_items)
    gold_counts = Counter(gold_items)
    overlap = sum(min(pred_counts[k], gold_counts[k]) for k in pred_counts)
    if not pred_items or not gold_items or overlap == 0:
        return 0.0
    precision = overlap / sum(pred_counts.values())
    recall = overlap / sum(gold_counts.values())
    return 2 * precision * recall / (precision + recall)


def extract_field_texts(obj, field_name, text_key):
    values = []
    for item in obj.get(field_name, []) if isinstance(obj, dict) else []:
        if isinstance(item, dict):
            value = item.get(text_key)
            if isinstance(value, str) and value.strip():
                values.append(normalize(value))
    return values


def edge_set(obj):
    if not isinstance(obj, dict):
        return []
    evidence_lookup = {}
    target_lookup = {}
    for item in obj.get("evidence_items", []):
        if isinstance(item, dict):
            evidence_lookup[item.get("evidence_id")] = normalize(item.get("text", ""))
    for field, id_key, text_key in [
        ("hypotheses", "hypothesis_id", "text"),
        ("discoveries", "discovery_id", "text"),
        ("gaps", "gap_id", "text"),
        ("conclusions", "conclusion_id", "text"),
    ]:
        for item in obj.get(field, []):
            if isinstance(item, dict):
                target_lookup[item.get(id_key)] = (field[:-1], normalize(item.get(text_key, "")))

    edges = []
    for link in obj.get("evidence_links", []):
        if not isinstance(link, dict):
            continue
        source = evidence_lookup.get(link.get("source_evidence_id"), "")
        target_type, target_text = target_lookup.get(link.get("target_id"), (link.get("target_type", ""), ""))
        relation = normalize(link.get("relation", ""))
        if source and target_text and relation:
            edges.append((relation, target_type, source, target_text))
    return edges


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-result-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.benchmark_result_json.read_text())
    results = report.get("results", [])
    scored = []

    for item in results:
        pred_obj = safe_json_loads(item.get("prediction", ""))
        gold_obj = safe_json_loads(item.get("gold_answer", ""))
        metrics = {
            "artifact_name": item.get("artifact_name", "item"),
            "json_parse_success": float(pred_obj is not None),
            "schema_keys_present": 0.0,
            "field_f1": {},
            "edge_f1": 0.0,
        }

        if pred_obj is not None and isinstance(pred_obj, dict):
            required = {
                "paper_id", "title", "entities", "hypotheses", "evidence_items",
                "evidence_links", "discoveries", "gaps", "conclusions", "confidence",
            }
            metrics["schema_keys_present"] = sum(1 for k in required if k in pred_obj) / len(required)

        if pred_obj is not None and gold_obj is not None and isinstance(pred_obj, dict) and isinstance(gold_obj, dict):
            field_scores = []
            for field_name, text_key in FIELDS:
                pred_vals = extract_field_texts(pred_obj, field_name, text_key)
                gold_vals = extract_field_texts(gold_obj, field_name, text_key)
                f1 = bag_f1(pred_vals, gold_vals)
                metrics["field_f1"][field_name] = f1
                field_scores.append(f1)
            metrics["macro_field_f1"] = sum(field_scores) / len(field_scores) if field_scores else 0.0
            metrics["edge_f1"] = bag_f1(edge_set(pred_obj), edge_set(gold_obj))
        else:
            metrics["macro_field_f1"] = 0.0
            for field_name, _ in FIELDS:
                metrics["field_f1"][field_name] = 0.0

        scored.append(metrics)

    summary = {
        "benchmark_result_json": str(args.benchmark_result_json),
        "num_items": len(scored),
        "json_parse_rate": sum(x["json_parse_success"] for x in scored) / len(scored) if scored else 0.0,
        "schema_keys_present_rate": sum(x["schema_keys_present"] for x in scored) / len(scored) if scored else 0.0,
        "macro_field_f1": sum(x["macro_field_f1"] for x in scored) / len(scored) if scored else 0.0,
        "edge_f1": sum(x["edge_f1"] for x in scored) / len(scored) if scored else 0.0,
        "field_f1": {
            field_name: sum(x["field_f1"][field_name] for x in scored) / len(scored) if scored else 0.0
            for field_name, _ in FIELDS
        },
        "results": scored,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
