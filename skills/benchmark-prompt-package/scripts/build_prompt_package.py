#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the participant-facing benchmark prompt package.")
    parser.add_argument("case_dir", type=Path, help="Path to ground_truth/<case>")
    parser.add_argument("--research-question", help="Curated research question to use")
    return parser.parse_args()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def derive_research_question(extraction: dict, title: str) -> str:
    for assertion in extraction.get("assertions", []):
        if assertion.get("claim_role") in {"objective", "research_question", "hypothesis"}:
            text = assertion.get("assertion_text", "").strip()
            if text:
                return text.rstrip(".?") + "?"
    return f"What can be inferred from the participant-visible starting data about the core claims made in \"{title}\"?"


def manifest_visible_items(starting_data: dict) -> list[dict]:
    items = starting_data.get("items", [])
    return [item for item in items if item.get("participant_visible", True)]


def build_prompt(case_dir: Path, title: str, question: str, visible_items: list[dict]) -> str:
    bullets = []
    for item in visible_items:
        name = item.get("name", "unnamed item")
        role = item.get("role", "starting data")
        summary = item.get("summary", "")
        repo = item.get("repository", "")
        accessions = item.get("accessions", [])
        urls = item.get("urls", [])
        line = f"- {name}: {role}"
        if summary:
            line += f". {summary}"
        if repo:
            line += f" Repository: {repo}."
        if accessions:
            line += f" Accessions/IDs: {', '.join(accessions)}."
        if urls:
            line += f" URLs: {', '.join(urls)}."
        bullets.append(line)
    visible_block = "\n".join(bullets) if bullets else "- No participant-visible starting-data items were declared."

    return (
        f"# Participant Prompt\n\n"
        f"## Research Question\n\n"
        f"{question}\n\n"
        f"## Allowed Starting Data\n\n"
        f"{visible_block}\n\n"
        f"## Task\n\n"
        f"Use only the participant-visible starting data and your own downstream analyses to investigate the research question. "
        f"Do not assume access to the original manuscript, any ground-truth knowledge artifact, or any evaluator materials.\n\n"
        f"Your final deliverable should be a manuscript-style PDF that presents your methods, results, interpretation, and limitations.\n\n"
        f"## Restrictions\n\n"
        f"- Do not rely on hidden benchmark files.\n"
        f"- Do not assume the claims in the original paper are correct.\n"
        f"- Treat the starting data as the scientific starting point for an independent investigation.\n"
        f"- Make uncertainty explicit when evidence is incomplete.\n"
    )


def main() -> int:
    args = parse_args()
    case_dir = args.case_dir.expanduser().resolve()
    case = case_dir.name

    article_path = case_dir / "data" / f"{case}.article.json"
    extraction_path = case_dir / "csag" / "paper_extraction.json"
    starting_manifest_path = case_dir / "starting_data" / "manifest.yaml"
    download_instructions_path = case_dir / "starting_data" / "download_instructions.md"

    article = load_json(article_path)
    extraction = load_json(extraction_path)
    starting_data = yaml.safe_load(starting_manifest_path.read_text(encoding="utf-8")) or {}

    question = args.research_question.strip() if args.research_question else derive_research_question(extraction, article["title"])
    visible_items = manifest_visible_items(starting_data)

    prompt_dir = case_dir / "prompt"
    export_dir = case_dir / "exports" / "participant"
    ensure_dir(prompt_dir)
    ensure_dir(export_dir)

    research_question_path = prompt_dir / "research_question.md"
    participant_prompt_path = prompt_dir / "participant_prompt.md"
    participant_manifest_path = prompt_dir / "participant_package_manifest.json"

    research_question_path.write_text(f"# Research Question\n\n{question}\n", encoding="utf-8")
    participant_prompt_path.write_text(build_prompt(case_dir, article["title"], question, visible_items), encoding="utf-8")

    participant_manifest = {
        "case_id": case,
        "title": article["title"],
        "research_question": question,
        "participant_visible_items": visible_items,
        "exported_files": [
            "research_question.md",
            "participant_prompt.md",
            "starting_data_manifest.yaml",
            "download_instructions.md",
        ],
        "excluded_files": [
            "data/",
            "csag/",
            "scoring/",
        ],
    }
    participant_manifest_path.write_text(json.dumps(participant_manifest, indent=2) + "\n", encoding="utf-8")

    shutil.copy2(research_question_path, export_dir / "research_question.md")
    shutil.copy2(participant_prompt_path, export_dir / "participant_prompt.md")
    shutil.copy2(starting_manifest_path, export_dir / "starting_data_manifest.yaml")
    shutil.copy2(download_instructions_path, export_dir / "download_instructions.md")

    print(research_question_path)
    print(participant_prompt_path)
    print(participant_manifest_path)
    print(export_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
