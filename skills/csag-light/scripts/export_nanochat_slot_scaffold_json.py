#!/usr/bin/env python3
import argparse
import json
import random
from pathlib import Path


FIELD_SPECS = [
    ("header", ["paper_id", "title"]),
    ("entities", ["entities"]),
    ("hypotheses", ["hypotheses"]),
    ("evidence_items", ["evidence_items"]),
    ("evidence_links", ["evidence_links"]),
    ("discoveries", ["discoveries"]),
    ("gaps", ["gaps"]),
    ("conclusions", ["conclusions"]),
    ("confidence", ["confidence"]),
]


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


def field_scaffold(full_artifact: dict, field_name: str):
    if field_name == "header":
        return {"paper_id": "", "title": ""}
    if field_name == "confidence":
        return {"confidence": {"document_confidence": 0.0}}
    if field_name == "entities":
        return {"entities": [{"entity_id": x["entity_id"], "name": "", "type": ""} for x in full_artifact["entities"]]}
    if field_name == "hypotheses":
        return {"hypotheses": [{"hypothesis_id": x["hypothesis_id"], "text": "", "status": "", "entities": []} for x in full_artifact["hypotheses"]]}
    if field_name == "evidence_items":
        return {"evidence_items": [{"evidence_id": x["evidence_id"], "text": "", "type": "", "strength": ""} for x in full_artifact["evidence_items"]]}
    if field_name == "evidence_links":
        return {"evidence_links": [{"link_id": x["link_id"], "source_evidence_id": "", "target_id": "", "target_type": "", "relation": ""} for x in full_artifact["evidence_links"]]}
    if field_name == "discoveries":
        return {"discoveries": [{"discovery_id": x["discovery_id"], "text": "", "supporting_evidence_ids": []} for x in full_artifact["discoveries"]]}
    if field_name == "gaps":
        return {"gaps": [{"gap_id": x["gap_id"], "text": "", "type": ""} for x in full_artifact["gaps"]]}
    if field_name == "conclusions":
        return {"conclusions": [{"conclusion_id": x["conclusion_id"], "text": "", "supported_by": [], "limited_by": []} for x in full_artifact["conclusions"]]}
    raise KeyError(field_name)


def quote_bank(full_artifact: dict, field_name: str, max_lines=10, max_quote_chars=120):
    mapping = {
        "header": ["entities", "hypotheses", "discoveries", "conclusions"],
        "entities": ["entities"],
        "hypotheses": ["hypotheses"],
        "evidence_items": ["evidence_items"],
        "evidence_links": ["evidence_items", "hypotheses", "discoveries", "conclusions"],
        "discoveries": ["discoveries"],
        "gaps": ["gaps"],
        "conclusions": ["conclusions"],
        "confidence": ["hypotheses", "evidence_items", "discoveries", "gaps", "conclusions"],
    }
    id_keys = {
        "entities": "entity_id",
        "hypotheses": "hypothesis_id",
        "evidence_items": "evidence_id",
        "discoveries": "discovery_id",
        "gaps": "gap_id",
        "conclusions": "conclusion_id",
    }
    lines = []
    for coll in mapping[field_name]:
        if coll in id_keys:
            for item in full_artifact.get(coll, []):
                prov = item.get("provenance", [])
                if prov and isinstance(prov, list):
                    quote = prov[0].get("quote", "")
                    if quote:
                        lines.append(f'{item[id_keys[coll]]}: "{quote[:max_quote_chars].strip()}"')
    return "\n".join(lines[:max_lines])


def slot_prompt(title: str, abstract: str, field_name: str, scaffold: dict, full_artifact: dict) -> str:
    scaffold_json = json.dumps(scaffold, ensure_ascii=True, separators=(",", ":"))
    bank = quote_bank(full_artifact, field_name)
    return (
        f"Paper title: {title}\n\n"
        f"Abstract:\n{abstract}\n\n"
        f"Field to fill: {field_name}\n\n"
        f"Retrieved snippets:\n{bank}\n\n"
        "Fill only this JSON scaffold for the requested field. "
        "Keep all keys, ids, array lengths, and key order exactly as given. "
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
    args = parser.parse_args()

    index = load_json(args.dataset_dir / "artifact_index.json")
    assert args.train_size + args.val_size + args.test_size <= len(index)

    rows = []
    for item in index:
        full_art = load_json(Path(item["artifact_path"]))
        art = minimal_artifact(full_art)
        raw = load_json(Path(item["raw_path"]))
        paper = raw["paper"]
        rows.append({
            "artifact_name": item["artifact_name"],
            "title": paper.get("title") or art.get("title"),
            "abstract": paper.get("abstract") or "",
            "artifact": art,
            "full_artifact": full_art,
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
            for field_name, keys in FIELD_SPECS:
                scaffold = field_scaffold(row["artifact"], field_name)
                prompt = slot_prompt(row["title"], row["abstract"], field_name, scaffold, row["full_artifact"])
                target = json.dumps({k: row["artifact"][k] for k in keys}, ensure_ascii=True, separators=(",", ":"))
                convs.append([
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": target},
                ])
                if not train_split:
                    bench.append({
                        "artifact_name": row["artifact_name"],
                        "question_type": field_name,
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
        "train_rows": len(train_rows),
        "benchmark_items": len(test_bench),
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
