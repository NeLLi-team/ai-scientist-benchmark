#!/usr/bin/env python3
"""Copy a reference database while dropping records of named genomes.

Blinded benchmark runs mount a filtered copy of a local reference database so that
participant agents cannot look up the benchmark assemblies under their original IDs.
FASTA records are dropped by header, TSV rows by their first column. A genome ID
matches only as a whole token, so `BIN25` does not match `BIN250`.
"""

from __future__ import annotations

import argparse
import logging
import re
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_CHARS = "A-Za-z0-9"  # noqa: S105  # character class, not a password


def filter_database(
    source_dir: Path,
    dest_dir: Path,
    genome_ids: Sequence[str],
    *,
    fasta_files: Iterable[str],
    tsv_files: Iterable[str],
) -> dict[str, int]:
    """Filter the named files under `source_dir` into `dest_dir`.

    Returns the number of records removed per file, keyed by relative path. The
    source tree is only read: `dest_dir` must be new or empty and must not lie
    inside it, and no destination file may resolve into it.
    """
    source_dir = source_dir.resolve()
    dest_dir = _prepare_destination(dest_dir, source_dir)
    pattern = _id_pattern(genome_ids)
    removed: dict[str, int] = {}
    for names, is_fasta in ((fasta_files, True), (tsv_files, False)):
        for name in names:
            _check_relative(name)
            dest = _make_destination_path(dest_dir, name, source_dir)
            removed[name] = _filter_lines(
                source_dir / name, dest, pattern, is_fasta=is_fasta
            )
    return removed


def filter_fasta(source: Path, dest: Path, genome_ids: Sequence[str]) -> int:
    """Copy a FASTA file without records whose header contains a genome ID."""
    return _filter_lines(source, dest, _id_pattern(genome_ids), is_fasta=True)


def filter_tsv(source: Path, dest: Path, genome_ids: Sequence[str]) -> int:
    """Copy a TSV file without rows whose first column contains a genome ID."""
    return _filter_lines(source, dest, _id_pattern(genome_ids), is_fasta=False)


def _id_pattern(genome_ids: Sequence[str]) -> re.Pattern[bytes]:
    alternatives = "|".join(re.escape(genome_id) for genome_id in genome_ids)
    pattern = rf"(?<![{TOKEN_CHARS}])(?:{alternatives})(?![{TOKEN_CHARS}])"
    return re.compile(pattern.encode())


def _is_inside(path: Path, tree: Path) -> bool:
    return path == tree or tree in path.parents


def _prepare_destination(dest_dir: Path, source_dir: Path) -> Path:
    """Return the resolved destination, created if new, refusing unsafe ones."""
    resolved = dest_dir.resolve()
    if _is_inside(resolved, source_dir):
        raise ValueError(f"destination {dest_dir} lies inside the source {source_dir}")
    if not resolved.exists():
        resolved.mkdir(parents=True)
    elif not resolved.is_dir() or any(resolved.iterdir()):
        raise ValueError(f"destination must be a new or empty directory: {dest_dir}")
    return resolved


def _make_destination_path(dest_dir: Path, name: str, source_dir: Path) -> Path:
    """Create the destination subdirectories without following symlinks."""
    directory = dest_dir
    for part in Path(name).parent.parts:
        directory = directory / part
        if directory.is_symlink():
            raise ValueError(f"destination directory is a symlink: {directory}")
        directory.mkdir(exist_ok=True)
    if _is_inside(directory.resolve(), source_dir):
        raise ValueError(f"destination {directory} resolves into the source")
    return directory / Path(name).name


def _check_relative(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"file name must be relative to the database dir: {name!r}")


def _filter_lines(
    source: Path, dest: Path, pattern: re.Pattern[bytes], *, is_fasta: bool
) -> int:
    """Stream `source` to a temporary file, rename it to `dest`, return drops."""
    if dest.exists() and dest.samefile(source):
        raise ValueError(f"destination is the same file as the source: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    removed = 0
    keep = True
    with (
        source.open("rb") as reader,
        tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as writer,
    ):
        try:
            for line in reader:
                if is_fasta:
                    if line.startswith(b">"):
                        keep = pattern.search(line) is None
                        removed += not keep
                else:
                    keep = pattern.search(line.split(b"\t", 1)[0]) is None
                    removed += not keep
                if keep:
                    writer.write(line)
        except BaseException:
            Path(writer.name).unlink(missing_ok=True)
            raise
    Path(writer.name).chmod(0o644)
    Path(writer.name).replace(dest)
    logger.info("%s: removed %d records", source.name, removed)
    return removed


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="Database directory to read")
    parser.add_argument("dest_dir", type=Path, help="Directory for the filtered copy")
    parser.add_argument("--drop", nargs="+", required=True, help="Genome IDs to drop")
    parser.add_argument("--fasta", nargs="+", default=[], help="FASTA files, relative")
    parser.add_argument("--tsv", nargs="+", default=[], help="TSV files, relative")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the filter and print removed-record counts per file."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    counts = filter_database(
        args.source_dir,
        args.dest_dir,
        args.drop,
        fasta_files=args.fasta,
        tsv_files=args.tsv,
    )
    for name, removed in counts.items():
        print(f"{name}\t{removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
