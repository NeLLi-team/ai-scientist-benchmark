#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path


KEEP_TITLE_RE = re.compile(
    r"(giant virus|giant viruses|nucleocytoplasmic large dna virus|nucleo[- ]cytoplasmic large dna virus|"
    r"nucleocytovir|ncldv|mimivir|marseillevirus|marseilleviridae|pandoravirus|pithovirus|mollivirus|"
    r"medusavirus|prasinovirus|phycodnavir|chlorovirus|virophage|virophages|polinton|polintons|"
    r"transpoviron|transpovirons|imitervirales|algavirales|pimascovirales|megavirus|megaviricetes|"
    r"faustovirus|moumouvirus|klosneuvirus|tupanvirus|cedratvirus|orpheovirus|mirusvirus|mirusviricota|"
    r"naiavirus|usurpativirus|clandestinovirus|large dna viruses?)",
    re.I,
)

DROP_TITLE_RE = re.compile(
    r"(influenza|scleroderma|hepatotropic|plague coral|white plague|cytomegalovirus|toxoplasma|rubella|"
    r"vultures|himalayan vultures|sheep ked|melophagus ovinus|gut dna viromes|gut phageome|swine fever virus structural protein p17|"
    r"hiv|covid|deep sequencing diagnostic|rickettsiologists)",
    re.I,
)


def load_json(path: Path):
    with path.open() as handle:
        return json.load(handle)


def classify_title(title: str):
    if DROP_TITLE_RE.search(title):
        return "drop", "explicit_peripheral_pattern"
    if KEEP_TITLE_RE.search(title):
        return "keep", "core_title_keyword"
    return "drop", "no_core_title_keyword"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    index = load_json(args.dataset_dir / "artifact_index.json")

    keep = []
    drop = []
    rows = []
    for item in index:
        artifact = load_json(Path(item["artifact_path"]))
        title = artifact.get("title", "")
        source_run = item.get("source_run", "")

        if source_run in {"backfilled_first10", "second10_v2"}:
            decision, reason = "keep", f"seed_block:{source_run}"
        else:
            decision, reason = classify_title(title)

        row = {
            "artifact_name": item["artifact_name"],
            "artifact_path": item["artifact_path"],
            "raw_path": item["raw_path"],
            "source_run": source_run,
            "title": title,
            "decision": decision,
            "reason": reason,
        }
        rows.append(row)
        (keep if decision == "keep" else drop).append(row)

    (args.output_dir / "keep_index.json").write_text(json.dumps(keep, indent=2))
    (args.output_dir / "drop_index.json").write_text(json.dumps(drop, indent=2))
    with (args.output_dir / "curation.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["artifact_name", "source_run", "decision", "reason", "title", "artifact_path", "raw_path"],
        )
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "input_dataset_dir": str(args.dataset_dir),
        "total": len(rows),
        "keep_count": len(keep),
        "drop_count": len(drop),
        "drop_by_reason": {
            reason: sum(1 for row in drop if row["reason"] == reason)
            for reason in sorted(set(row["reason"] for row in drop))
        },
        "paths": {
            "keep_index": str(args.output_dir / "keep_index.json"),
            "drop_index": str(args.output_dir / "drop_index.json"),
            "curation_csv": str(args.output_dir / "curation.csv"),
        },
        "kept_titles_preview": [row["title"] for row in keep[:20]],
        "dropped_titles_preview": [row["title"] for row in drop[:20]],
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
