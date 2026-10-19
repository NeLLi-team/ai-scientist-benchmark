"""Tests for the benchmark-run leakage audit."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1] / "skills" / "benchmark-run" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))
import audit_leakage  # noqa: E402  # needs the scripts dir on sys.path first

ORIGINAL_ID = "IMGS3300050451"
PAPER_TITLE = "Giant Viruses With Remarkable Genomes"
TERMS = [ORIGINAL_ID, "Ga0529969", "RhikV", PAPER_TITLE]


class AuditLeakageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.terms_file = self.root / "terms.txt"
        self.terms_file.write_text(
            "# private terms\n\n" + "\n".join(TERMS) + "\n", encoding="utf-8"
        )
        self.scan = self.root / "scan"
        self.scan.mkdir()

    def run_main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = audit_leakage.main(["--terms-file", str(self.terms_file), *argv])
        return code, out.getvalue()

    def test_finds_a_term_case_insensitively_with_file_and_line(self) -> None:
        (self.scan / "notes.txt").write_text(
            "first line\nthe bin imgs3300050451 was used\n", encoding="utf-8"
        )

        hits = audit_leakage.audit([self.scan], TERMS).hits

        self.assertEqual(
            [(hit.path.name, hit.line) for hit in hits], [("notes.txt", 2)]
        )

    def test_finds_a_title_wrapped_across_lines(self) -> None:
        (self.scan / "draft.md").write_text(
            "see Giant Viruses\nWith Remarkable   Genomes for details\n",
            encoding="utf-8",
        )

        hits = audit_leakage.audit([self.scan], TERMS).hits

        self.assertEqual([(hit.path.name, hit.line) for hit in hits], [("draft.md", 1)])

    def test_finds_a_term_inside_a_json_string_with_escapes(self) -> None:
        event = {"type": "item", "text": "Giant Viruses\nWith Remarkable Genomes"}
        (self.scan / "transcript.jsonl").write_text(
            json.dumps({"type": "start"}) + "\n" + json.dumps(event) + "\n",
            encoding="utf-8",
        )

        hits = audit_leakage.audit([self.scan], TERMS).hits

        self.assertEqual([hit.line for hit in hits], [2])

    def test_finds_a_wrapped_title_in_pretty_printed_json(self) -> None:
        document = {"outer": {"text": "Giant Viruses\nWith Remarkable Genomes", "n": 1}}
        (self.scan / "report.json").write_text(
            json.dumps(document, indent=2), encoding="utf-8"
        )

        hits = audit_leakage.audit([self.scan], TERMS).hits

        self.assertEqual([hit.term_id for hit in hits], ["T04"])

    def test_pretty_printed_json_hit_reports_the_line_of_the_value(self) -> None:
        document = {"a": 1, "text": "see RhikV here"}
        (self.scan / "report.json").write_text(
            json.dumps(document, indent=2), encoding="utf-8"
        )

        hits = audit_leakage.audit([self.scan], TERMS).hits

        self.assertEqual([hit.line for hit in hits], [3])

    def test_json_file_that_is_not_one_document_is_still_read_by_line(self) -> None:
        (self.scan / "events.json").write_text(
            json.dumps({"type": "start"}) + "\n" + json.dumps({"text": "RhikV"}) + "\n",
            encoding="utf-8",
        )

        hits = audit_leakage.audit([self.scan], TERMS).hits

        self.assertEqual([hit.line for hit in hits], [2])

    def test_printed_report_redacts_a_path_that_contains_a_term(self) -> None:
        (self.scan / f"notes-{ORIGINAL_ID}.txt").write_text(
            f"{ORIGINAL_ID}\n", encoding="utf-8"
        )

        code, output = self.run_main(str(self.scan))

        self.assertEqual(code, 1)
        self.assertNotIn(ORIGINAL_ID.lower(), output.lower())
        self.assertIn("notes-[T01].txt:1", output)

    def test_json_report_redacts_a_path_that_contains_a_term(self) -> None:
        directory = self.scan / ORIGINAL_ID
        directory.mkdir()
        (directory / "a.txt").write_text("RhikV\n", encoding="utf-8")
        report = self.root / "report.json"

        self.run_main(str(self.scan), "--json-out", str(report))

        text = report.read_text(encoding="utf-8")
        self.assertNotIn(ORIGINAL_ID, text)
        self.assertIn("[T01]/a.txt", json.loads(text)["hits"][0]["path"])

    def test_clean_files_give_no_hits(self) -> None:
        (self.scan / "clean.txt").write_text(
            "assembly_A contigs only\n", encoding="utf-8"
        )

        self.assertEqual(audit_leakage.audit([self.scan], TERMS).hits, [])

    def test_hits_carry_term_ids_not_terms(self) -> None:
        (self.scan / "a.txt").write_text("RhikV and Ga0529969\n", encoding="utf-8")

        hits = audit_leakage.audit([self.scan], TERMS).hits

        self.assertEqual(sorted(hit.term_id for hit in hits), ["T02", "T03"])

    def test_printed_report_never_contains_a_term(self) -> None:
        (self.scan / "a.txt").write_text(
            f"context before {ORIGINAL_ID} context after\nRhikV\n", encoding="utf-8"
        )

        code, output = self.run_main(str(self.scan))

        self.assertEqual(code, 1)
        for term in TERMS:
            with self.subTest(term=term):
                self.assertNotIn(term.lower(), output.lower())
        self.assertIn("a.txt:1", output)
        self.assertIn("context before [T01] context after", output)

    def test_json_report_never_contains_a_term(self) -> None:
        (self.scan / "a.txt").write_text(f"{ORIGINAL_ID}\n", encoding="utf-8")
        report = self.root / "report.json"

        self.run_main(str(self.scan), "--json-out", str(report))

        text = report.read_text(encoding="utf-8")
        self.assertNotIn(ORIGINAL_ID, text)
        self.assertEqual(json.loads(text)["hits"][0]["term_id"], "T01")

    def test_exit_code_is_zero_when_clean(self) -> None:
        (self.scan / "a.txt").write_text("nothing here\n", encoding="utf-8")

        code, _ = self.run_main(str(self.scan))

        self.assertEqual(code, 0)

    def test_terms_file_inside_the_scanned_tree_is_skipped(self) -> None:
        inside = self.scan / "terms.txt"
        inside.write_text("\n".join(TERMS), encoding="utf-8")
        self.terms_file = inside

        code, _ = self.run_main(str(self.scan))

        self.assertEqual(code, 0)

    def test_binary_and_oversized_files_are_skipped_and_counted(self) -> None:
        (self.scan / "blob.bin").write_bytes(b"\x00\x01" + ORIGINAL_ID.encode())
        (self.scan / "big.txt").write_text(ORIGINAL_ID * 10, encoding="utf-8")

        result = audit_leakage.audit([self.scan], TERMS, max_file_bytes=20)

        self.assertEqual(result.hits, [])
        self.assertEqual(result.skipped_binary, 1)
        self.assertEqual(result.skipped_large, 1)

    def test_environment_directories_are_pruned(self) -> None:
        env_dir = self.scan / ".pixi" / "envs"
        env_dir.mkdir(parents=True)
        (env_dir / "pkg.txt").write_text(ORIGINAL_ID, encoding="utf-8")

        self.assertEqual(audit_leakage.audit([self.scan], TERMS).hits, [])

    def test_run_dir_shortcut_scans_transcript_stderr_work_and_home(self) -> None:
        run_dir = self.root / "run"
        for name in ("work", "home", "input"):
            (run_dir / name).mkdir(parents=True)
        (run_dir / "transcript.jsonl").write_text(
            json.dumps({"t": "Ga0529969"}) + "\n", encoding="utf-8"
        )
        (run_dir / "stderr.log").write_text("RhikV\n", encoding="utf-8")
        (run_dir / "work" / "m.md").write_text(ORIGINAL_ID, encoding="utf-8")
        (run_dir / "home" / "history.txt").write_text(PAPER_TITLE, encoding="utf-8")
        (run_dir / "input" / "x.fna").write_text(ORIGINAL_ID, encoding="utf-8")

        code, output = self.run_main("--run-dir", str(run_dir))

        self.assertEqual(code, 1)
        self.assertEqual(output.count("\n  "), 4)

    def test_missing_terms_file_is_an_error(self) -> None:
        self.terms_file = self.root / "absent.txt"

        code, _ = self.run_main(str(self.scan))

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
