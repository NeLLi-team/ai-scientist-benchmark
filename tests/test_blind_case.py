"""Tests for the blind_case helper that aliases participant-visible FASTA files."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "benchmark-prompt-package" / "scripts" / "blind_case.py"
PARTICIPANT_PROMPT = "# Participant Prompt\n\nInputs: /benchmark/input/assembly_A.fna\n"
QUESTION = "What can be inferred from assembly_A, assembly_B, and assembly_C?"
SEED = "test-seed"
BINS = ("IMGS0000000001_BIN1", "IMGS0000000002_BIN2", "IMGS0000000003_BIN3")
LEAK_STRINGS = ("IMG", "IMGS", "Ga05299", "_BIN", "img.jgi")


def load_module():
    spec = importlib.util.spec_from_file_location("blind_case", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


def write_source_case(root: Path) -> Path:
    """Create a miniature source case with three FASTA bins and ground truth."""
    case = root / "ground_truth" / "src"
    (case / "starting_data" / "files").mkdir(parents=True)
    (case / "csag").mkdir()
    (case / "scoring").mkdir()
    (case / "csag" / "paper_extraction.json").write_text('{"a": 1}\n', encoding="utf-8")
    (case / "scoring" / "scoring_schema.json").write_text(
        '{"b": 2}\n', encoding="utf-8"
    )
    for index, name in enumerate(BINS, start=1):
        lines = []
        for contig in range(1, 4 + index):
            lines.append(f">{name}|Ga0529969_{contig:05d}")
            lines.append("ACGT" * (10 + contig))
            lines.append("TTGCA")
        (case / "starting_data" / "files" / f"{name}.fna").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    return case


def read_records(path: Path) -> list[tuple[str, str]]:
    records = []
    for block in path.read_text(encoding="utf-8").split(">")[1:]:
        header, *sequence_lines = block.splitlines()
        records.append((header, "".join(sequence_lines)))
    return records


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BlindCaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = write_source_case(self.root)
        self.module = load_module()
        self.prompt_file = self.root / "participant_prompt.md"
        self.prompt_file.write_text(PARTICIPANT_PROMPT, encoding="utf-8")

    def build(self, name: str = "dest", seed: str = SEED) -> Path:
        dest = self.root / "ground_truth" / name
        self.module.blind_case(self.source, dest, seed, self.prompt_file, QUESTION)
        return dest

    def private_map(self, dest: Path) -> dict:
        return json.loads((dest / "private" / "blinding_map.json").read_text("utf-8"))

    def test_blind_case_is_deterministic_for_a_seed(self) -> None:
        first = self.private_map(self.build("one"))
        second = self.private_map(self.build("two"))

        self.assertEqual(first["assemblies"], second["assemblies"])

    def test_blind_case_assignment_depends_on_seed(self) -> None:
        orders = {
            tuple(
                entry["original_file"]
                for entry in self.private_map(self.build(f"d{i}", f"seed-{i}"))[
                    "assemblies"
                ].values()
            )
            for i in range(12)
        }

        self.assertGreater(len(orders), 1)

    def test_header_map_is_complete_and_unique(self) -> None:
        dest = self.build()
        mapping = self.private_map(dest)

        for alias, entry in mapping["assemblies"].items():
            aliased = read_records(dest / "starting_data" / "files" / f"{alias}.fna")
            original = read_records(
                self.source / "starting_data" / "files" / entry["original_file"]
            )
            header_map = entry["header_map"]
            self.assertEqual([h for h, _ in aliased], list(header_map))
            self.assertEqual([h for h, _ in original], list(header_map.values()))
            self.assertEqual(len(set(header_map)), len(header_map))
            self.assertEqual(len(set(header_map.values())), len(header_map))
            self.assertEqual(next(iter(header_map)), f"{alias}_contig_0001")

    def test_sequences_are_unchanged(self) -> None:
        dest = self.build()
        mapping = self.private_map(dest)

        for alias, entry in mapping["assemblies"].items():
            aliased = read_records(dest / "starting_data" / "files" / f"{alias}.fna")
            original = read_records(
                self.source / "starting_data" / "files" / entry["original_file"]
            )
            self.assertEqual([s for _, s in aliased], [s for _, s in original])

    def test_hashes_match_files(self) -> None:
        dest = self.build()
        mapping = self.private_map(dest)

        for alias, entry in mapping["assemblies"].items():
            self.assertEqual(
                entry["aliased_sha256"],
                sha256(dest / "starting_data" / "files" / f"{alias}.fna"),
            )
            self.assertEqual(
                entry["original_sha256"],
                sha256(
                    self.source / "starting_data" / "files" / entry["original_file"]
                ),
            )
        self.assertTrue(mapping["private"])
        self.assertEqual(mapping["seed"], SEED)

    def test_participant_visible_files_contain_no_identifying_strings(self) -> None:
        dest = self.build()

        for sub in ("exports/participant", "starting_data", "prompt"):
            for path in (dest / sub).rglob("*"):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8")
                for leak in (*LEAK_STRINGS, *BINS):
                    self.assertNotIn(leak, text, f"{leak!r} leaked in {path}")

    def test_export_contains_only_participant_files(self) -> None:
        dest = self.build()

        exported = sorted(
            str(p.relative_to(dest / "exports" / "participant"))
            for p in (dest / "exports" / "participant").rglob("*")
            if p.is_file()
        )

        self.assertEqual(
            exported,
            [
                "files/assembly_A.fna",
                "files/assembly_B.fna",
                "files/assembly_C.fna",
                "participant_package_manifest.json",
                "participant_prompt.md",
                "research_question.md",
                "starting_data_manifest.yaml",
            ],
        )

    def test_prompt_is_copied_verbatim(self) -> None:
        dest = self.build()

        self.assertEqual(
            (dest / "prompt" / "participant_prompt.md").read_text("utf-8"),
            PARTICIPANT_PROMPT,
        )
        self.assertEqual(
            (dest / "exports" / "participant" / "participant_prompt.md").read_text(
                "utf-8"
            ),
            PARTICIPANT_PROMPT,
        )
        self.assertEqual(
            (dest / "prompt" / "research_question.md").read_text("utf-8"),
            f"# Research Question\n\n{QUESTION}\n",
        )

    def test_manifest_lists_aliased_files_with_counts(self) -> None:
        dest = self.build()

        manifest = yaml.safe_load(
            (dest / "starting_data" / "manifest.yaml").read_text("utf-8")
        )

        self.assertTrue(manifest["participant_visible"])
        self.assertEqual(
            [item["local_files"] for item in manifest["items"]],
            [
                ["files/assembly_A.fna"],
                ["files/assembly_B.fna"],
                ["files/assembly_C.fna"],
            ],
        )
        counts = [entry["sequence_count"] for entry in manifest["files"]]
        self.assertEqual(
            counts,
            [
                len(
                    read_records(dest / "starting_data" / "files" / f"assembly_{x}.fna")
                )
                for x in "ABC"
            ],
        )

    def test_case_yaml_records_reused_ground_truth(self) -> None:
        dest = self.build()

        case = yaml.safe_load((dest / "case.yaml").read_text("utf-8"))

        reused = case["reused_ground_truth"]
        self.assertEqual(
            reused["csag"]["path"], "ground_truth/src/csag/paper_extraction.json"
        )
        self.assertEqual(
            reused["csag"]["sha256"],
            sha256(self.source / "csag" / "paper_extraction.json"),
        )
        self.assertEqual(
            reused["scoring_schema"]["sha256"],
            sha256(self.source / "scoring" / "scoring_schema.json"),
        )

    def test_blind_case_refuses_existing_destination(self) -> None:
        dest = self.build()

        with self.assertRaises(FileExistsError):
            self.module.blind_case(self.source, dest, SEED, self.prompt_file, QUESTION)

    def test_leak_check_raises_when_original_token_is_present(self) -> None:
        directory = self.root / "scan"
        directory.mkdir()
        (directory / "x.txt").write_text("see IMGS0000000001_BIN1", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.module.assert_no_leaks([directory], ["IMGS0000000001_BIN1"])

    def test_blind_case_rejects_bare_header_id_of_a_described_record(self) -> None:
        (self.source / "starting_data" / "files" / "extra.fna").write_text(
            ">PRIVATE_CONTIG_1234 original descriptive metadata\nACGT\n",
            encoding="utf-8",
        )
        self.prompt_file.write_text(
            "Look at PRIVATE_CONTIG_1234 first.\n", encoding="utf-8"
        )

        with self.assertRaises(ValueError):
            self.build()

    def test_blind_case_rejects_lowercase_variant_of_original_id(self) -> None:
        self.prompt_file.write_text(
            f"Look at {BINS[0].lower()} first.\n", encoding="utf-8"
        )

        with self.assertRaises(ValueError):
            self.build()

    def test_leak_check_is_case_insensitive(self) -> None:
        directory = self.root / "scan"
        directory.mkdir()
        (directory / "x.txt").write_text("see private_contig_1234", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.module.assert_no_leaks([directory], ["PRIVATE_CONTIG_1234"])


if __name__ == "__main__":
    unittest.main()
