#!/usr/bin/env python3
"""Build a blinded copy of a benchmark case with neutral assembly and contig names.

The source case keeps its FASTA files under `starting_data/files/`. This script
assigns the files to aliases `assembly_A`, `assembly_B`, ... with a seeded shuffle,
renames every contig to `assembly_X_contig_NNNN`, and writes the participant
package, a private blinding map, and a `case.yaml` that points at the reused
ground truth. Sequence lines are copied byte for byte, so line wrapping stays as in
the source. The private map must never enter an export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import shutil
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PUBLIC_CASE_ID = "blinded-case"
ASSEMBLY_SUMMARY = "Nucleotide FASTA assembly of a metagenome-derived genome bin."
PUBLIC_TITLE = "Blinded metagenome-assembly interpretation task"
GENERIC_FORBIDDEN = ("IMG",)
GROUND_TRUTH_FILES = {
    "csag": "csag/paper_extraction.json",
    "scoring_schema": "scoring/scoring_schema.json",
}
PRIVATE_NOTICE = (
    "PRIVATE. Maps participant aliases to the original assemblies and contigs. "
    "Never copy this file into an export or show it to a participant."
)


@dataclass(frozen=True, slots=True)
class BlindedAssembly:
    """One aliased FASTA file and the facts needed to undo the aliasing."""

    alias: str
    original_file: str
    original_sha256: str
    aliased_sha256: str
    header_map: dict[str, str]


def blind_case(
    source: Path,
    dest: Path,
    seed: str,
    prompt_file: Path,
    research_question: str,
) -> None:
    """Write the blinded case at `dest` from the case at `source`."""
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"destination is not empty: {dest}")
    files = sorted((source / "starting_data" / "files").glob("*.fna"))
    aliases = [f"assembly_{letter}" for letter in string.ascii_uppercase[: len(files)]]
    shuffled = random.Random(seed).sample(files, k=len(files))

    files_dir = dest / "starting_data" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    assemblies = [
        _blind_fasta(original, files_dir / f"{alias}.fna", alias)
        for alias, original in zip(aliases, shuffled, strict=True)
    ]

    _write_participant_package(dest, assemblies, prompt_file, research_question)
    _write_private_map(dest, source, seed, assemblies)
    _write_case_yaml(dest, source)
    assert_no_leaks(
        [dest / "exports" / "participant", dest / "starting_data", dest / "prompt"],
        _forbidden_tokens(shuffled, assemblies),
    )


def assert_no_leaks(directories: Iterable[Path], tokens: Iterable[str]) -> None:
    """Raise ValueError if any token appears, in any case, under the directories."""
    pattern = re.compile(
        "|".join(re.escape(token) for token in _minimal_tokens(tokens)), re.IGNORECASE
    )
    for directory in directories:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            match = pattern.search(path.read_text(encoding="utf-8"))
            if match:
                raise ValueError(
                    f"identifying string {match.group()!r} found in {path}"
                )


def _minimal_tokens(tokens: Iterable[str]) -> list[str]:
    """Drop tokens that contain another token, since the shorter one matches first."""
    unique = sorted({token.lower() for token in tokens}, key=len)
    kept: list[str] = []
    for token in unique:
        if not any(shorter in token for shorter in kept):
            kept.append(token)
    return kept


def _blind_fasta(original: Path, aliased: Path, alias: str) -> BlindedAssembly:
    header_map: dict[str, str] = {}
    with original.open("rb") as reader, aliased.open("wb") as writer:
        for line in reader:
            if not line.startswith(b">"):
                writer.write(line)
                continue
            new_header = f"{alias}_contig_{len(header_map) + 1:04d}"
            header_map[new_header] = line[1:].decode("utf-8").rstrip("\r\n")
            writer.write(f">{new_header}\n".encode())
    return BlindedAssembly(
        alias=alias,
        original_file=original.name,
        original_sha256=_sha256(original),
        aliased_sha256=_sha256(aliased),
        header_map=header_map,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _forbidden_tokens(
    originals: Sequence[Path], assemblies: Sequence[BlindedAssembly]
) -> set[str]:
    """Collect file stems, header IDs (first word), bin IDs and contig prefixes."""
    tokens = set(GENERIC_FORBIDDEN)
    tokens.update(original.stem for original in originals)
    for assembly in assemblies:
        for header in assembly.header_map.values():
            identifier = header.split(maxsplit=1)[0] if header.strip() else ""
            tokens.add(identifier)
            bin_id, _, contig = identifier.partition("|")
            tokens.add(bin_id)
            if contig:
                tokens.add(contig.rsplit("_", 1)[0])
    tokens.discard("")
    return tokens


def _write_participant_package(
    dest: Path,
    assemblies: Sequence[BlindedAssembly],
    prompt_file: Path,
    research_question: str,
) -> None:
    prompt_dir = dest / "prompt"
    export_dir = dest / "exports" / "participant"
    prompt_dir.mkdir(parents=True)
    (export_dir / "files").mkdir(parents=True)

    (prompt_dir / "research_question.md").write_text(
        f"# Research Question\n\n{research_question}\n", encoding="utf-8"
    )
    shutil.copyfile(prompt_file, prompt_dir / "participant_prompt.md")

    counts = {a.alias: len(a.header_map) for a in assemblies}
    manifest_path = dest / "starting_data" / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(_starting_manifest(counts), sort_keys=False, width=88),
        encoding="utf-8",
    )
    package_manifest = {
        "case_id": PUBLIC_CASE_ID,
        "title": PUBLIC_TITLE,
        "research_question": research_question,
        "participant_visible_items": [_manifest_item(alias) for alias in counts],
        "exported_files": [
            "research_question.md",
            "participant_prompt.md",
            "starting_data_manifest.yaml",
            *(f"files/{alias}.fna" for alias in counts),
        ],
        "excluded_files": ["data/", "csag/", "scoring/"],
    }
    package_path = prompt_dir / "participant_package_manifest.json"
    package_path.write_text(
        json.dumps(package_manifest, indent=2) + "\n", encoding="utf-8"
    )

    for name in ("research_question.md", "participant_prompt.md"):
        shutil.copyfile(prompt_dir / name, export_dir / name)
    shutil.copyfile(package_path, export_dir / package_path.name)
    shutil.copyfile(manifest_path, export_dir / "starting_data_manifest.yaml")
    for alias in counts:
        shutil.copyfile(
            dest / "starting_data" / "files" / f"{alias}.fna",
            export_dir / "files" / f"{alias}.fna",
        )


def _starting_manifest(counts: dict[str, int]) -> dict[str, object]:
    """Build the starting_data manifest from sequence counts keyed by alias."""
    return {
        "case": PUBLIC_CASE_ID,
        "participant_visible": True,
        "description": (
            "Participant-visible starting data. The manuscript, ground truth and "
            "scoring assets are not participant-visible."
        ),
        "items": [_manifest_item(alias) for alias in counts],
        "files": [
            {
                "path": f"files/{alias}.fna",
                "type": "nucleotide_fasta",
                "description": ASSEMBLY_SUMMARY,
                "sequence_count": count,
                "participant_visible": True,
            }
            for alias, count in counts.items()
        ],
        "allowed_use": [
            "Use the FASTA files in this starting-data package.",
            (
                "Use public reference databases and literature needed to analyze "
                "the sequences."
            ),
        ],
        "exclusions": [
            "The source manuscript, ground-truth artifacts and scoring assets."
        ],
    }


def _manifest_item(alias: str) -> dict[str, object]:
    return {
        "name": alias,
        "role": "nucleotide FASTA assembly",
        "summary": ASSEMBLY_SUMMARY,
        "local_files": [f"files/{alias}.fna"],
        "participant_visible": True,
    }


def _write_private_map(
    dest: Path, source: Path, seed: str, assemblies: Sequence[BlindedAssembly]
) -> None:
    private_dir = dest / "private"
    private_dir.mkdir()
    blinding_map = {
        "private": True,
        "notice": PRIVATE_NOTICE,
        "seed": seed,
        "source_case": source.name,
        "line_wrapping": "unchanged from the source files",
        "assemblies": {
            a.alias: {
                "original_file": a.original_file,
                "original_sha256": a.original_sha256,
                "aliased_sha256": a.aliased_sha256,
                "sequence_count": len(a.header_map),
                "header_map": a.header_map,
            }
            for a in assemblies
        },
    }
    (private_dir / "blinding_map.json").write_text(
        json.dumps(blinding_map, indent=2) + "\n", encoding="utf-8"
    )


def _write_case_yaml(dest: Path, source: Path) -> None:
    root = dest.parent.parent
    reused = {
        name: {
            "path": os.path.relpath(source / relative, root),
            "sha256": _sha256(source / relative),
        }
        for name, relative in GROUND_TRUTH_FILES.items()
    }
    case = {
        "case": dest.name,
        "blinded_from": source.name,
        "description": (
            "Blinded copy of the source case. Ground truth and scoring are reused "
            "from the source case; evaluator-side de-blinding uses "
            "private/blinding_map.json."
        ),
        "reused_ground_truth": {"source_case": source.name, **reused},
    }
    (dest / "case.yaml").write_text(
        yaml.safe_dump(case, sort_keys=False, width=88), encoding="utf-8"
    )


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Source case directory")
    parser.add_argument("dest", type=Path, help="Destination case directory")
    parser.add_argument("--seed", required=True, help="Seed for the alias assignment")
    parser.add_argument(
        "--participant-prompt",
        type=Path,
        required=True,
        help="Prompt file, copied verbatim",
    )
    parser.add_argument("--research-question", required=True, help="Research question")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Build the blinded case and return the exit code."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    blind_case(
        args.source.resolve(),
        args.dest.resolve(),
        args.seed,
        args.participant_prompt,
        args.research_question.strip(),
    )
    logger.info("wrote blinded case %s", args.dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
