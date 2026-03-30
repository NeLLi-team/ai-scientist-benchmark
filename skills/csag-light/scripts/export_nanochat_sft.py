#!/usr/bin/env python3
import argparse
import json
import random
from pathlib import Path


def load_json(path: Path):
    with path.open() as handle:
        return json.load(handle)


def prose_from_artifact(artifact: dict) -> str:
    parts = []
    if artifact.get("hypotheses"):
        parts.append(
            "Hypotheses: " + "; ".join(item["text"] for item in artifact["hypotheses"][:3]) + "."
        )
    if artifact.get("evidence_items"):
        parts.append(
            "Evidence: " + "; ".join(item["text"] for item in artifact["evidence_items"][:4]) + "."
        )
    if artifact.get("discoveries"):
        parts.append(
            "Discoveries: " + "; ".join(item["text"] for item in artifact["discoveries"][:3]) + "."
        )
    if artifact.get("gaps"):
        parts.append(
            "Gaps: " + "; ".join(item["text"] for item in artifact["gaps"][:3]) + "."
        )
    if artifact.get("conclusions"):
        parts.append(
            "Conclusions: " + "; ".join(item["text"] for item in artifact["conclusions"][:2]) + "."
        )
    return "\n".join(parts)


def logic_prompt(title: str, abstract: str) -> str:
    return (
        f"Paper title: {title}\n\n"
        f"Abstract:\n{abstract}\n\n"
        "Extract a csag-light JSON record with the fields: paper_id, title, entities, hypotheses, "
        "evidence_items, evidence_links, discoveries, gaps, conclusions, confidence. "
        "Return JSON only."
    )


def logic_prompt_variants(title: str, abstract: str):
    return [
        logic_prompt(title, abstract),
        (
            f"Paper title: {title}\n\n"
            f"Abstract:\n{abstract}\n\n"
            "Produce a csag-light JSON object. Include paper_id, title, entities, hypotheses, "
            "evidence_items, evidence_links, discoveries, gaps, conclusions, and confidence. JSON only."
        ),
        (
            f"Given the following paper metadata and abstract, return only a valid csag-light JSON record.\n\n"
            f"Title: {title}\n\n"
            f"Abstract:\n{abstract}\n\n"
            "Required keys: paper_id, title, entities, hypotheses, evidence_items, evidence_links, discoveries, gaps, conclusions, confidence."
        ),
    ]


def regular_prompt(title: str, abstract: str) -> str:
    return (
        f"Paper title: {title}\n\n"
        f"Abstract:\n{abstract}\n\n"
        "Summarize the paper in concise scientific prose. Cover the main hypotheses, key evidence, "
        "discoveries, unresolved gaps, and conclusions."
    )


def regular_prompt_variants(title: str, abstract: str):
    return [
        regular_prompt(title, abstract),
        (
            f"Paper title: {title}\n\n"
            f"Abstract:\n{abstract}\n\n"
            "Write a compact scientific summary covering the paper's hypotheses, evidence, discoveries, gaps, and conclusions."
        ),
        (
            f"Summarize the scientific logic of this paper in prose.\n\n"
            f"Title: {title}\n\n"
            f"Abstract:\n{abstract}\n\n"
            "Include the main hypothesis, key supporting evidence, major discoveries, remaining gaps, and the main conclusion."
        ),
    ]


def qa_items_from_artifact(title: str, abstract: str, artifact: dict):
    items = []
    if artifact.get("hypotheses"):
        items.append((
            f"Paper title: {title}\n\nAbstract:\n{abstract}\n\nWhat is the main hypothesis of this paper?",
            artifact["hypotheses"][0]["text"],
            "hypothesis",
        ))
    if artifact.get("evidence_items"):
        items.append((
            f"Paper title: {title}\n\nAbstract:\n{abstract}\n\nGive one key evidence item supporting the paper's main claim.",
            artifact["evidence_items"][0]["text"],
            "evidence_item",
        ))
    if artifact.get("gaps"):
        items.append((
            f"Paper title: {title}\n\nAbstract:\n{abstract}\n\nWhat unresolved gap or limitation remains?",
            artifact["gaps"][0]["text"],
            "gap",
        ))
    if artifact.get("conclusions"):
        items.append((
            f"Paper title: {title}\n\nAbstract:\n{abstract}\n\nWhat is the main conclusion of this paper?",
            artifact["conclusions"][0]["text"],
            "conclusion",
        ))
    return items


