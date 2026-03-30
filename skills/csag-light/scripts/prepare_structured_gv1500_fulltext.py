#!/usr/bin/env python3
import argparse
import json
import random
import re
from pathlib import Path


KEYWORD_RE = re.compile(
    r"(nucleocytoviricota|ncldv|imitervirales|mimivir|pandoravirus|pithovirus|"
    r"marseillevirus|mollivirus|giant virus|giant viruses|virophage|mirusvirus|"
    r"mirusviricota|phycodnavir|algavirales|pimascovirales|megavirus|megaviricetes|"
    r"viral factory|virocell)",
    re.I,
)

SECTION_BREAK_RE = re.compile(
    r"\b(introduction|background|methods?|materials and methods|results?|discussion|conclusion|importance)\b",
    re.I,
)


def load_json(path: Path):
    with path.open() as handle:
        return json.load(handle)


def extract_identifiers(path: Path):
    match = re.search(r"_(\d{7,8})", path.stem)
    pmid = match.group(1) if match else None
    paper_id = f"pmid:{pmid}" if pmid else path.stem
    return paper_id, pmid


def clean_text(value):
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value if v)
    value = str(value or "")
    value = value.replace("\r", "\n")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\|", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def trim_abstract(text, max_words=350):
    text = clean_text(text)
    if not text:
        return ""
    text = re.sub(r"^\s*abstract[:\s-]*", "", text, flags=re.I)
    match = SECTION_BREAK_RE.search(text)
    if match and match.start() > 200:
        text = text[: match.start()].strip()
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
    return text.strip()


def clean_title(title, fallback_stem):
    title = clean_text(title)
    if not title:
        return fallback_stem.replace("_", " ")
    separator_count = title.count(",") + title.count("·") + title.count(";")
    author_like = separator_count >= 3 or (bool(re.search(r"\b\d{1,2}\b", title)) and separator_count >= 2)
    too_short = len(title.split()) < 4
    bad_literal = title.lower() in {"open", "nouvelle", "sequence analysis"}
    noisy = any(token in title for token in ["\\(", "\\)", "*", "†", "‡"])
    if author_like or too_short or "serval" in title.lower() or bad_literal or noisy:
        return fallback_stem.replace("_", " ")
    return title


def build_body(record, max_chars=3500):
    pieces = [
        clean_text(record.get("main")),
        clean_text(record.get("methods")),
        clean_text(record.get("figure_interpretation")),
        clean_text(record.get("figure_legends")),
    ]
    body = "\n\n".join(piece for piece in pieces if piece)
    return body[:max_chars].strip()


def load_exclusions(index_path: Path):
    if not index_path:
        return set(), set()
    index = load_json(index_path)
    pmids = set()
    pmcs = set()
    for item in index:
        raw_path = Path(item["raw_path"])
        raw = load_json(raw_path)
        paper = raw.get("paper", {})
        pmid = paper.get("pmid")
        pmc = paper.get("pmc_id")
        if pmid:
            pmids.add(str(pmid))
        if pmc:
            pmcs.add(str(pmc))
    return pmids, pmcs


def keep_record(title, abstract, body):
    strict = bool(KEYWORD_RE.search(f"{title}\n{abstract}"))
    full = bool(KEYWORD_RE.search(f"{title}\n{abstract}\n{body[:3000]}"))
    return strict or full, strict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scores-dir", type=Path)
    parser.add_argument("--exclude-index", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-size", type=int, default=700)
    parser.add_argument("--val-size", type=int, default=80)
    parser.add_argument("--test-size", type=int, default=80)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    excluded_pmids, excluded_pmcs = load_exclusions(args.exclude_index) if args.exclude_index else (set(), set())

    all_files = sorted(
        p for p in args.input_dir.glob("*.json") if not p.name.endswith(".references.json")
    )

    kept = []
    for path in all_files:
        record = load_json(path)
        if not isinstance(record, dict):
            continue

        paper_id, pmid = extract_identifiers(path)
        if pmid and pmid in excluded_pmids:
            continue

        title = clean_title(record.get("title"), path.stem)
        abstract = trim_abstract(record.get("abstract"))
        body = build_body(record)
        if not abstract or not body:
            continue

        keep, strict = keep_record(title, abstract, body)
        if not keep:
            continue

        if args.scores_dir:
            score_path = args.scores_dir / path.name
            if score_path.exists():
                scores = load_json(score_path)
                summary = scores.get("__summary__", {})
                if summary.get("total_word_count", 0) < 1500:
                    continue

        kept.append(
            {
                "paper_id": paper_id,
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "body": body,
                "source_path": str(path),
                "strict_match": strict,
                "body_chars": len(body),
            }
        )

    rng.shuffle(kept)
    total_requested = args.train_size + args.val_size + args.test_size
    kept = kept[: min(total_requested, len(kept))]
    train = kept[: args.train_size]
    val = kept[args.train_size : args.train_size + args.val_size]
    test = kept[args.train_size + args.val_size : args.train_size + args.val_size + args.test_size]

    def write_jsonl(path: Path, rows):
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    write_jsonl(args.output_dir / "shared_train_fulltext.jsonl", train)
    write_jsonl(args.output_dir / "shared_val_fulltext.jsonl", val)
    write_jsonl(args.output_dir / "shared_test_fulltext.jsonl", test)

    summary = {
        "input_dir": str(args.input_dir),
        "structured_json_count": len(all_files),
        "kept_count": len(kept),
        "strict_match_count": sum(1 for row in kept if row["strict_match"]),
        "excluded_pmids_count": len(excluded_pmids),
        "splits": {
            "train": len(train),
            "val": len(val),
            "test": len(test),
        },
        "paths": {
            "train": str(args.output_dir / "shared_train_fulltext.jsonl"),
            "val": str(args.output_dir / "shared_val_fulltext.jsonl"),
            "test": str(args.output_dir / "shared_test_fulltext.jsonl"),
        },
        "sample_titles": [row["title"] for row in kept[:20]],
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
