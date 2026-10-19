"""Tests for the filter_reference_db helper."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "skills"
    / "benchmark-prompt-package"
    / "scripts"
    / "filter_reference_db.py"
)
DROP = ["IMGS1_BIN25"]


def load_module():
    spec = importlib.util.spec_from_file_location("filter_reference_db", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


class FilterReferenceDbTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.module = load_module()

    def test_filter_fasta_drops_exact_bin_token_only(self) -> None:
        source = self.root / "in.fna"
        source.write_text(
            ">NCLDV__IMGS1_BIN25|Ga1_1\nACGT\nAC\n"
            ">NCLDV__IMGS1_BIN250|Ga1_2\nGGGG\n"
            ">NCLDV__IMGS1_BIN2|Ga1_3\nTTTT\n"
            ">NCLDV__IMGS1_BIN25|Ga1_4\nCCCC\n",
            encoding="utf-8",
        )
        dest = self.root / "out.fna"

        removed = self.module.filter_fasta(source, dest, DROP)

        self.assertEqual(removed, 2)
        self.assertEqual(
            dest.read_text("utf-8"),
            ">NCLDV__IMGS1_BIN250|Ga1_2\nGGGG\n>NCLDV__IMGS1_BIN2|Ga1_3\nTTTT\n",
        )

    def test_filter_tsv_drops_rows_by_first_column_and_keeps_header(self) -> None:
        source = self.root / "in.tsv"
        source.write_text(
            "genome\tgroup\n"
            "IMGS1_BIN25\tNCLDV\n"
            "IMGS1_BIN2\tNCLDV\n"
            "NCLDV__IMGS1_BIN25|Ga1_1\tNCLDV\n"
            "other\tmentions IMGS1_BIN25 in a later column\n",
            encoding="utf-8",
        )
        dest = self.root / "out.tsv"

        removed = self.module.filter_tsv(source, dest, DROP)

        self.assertEqual(removed, 2)
        self.assertEqual(
            dest.read_text("utf-8"),
            "genome\tgroup\nIMGS1_BIN2\tNCLDV\n"
            "other\tmentions IMGS1_BIN25 in a later column\n",
        )

    def test_filter_database_keeps_layout_and_reports_counts(self) -> None:
        source = self.root / "db"
        (source / "old").mkdir(parents=True)
        (source / "a.fna").write_text(
            ">IMGS1_BIN25|x\nAC\n>keep\nGT\n", encoding="utf-8"
        )
        (source / "old" / "t.tsv").write_text("h\nIMGS1_BIN25\nz\n", encoding="utf-8")
        dest = self.root / "filtered"

        counts = self.module.filter_database(
            source, dest, DROP, fasta_files=["a.fna"], tsv_files=["old/t.tsv"]
        )

        self.assertEqual(counts, {"a.fna": 1, "old/t.tsv": 1})
        self.assertEqual((dest / "a.fna").read_text("utf-8"), ">keep\nGT\n")
        self.assertEqual((dest / "old" / "t.tsv").read_text("utf-8"), "h\nz\n")

    def test_filter_fasta_refuses_to_overwrite_its_source(self) -> None:
        source = self.root / "in.fna"
        content = ">IMGS1_BIN25|x\nAC\n>keep\nGT\n"
        source.write_text(content, encoding="utf-8")

        with self.assertRaises(ValueError):
            self.module.filter_fasta(source, source, DROP)

        self.assertEqual(source.read_text("utf-8"), content)

    def test_filter_fasta_refuses_destination_that_is_a_link_to_source(self) -> None:
        source = self.root / "in.fna"
        content = ">IMGS1_BIN25|x\nAC\n"
        source.write_text(content, encoding="utf-8")
        link = self.root / "link.fna"
        link.symlink_to(source)

        with self.assertRaises(ValueError):
            self.module.filter_fasta(source, link, DROP)

        self.assertEqual(source.read_text("utf-8"), content)

    def test_filter_database_refuses_same_source_and_destination_directory(
        self,
    ) -> None:
        source = self.root / "db"
        source.mkdir()
        content = ">IMGS1_BIN25|x\nAC\n"
        (source / "a.fna").write_text(content, encoding="utf-8")

        with self.assertRaises(ValueError):
            self.module.filter_database(
                source, source, DROP, fasta_files=["a.fna"], tsv_files=[]
            )

        self.assertEqual((source / "a.fna").read_text("utf-8"), content)

    def test_filter_database_refuses_absolute_file_names(self) -> None:
        source = self.root / "db"
        source.mkdir()
        content = ">IMGS1_BIN25|x\nAC\n"
        (source / "a.fna").write_text(content, encoding="utf-8")

        with self.assertRaises(ValueError):
            self.module.filter_database(
                source,
                self.root / "out",
                DROP,
                fasta_files=[str(source / "a.fna")],
                tsv_files=[],
            )

        self.assertEqual((source / "a.fna").read_text("utf-8"), content)

    def test_filter_database_refuses_names_that_leave_the_directory(self) -> None:
        source = self.root / "db"
        source.mkdir()
        (self.root / "outside.fna").write_text(">x\nAC\n", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.module.filter_database(
                source,
                self.root / "out",
                DROP,
                fasta_files=["../outside.fna"],
                tsv_files=[],
            )

    def test_filter_fasta_leaves_no_temporary_file_behind(self) -> None:
        source = self.root / "in.fna"
        source.write_text(">keep\nAC\n", encoding="utf-8")
        dest_dir = self.root / "out"

        self.module.filter_fasta(source, dest_dir / "out.fna", DROP)

        self.assertEqual([p.name for p in dest_dir.iterdir()], ["out.fna"])

    def _database_with_nested_source(self) -> tuple[Path, str]:
        source = self.root / "src"
        (source / "b").mkdir(parents=True)
        content = ">IMGS1_BIN25|x\nAC\n>keep\nGT\n"
        (source / "b" / "seq.fna").write_text(content, encoding="utf-8")
        (source / "a").mkdir()
        (source / "a" / "seq.fna").write_text(content, encoding="utf-8")
        return source, content

    def _run(self, source: Path, dest: Path) -> None:
        self.module.filter_database(
            source, dest, DROP, fasta_files=["a/seq.fna"], tsv_files=[]
        )

    def test_filter_database_refuses_destination_symlink_into_source(self) -> None:
        source, content = self._database_with_nested_source()
        dest = self.root / "dest"
        dest.mkdir()
        (dest / "a").symlink_to(source / "b")

        with self.assertRaises(ValueError):
            self._run(source, dest)

        self.assertEqual((source / "b" / "seq.fna").read_text("utf-8"), content)

    def test_filter_database_refuses_destination_that_links_into_source(self) -> None:
        source, content = self._database_with_nested_source()
        dest = self.root / "dest"
        dest.symlink_to(source / "b")

        with self.assertRaises(ValueError):
            self._run(source, dest)

        self.assertEqual((source / "b" / "seq.fna").read_text("utf-8"), content)

    def test_filter_database_refuses_destination_inside_source(self) -> None:
        source, content = self._database_with_nested_source()

        with self.assertRaises(ValueError):
            self._run(source, source / "b")

        self.assertEqual((source / "b" / "seq.fna").read_text("utf-8"), content)

    def test_filter_database_refuses_non_empty_destination(self) -> None:
        source, _ = self._database_with_nested_source()
        dest = self.root / "dest"
        dest.mkdir()
        (dest / "old.txt").write_text("x", encoding="utf-8")

        with self.assertRaises(ValueError):
            self._run(source, dest)

    def test_filter_database_accepts_empty_existing_destination(self) -> None:
        source, _ = self._database_with_nested_source()
        dest = self.root / "dest"
        dest.mkdir()

        self._run(source, dest)

        self.assertEqual((dest / "a" / "seq.fna").read_text("utf-8"), ">keep\nGT\n")


if __name__ == "__main__":
    unittest.main()