def logic_extract_item_from_artifact(title: str, abstract: str, artifact_name: str, artifact: dict):
    return {
        "artifact_name": artifact_name,
        "question_type": "logic_extract",
        "prompt": logic_prompt(title, abstract),
        "answer": json.dumps(artifact, ensure_ascii=True),
    }


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
    parser.add_argument("--train-size", type=int, default=80)
    parser.add_argument("--val-size", type=int, default=10)
    parser.add_argument("--test-size", type=int, default=10)
    parser.add_argument("--train-regular-templates", type=int, default=1)
    parser.add_argument("--train-logic-templates", type=int, default=1)
    args = parser.parse_args()

    index = load_json(args.dataset_dir / "artifact_index.json")
    assert args.train_size + args.val_size + args.test_size <= len(index)

    rows = []
    for item in index:
        artifact = load_json(Path(item["artifact_path"]))
        raw = load_json(Path(item["raw_path"]))
        paper = raw["paper"]
        rows.append({
            "artifact_name": item["artifact_name"],
            "artifact": artifact,
            "title": paper.get("title") or artifact.get("title"),
            "abstract": paper.get("abstract") or "",
        })

    rng = random.Random(args.seed)
    rng.shuffle(rows)
    train = rows[:args.train_size]
    val = rows[args.train_size:args.train_size + args.val_size]
    test = rows[args.train_size + args.val_size:args.train_size + args.val_size + args.test_size]

    def build_split(records, train=False):
        regular = []
        logic = []
        benchmark = []
        logic_extract = []
        for row in records:
            title = row["title"]
            abstract = row["abstract"]
            artifact = row["artifact"]
            regular_prompts = regular_prompt_variants(title, abstract)
            logic_prompts = logic_prompt_variants(title, abstract)
            num_reg = min(args.train_regular_templates, len(regular_prompts)) if train else 1
            num_log = min(args.train_logic_templates, len(logic_prompts)) if train else 1
            for prompt in regular_prompts[:num_reg]:
                regular.append([
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": prose_from_artifact(artifact)},
                ])
            for prompt in logic_prompts[:num_log]:
                logic.append([
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": json.dumps(artifact, ensure_ascii=True)},
                ])
            for prompt, answer, qtype in qa_items_from_artifact(title, abstract, artifact):
                benchmark.append({
                    "artifact_name": row["artifact_name"],
                    "question_type": qtype,
                    "prompt": prompt,
                    "answer": answer,
                })
            logic_extract.append(
                logic_extract_item_from_artifact(title, abstract, row["artifact_name"], artifact)
            )
        return regular, logic, benchmark, logic_extract

    train_regular, train_logic, _, _ = build_split(train, train=True)
    val_regular, val_logic, val_bench, val_logic_extract = build_split(val, train=False)
    test_regular, test_logic, test_bench, test_logic_extract = build_split(test, train=False)

    write_jsonl(args.output_dir / "regular_train.jsonl", train_regular)
    write_jsonl(args.output_dir / "regular_val.jsonl", val_regular)
    write_jsonl(args.output_dir / "regular_test.jsonl", test_regular)
    write_jsonl(args.output_dir / "logic_train.jsonl", train_logic)
    write_jsonl(args.output_dir / "logic_val.jsonl", val_logic)
    write_jsonl(args.output_dir / "logic_test.jsonl", test_logic)
    (args.output_dir / "benchmark_val.json").write_text(json.dumps(val_bench, indent=2))
    (args.output_dir / "benchmark_test.json").write_text(json.dumps(test_bench, indent=2))
    (args.output_dir / "benchmark_logic_extract_val.json").write_text(json.dumps(val_logic_extract, indent=2))
    (args.output_dir / "benchmark_logic_extract_test.json").write_text(json.dumps(test_logic_extract, indent=2))
    (args.output_dir / "split_manifest.json").write_text(json.dumps({
        "seed": args.seed,
        "train_size": len(train),
        "val_size": len(val),
        "test_size": len(test),
        "train_regular_templates": args.train_regular_templates,
        "train_logic_templates": args.train_logic_templates,
        "paths": {
            "regular_train": str(args.output_dir / "regular_train.jsonl"),
            "logic_train": str(args.output_dir / "logic_train.jsonl"),
            "benchmark_val": str(args.output_dir / "benchmark_val.json"),
            "benchmark_test": str(args.output_dir / "benchmark_test.json"),
            "benchmark_logic_extract_val": str(args.output_dir / "benchmark_logic_extract_val.json"),
            "benchmark_logic_extract_test": str(args.output_dir / "benchmark_logic_extract_test.json"),
        },
    }, indent=2))


if __name__ == "__main__":
    main()
