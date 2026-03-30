#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def load_json(path: Path):
    with path.open() as handle:
        return json.load(handle)


def load_jsonl(path: Path):
    rows = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def fulltext_prompt(title: str, body: str) -> str:
    title = title_for_prompt(title)
    prefix = f"Paper title: {title}\n\n" if title else ""
    return prefix + f"Paper body:\n{body}\n\nWrite a concise abstract-style scientific summary of this paper."


NOISY_TITLE_RE = re.compile(
    r"(^[A-Za-z]+ \d{4}( \d+)?$|serval|^open$|^nouvelle$|[A-Za-z]+[A-Z][a-z]+[A-Z])",
    re.I,
)


def title_for_prompt(title: str) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    if NOISY_TITLE_RE.search(title):
        return ""
    if any(ch in title for ch in ["·", "†", "‡"]):
        return ""
    return title


def clean_shared_benchmark_record(record):
    title = title_for_prompt(record.get("title") or "")
    abstract = (record.get("abstract") or "").strip()
    if title and len(title.split()) < 4:
        return False
    if len(abstract) < 400 or len(abstract) > 4000:
        return False
    if abstract[:80].count(",") >= 6:
        return False
    if abstract.lower().startswith(("summary", "introduction", "results ")):
        return False
    ascii_ratio = sum(ord(ch) < 128 for ch in abstract) / max(len(abstract), 1)
    if ascii_ratio < 0.9:
        return False
    return True


def build_fulltext_examples(records):
    rows = []
    benchmark = []
    for record in records:
        rows.append([
            {"role": "user", "content": fulltext_prompt(record["title"], record["body"])},
            {"role": "assistant", "content": record["abstract"]},
        ])
        benchmark.append({
            "paper_id": record["paper_id"],
            "prompt": fulltext_prompt(record["title"], record["body"]),
            "answer": record["abstract"],
            "question_type": "abstract_summary",
        })
    return rows, benchmark


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shared-dir", type=Path, required=True)
    parser.add_argument("--logic-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    shared_train = load_jsonl(args.shared_dir / "shared_train_fulltext.jsonl")
    shared_val = load_jsonl(args.shared_dir / "shared_val_fulltext.jsonl")
    shared_test = load_jsonl(args.shared_dir / "shared_test_fulltext.jsonl")

    shared_train_rows, _ = build_fulltext_examples(shared_train)
    shared_val_rows, shared_val_bench = build_fulltext_examples(shared_val)
    shared_test_rows, shared_test_bench = build_fulltext_examples(shared_test)
    shared_test_bench_clean = [
        item for item, record in zip(shared_test_bench, shared_test)
        if clean_shared_benchmark_record(record)
    ]

    regular_train = load_jsonl(args.logic_dir / "regular_train.jsonl")
    regular_val = load_jsonl(args.logic_dir / "regular_val.jsonl")
    logic_train = load_jsonl(args.logic_dir / "logic_train.jsonl")
    logic_val = load_jsonl(args.logic_dir / "logic_val.jsonl")

    write_jsonl(args.output_dir / "shared_train.jsonl", shared_train_rows)
    write_jsonl(args.output_dir / "shared_val.jsonl", shared_val_rows)
    write_jsonl(args.output_dir / "shared_test.jsonl", shared_test_rows)

    write_jsonl(args.output_dir / "ab_regular_train.jsonl", shared_train_rows + regular_train)
    write_jsonl(args.output_dir / "ab_regular_val.jsonl", shared_val_rows + regular_val)
    write_jsonl(args.output_dir / "ab_logic_train.jsonl", shared_train_rows + logic_train)
    write_jsonl(args.output_dir / "ab_logic_val.jsonl", shared_val_rows + logic_val)

    (args.output_dir / "benchmark_shared_test.json").write_text(json.dumps(shared_test_bench, indent=2))
    (args.output_dir / "benchmark_shared_test_clean.json").write_text(json.dumps(shared_test_bench_clean, indent=2))
    (args.output_dir / "benchmark_logic_test.json").write_text(
        (args.logic_dir / "benchmark_test.json").read_text()
    )
    (args.output_dir / "benchmark_logic_extract.json").write_text(
        (args.logic_dir / "benchmark_logic_extract.json").read_text()
    )

    manifest = {
        "shared_train_count": len(shared_train_rows),
        "shared_val_count": len(shared_val_rows),
        "shared_test_count": len(shared_test_rows),
        "regular_added_train_count": len(regular_train),
        "logic_added_train_count": len(logic_train),
        "ab_regular_train_total": len(shared_train_rows + regular_train),
        "ab_logic_train_total": len(shared_train_rows + logic_train),
        "shared_test_clean_count": len(shared_test_bench_clean),
        "paths": {
            "ab_regular_train": str(args.output_dir / "ab_regular_train.jsonl"),
            "ab_logic_train": str(args.output_dir / "ab_logic_train.jsonl"),
            "benchmark_shared_test": str(args.output_dir / "benchmark_shared_test.json"),
            "benchmark_shared_test_clean": str(args.output_dir / "benchmark_shared_test_clean.json"),
            "benchmark_logic_test": str(args.output_dir / "benchmark_logic_test.json"),
            "benchmark_logic_extract": str(args.output_dir / "benchmark_logic_extract.json"),
        },
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
