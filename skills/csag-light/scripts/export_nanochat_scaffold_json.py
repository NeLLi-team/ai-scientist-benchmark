#!/usr/bin/env python3
import argparse
import json
import random
from pathlib import Path


def load_json(path: Path):
    with path.open() as handle:
        return json.load(handle)


def minimal_artifact(artifact: dict) -> dict:
    out = {
        "paper_id": artifact.get("paper_id"),
        "title": artifact.get("title"),
        "entities": [],
        "hypotheses": [],
        "evidence_items": [],
        "evidence_links": [],
        "discoveries": [],
        "gaps": [],
        "conclusions": [],
        "confidence": {
            "document_confidence": (artifact.get("confidence") or {}).get("document_confidence", 0.0)
        },
    }
    for item in artifact.get("entities", []):
        out["entities"].append({
            "entity_id": item.get("entity_id"),
            "name": item.get("name"),
            "type": item.get("type"),
        })
    for item in artifact.get("hypotheses", []):
        out["hypotheses"].append({
            "hypothesis_id": item.get("hypothesis_id"),
            "text": item.get("text"),
            "status": item.get("status"),
            "entities": item.get("entities", []),
        })
    for item in artifact.get("evidence_items", []):
        out["evidence_items"].append({
            "evidence_id": item.get("evidence_id"),
            "text": item.get("text"),
            "type": item.get("type"),
            "strength": item.get("strength"),
        })
    for item in artifact.get("evidence_links", []):
        out["evidence_links"].append({
            "link_id": item.get("link_id"),
            "source_evidence_id": item.get("source_evidence_id"),
            "target_id": item.get("target_id"),
            "target_type": item.get("target_type"),
            "relation": item.get("relation"),
        })
    for item in artifact.get("discoveries", []):
        out["discoveries"].append({
            "discovery_id": item.get("discovery_id"),
            "text": item.get("text"),
            "supporting_evidence_ids": item.get("supporting_evidence_ids", []),
        })
    for item in artifact.get("gaps", []):
        out["gaps"].append({
            "gap_id": item.get("gap_id"),
            "text": item.get("text"),
            "type": item.get("type"),
        })
    for item in artifact.get("conclusions", []):
        out["conclusions"].append({
            "conclusion_id": item.get("conclusion_id"),
            "text": item.get("text"),
            "supported_by": item.get("supported_by", []),
            "limited_by": item.get("limited_by", []),
        })
    return out


def scaffold_from_artifact(artifact: dict) -> dict:
    out = {
        "paper_id": "",
        "title": "",
        "entities": [],
        "hypotheses": [],
        "evidence_items": [],
        "evidence_links": [],
        "discoveries": [],
        "gaps": [],
        "conclusions": [],
        "confidence": {"document_confidence": 0.0},
    }
    for item in artifact.get("entities", []):
        out["entities"].append({
            "entity_id": item["entity_id"],
            "name": "",
            "type": "",
        })
    for item in artifact.get("hypotheses", []):
        out["hypotheses"].append({
            "hypothesis_id": item["hypothesis_id"],
            "text": "",
            "status": "",
            "entities": [],
        })
    for item in artifact.get("evidence_items", []):
        out["evidence_items"].append({
            "evidence_id": item["evidence_id"],
            "text": "",
            "type": "",
            "strength": "",
        })
    for item in artifact.get("evidence_links", []):
        out["evidence_links"].append({
            "link_id": item["link_id"],
            "source_evidence_id": "",
            "target_id": "",
            "target_type": "",
            "relation": "",
        })
    for item in artifact.get("discoveries", []):
        out["discoveries"].append({
            "discovery_id": item["discovery_id"],
            "text": "",
            "supporting_evidence_ids": [],
        })
    for item in artifact.get("gaps", []):
        out["gaps"].append({
            "gap_id": item["gap_id"],
            "text": "",
            "type": "",
        })
    for item in artifact.get("conclusions", []):
        out["conclusions"].append({
            "conclusion_id": item["conclusion_id"],
            "text": "",
            "supported_by": [],
            "limited_by": [],
        })
    return out


