#!/usr/bin/env python3
import argparse
import json
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
    title = (title or "").strip()
    prefix = f"Paper title: {title}\n\n" if title else ""
    return prefix + f"Paper body:\n{body}\n\nWrite a concise abstract-style scientific summary of this paper."


def build_shared_conversations(records):
    rows = []
    for record in records:
        rows.append([
            {"role": "user", "content": fulltext_prompt(record["title"], record["body"])},
            {"role": "assistant", "content": record["abstract"]},
        ])
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shared-dir", type=Path, required=True)
    parser.add_argument("--shared-benchmark-dir", type=Path, default=None)
    parser.add_argument("--core-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    shared_train_records = load_jsonl(args.shared_dir / "shared_train_fulltext.jsonl")
    shared_val_records = load_jsonl(args.shared_dir / "shared_val_fulltext.jsonl")
    shared_test_records = load_jsonl(args.shared_dir / "shared_test_fulltext.jsonl")
    shared_train = build_shared_conversations(shared_train_records)
    shared_val = build_shared_conversations(shared_val_records)
    shared_test = build_shared_conversations(shared_test_records)
    shared_benchmark_dir = args.shared_benchmark_dir or args.shared_dir

    core_regular_train = load_jsonl(args.core_dir / "regular_train.jsonl")
    core_regular_val = load_jsonl(args.core_dir / "regular_val.jsonl")
    core_logic_train = load_jsonl(args.core_dir / "logic_train.jsonl")
    core_logic_val = load_jsonl(args.core_dir / "logic_val.jsonl")

    write_jsonl(args.output_dir / "shared_only_train.jsonl", shared_train)
    write_jsonl(args.output_dir / "shared_only_val.jsonl", shared_val)

    write_jsonl(args.output_dir / "shared_plus_regular_train.jsonl", shared_train + core_regular_train)
    write_jsonl(args.output_dir / "shared_plus_regular_val.jsonl", shared_val + core_regular_val)

    write_jsonl(args.output_dir / "shared_plus_logic_train.jsonl", shared_train + core_logic_train)
    write_jsonl(args.output_dir / "shared_plus_logic_val.jsonl", shared_val + core_logic_val)

    # Benchmarks: cleaner shared-summary benchmark plus held-out core logic benchmarks
    for src, dst in [
        (shared_benchmark_dir / "benchmark_shared_test_clean.json", args.output_dir / "benchmark_shared_test_clean.json"),
        (args.core_dir / "benchmark_test.json", args.output_dir / "benchmark_logic_test.json"),
        (args.core_dir / "benchmark_logic_extract_test.json", args.output_dir / "benchmark_logic_extract_test.json"),
    ]:
        dst.write_text(src.read_text())

    manifest = {
        "shared_train_count": len(shared_train_records),
        "shared_val_count": len(shared_val_records),
        "shared_test_count": len(load_json(shared_benchmark_dir / "benchmark_shared_test_clean.json")),
        "core_regular_train_count": len(core_regular_train),
        "core_logic_train_count": len(core_logic_train),
        "shared_only_train_total": len(shared_train),
        "shared_plus_regular_train_total": len(shared_train + core_regular_train),
        "shared_plus_logic_train_total": len(shared_train + core_logic_train),
        "paths": {
            "shared_only_train": str(args.output_dir / "shared_only_train.jsonl"),
            "shared_plus_regular_train": str(args.output_dir / "shared_plus_regular_train.jsonl"),
            "shared_plus_logic_train": str(args.output_dir / "shared_plus_logic_train.jsonl"),
            "benchmark_shared_test_clean": str(args.output_dir / "benchmark_shared_test_clean.json"),
            "benchmark_logic_test": str(args.output_dir / "benchmark_logic_test.json"),
            "benchmark_logic_extract_test": str(args.output_dir / "benchmark_logic_extract_test.json"),
        },
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
