#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def load_jsonl(path: Path):
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def document_text(record: dict) -> str:
    title = (record.get("title") or "").strip()
    abstract = (record.get("abstract") or "").strip()
    body = (record.get("body") or "").strip()
    parts = []
    if title:
        parts.append(title)
    if abstract:
        parts.append(abstract)
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def write_shard(path: Path, docs, row_group_size=64):
    table = pa.Table.from_pydict({"text": docs})
    pq.write_table(
        table,
        path,
        compression="zstd",
        row_group_size=row_group_size,
        use_dictionary=False,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-jsonl", type=Path, required=True)
    parser.add_argument("--val-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-docs-per-shard", type=int, default=200)
    parser.add_argument("--row-group-size", type=int, default=64)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    train_docs = [document_text(row) for row in load_jsonl(args.train_jsonl)]
    val_docs = [document_text(row) for row in load_jsonl(args.val_jsonl)]

    shard_paths = []
    shard_idx = 0
    for start in range(0, len(train_docs), args.train_docs_per_shard):
        shard_docs = train_docs[start : start + args.train_docs_per_shard]
        shard_path = args.output_dir / f"shard_{shard_idx:05d}.parquet"
        write_shard(shard_path, shard_docs, row_group_size=args.row_group_size)
        shard_paths.append(str(shard_path))
        shard_idx += 1

    val_path = args.output_dir / f"shard_{shard_idx:05d}.parquet"
    write_shard(val_path, val_docs, row_group_size=args.row_group_size)
    shard_paths.append(str(val_path))

    summary = {
        "train_jsonl": str(args.train_jsonl),
        "val_jsonl": str(args.val_jsonl),
        "output_dir": str(args.output_dir),
        "train_doc_count": len(train_docs),
        "val_doc_count": len(val_docs),
        "num_train_shards": len(shard_paths) - 1,
        "val_shard": str(val_path),
        "shards": shard_paths,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