def scaffold_prompt(title: str, abstract: str, scaffold: dict) -> str:
    scaffold_json = json.dumps(scaffold, ensure_ascii=True, separators=(",", ":"))
    return (
        f"Paper title: {title}\n\n"
        f"Abstract:\n{abstract}\n\n"
        "Fill the following JSON scaffold with the correct values for this paper. "
        "Keep all keys, ids, array lengths, and key order exactly as given. "
        "Replace only the empty strings, empty arrays, and document_confidence value. "
        "Return JSON only.\n\n"
        f"Scaffold:\n{scaffold_json}"
    )


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-size", type=int, default=49)
    parser.add_argument("--val-size", type=int, default=10)
    parser.add_argument("--test-size", type=int, default=10)
    parser.add_argument("--train-templates", type=int, default=2)
    args = parser.parse_args()

    index = load_json(args.dataset_dir / "artifact_index.json")
    assert args.train_size + args.val_size + args.test_size <= len(index)

    rows = []
    for item in index:
        artifact = minimal_artifact(load_json(Path(item["artifact_path"])))
        raw = load_json(Path(item["raw_path"]))
        paper = raw["paper"]
        rows.append({
            "artifact_name": item["artifact_name"],
            "title": paper.get("title") or artifact.get("title"),
            "abstract": paper.get("abstract") or "",
            "artifact": artifact,
            "scaffold": scaffold_from_artifact(artifact),
        })

    rng = random.Random(args.seed)
    rng.shuffle(rows)
    train = rows[:args.train_size]
    val = rows[args.train_size:args.train_size + args.val_size]
    test = rows[args.train_size + args.val_size:args.train_size + args.val_size + args.test_size]

    def build(records, train_split=False):
        convs = []
        bench = []
        for row in records:
            prompt = scaffold_prompt(row["title"], row["abstract"], row["scaffold"])
            target = json.dumps(row["artifact"], ensure_ascii=True, separators=(",", ":"))
            n = args.train_templates if train_split else 1
            for _ in range(n):
                convs.append([
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": target},
                ])
            bench.append({
                "artifact_name": row["artifact_name"],
                "question_type": "logic_extract",
                "prompt": prompt,
                "answer": target,
            })
        return convs, bench

    train_rows, _ = build(train, train_split=True)
    val_rows, val_bench = build(val, train_split=False)
    test_rows, test_bench = build(test, train_split=False)

    write_jsonl(args.output_dir / "train.jsonl", train_rows)
    write_jsonl(args.output_dir / "val.jsonl", val_rows)
    write_jsonl(args.output_dir / "test.jsonl", test_rows)
    (args.output_dir / "benchmark_val.json").write_text(json.dumps(val_bench, indent=2))
    (args.output_dir / "benchmark_test.json").write_text(json.dumps(test_bench, indent=2))
    (args.output_dir / "manifest.json").write_text(json.dumps({
        "seed": args.seed,
        "train_size": len(train),
        "val_size": len(val),
        "test_size": len(test),
        "train_templates": args.train_templates,
        "train_rows": len(train_rows),
        "paths": {
            "train": str(args.output_dir / "train.jsonl"),
            "val": str(args.output_dir / "val.jsonl"),
            "benchmark_test": str(args.output_dir / "benchmark_test.json"),
        },
    }, indent=2))
    print(json.dumps({
        "train_rows": len(train_rows),
        "val_rows": len(val_rows),
        "benchmark_items": len(test_bench),
        "paths": {
            "train": str(args.output_dir / "train.jsonl"),
            "val": str(args.output_dir / "val.jsonl"),
            "benchmark_test": str(args.output_dir / "benchmark_test.json"),
        },
    }, indent=2))


if __name__ == "__main__":
    main()
