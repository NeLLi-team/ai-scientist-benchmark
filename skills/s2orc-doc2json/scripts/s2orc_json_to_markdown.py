#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from html import unescape
from pathlib import Path
from typing import Iterable


def clean_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def author_name(author: dict) -> str:
    parts = [author.get("first", "").strip()]
    parts.extend(part.strip() for part in author.get("middle", []) if part.strip())
    parts.append(author.get("last", "").strip())
    suffix = author.get("suffix", "").strip()
    pieces = [piece for piece in parts if piece]
    if suffix:
        pieces.append(suffix)
    return " ".join(pieces).strip()


def iter_grouped_sections(blocks: Iterable[dict]) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    for block in blocks:
        text = clean_text(block.get("text", ""))
        if not text:
            continue
        section = clean_text(block.get("section", "")) or "Body"
        if sections and sections[-1][0] == section:
            sections[-1][1].append(text)
        else:
            sections.append((section, [text]))
    return sections


def bibliography_line(entry: dict) -> str:
    raw_text = clean_text(entry.get("raw_text", ""))
    if raw_text:
        return raw_text

    authors = []
    for author in entry.get("authors", []):
        name = author_name(author)
        if name:
            authors.append(name)

    pieces = []
    if authors:
        pieces.append(", ".join(authors))
    if entry.get("year"):
        pieces.append(str(entry["year"]))
    if entry.get("title"):
        pieces.append(clean_text(entry["title"]))
    if entry.get("venue"):
        pieces.append(clean_text(entry["venue"]))
    if entry.get("pages"):
        pieces.append(clean_text(entry["pages"]))
    return ". ".join(piece for piece in pieces if piece).strip()


def build_markdown(data: dict) -> str:
    lines: list[str] = []

    title = clean_text(data.get("title", "")) or clean_text(data.get("paper_id", "Untitled"))
    lines.append(f"# {title}")
    lines.append("")

    authors = [author_name(author) for author in data.get("authors", [])]
    authors = [author for author in authors if author]
    if authors:
        lines.append(f"**Authors:** {', '.join(authors)}")
        lines.append("")

    if data.get("year"):
        lines.append(f"**Year:** {data['year']}")
        lines.append("")

    abstract_blocks = data.get("pdf_parse", {}).get("abstract") or []
    abstract_text = "\n\n".join(clean_text(block.get("text", "")) for block in abstract_blocks if clean_text(block.get("text", "")))
    if abstract_text:
        lines.append("## Abstract")
        lines.append("")
        lines.append(abstract_text)
        lines.append("")

    body_sections = iter_grouped_sections(data.get("pdf_parse", {}).get("body_text") or [])
    for section, paragraphs in body_sections:
        lines.append(f"## {section}")
        lines.append("")
        lines.extend(paragraphs)
        lines.append("")

    back_sections = iter_grouped_sections(data.get("pdf_parse", {}).get("back_matter") or [])
    if back_sections:
        for section, paragraphs in back_sections:
            heading = section if section != "Body" else "Back Matter"
            lines.append(f"## {heading}")
            lines.append("")
            lines.extend(paragraphs)
            lines.append("")

    ref_entries = data.get("pdf_parse", {}).get("ref_entries") or {}
    if ref_entries:
        lines.append("## Figures And Tables")
        lines.append("")
        for ref_id in sorted(ref_entries):
            entry = ref_entries[ref_id]
            ref_type = clean_text(entry.get("type_str", "reference")).title()
            caption = clean_text(entry.get("text", ""))
            if not caption:
                continue
            lines.append(f"### {ref_id} ({ref_type})")
            lines.append("")
            lines.append(caption)
            lines.append("")

    bib_entries = data.get("pdf_parse", {}).get("bib_entries") or {}
    rendered_bibliography = []
    for bib_id in sorted(bib_entries):
        line = bibliography_line(bib_entries[bib_id])
        if line:
            rendered_bibliography.append(f"- {line}")
    if rendered_bibliography:
        lines.append("## References")
        lines.append("")
        lines.extend(rendered_bibliography)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render S2ORC JSON into readable Markdown.")
    parser.add_argument("input_json", help="Path to the S2ORC JSON file")
    parser.add_argument("--output", help="Path to the Markdown output file")
    return parser.parse_args()


def default_output_path(input_path: Path) -> Path:
    if input_path.name.endswith(".s2orc.json"):
        return input_path.with_name(input_path.name[: -len(".s2orc.json")] + ".md")
    return input_path.with_suffix(".md")


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_json).resolve()
    output_path = Path(args.output).resolve() if args.output else default_output_path(input_path)

    data = json.loads(input_path.read_text(encoding="utf-8"))
    markdown = build_markdown(data)
    output_path.write_text(markdown, encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
