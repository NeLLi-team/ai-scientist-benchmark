"""Tests for credential copying, safe cleanup and exposure scanning."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1] / "skills" / "benchmark-run" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))
import run_credentials  # noqa: E402  # needs the scripts dir on sys.path first

SECRET = "SENTINEL-ORIGINAL-ACCESS-TOKEN-0123456789abcdef"  # noqa: S105  # fake test value
REFRESHED = "SENTINEL-REFRESHED-ACCESS-TOKEN-9876543210zyxwvu"  # fake test value
RELPATH = ".codex/auth.json"


class CredentialTestCase(unittest.TestCase):
    """Builds a run home with one credentials copy and a host-only canary."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = self.root / "host_auth.json"
        self.source.write_text(
            json.dumps({"tokens": {"access": SECRET}}), encoding="utf-8"
        )
        self.run_dir = self.root / "run"
        self.home = self.run_dir / "home"
        self.work = self.run_dir / "work"
        self.home.mkdir(parents=True)
        self.work.mkdir()
        self.outside = self.root / "host_dir"
        self.outside.mkdir()
        self.canary = self.outside / "auth.json"
        self.canary.write_text("host-only credentials", encoding="utf-8")
        self.copy = run_credentials.copy_credentials(self.source, self.home, RELPATH)
        self.addCleanup(self.make_removable)

    def make_removable(self) -> None:
        """Restore permissions so the temporary tree can be deleted."""
        for path in (
            self.home,
            self.home / ".codex",
            self.home / ".codex.saved",
        ):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)

    def scrub(self) -> run_credentials.ScrubReport:
        return self.copy.scrub([self.home, self.work])

    def found_copies(self) -> list[Path]:
        """Every regular file under the run dir that holds the original bytes."""
        return [
            path
            for path in self.run_dir.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.read_bytes() == self.source.read_bytes()
        ]


class CopyCredentialsTest(CredentialTestCase):
    def test_copy_has_mode_0600_and_the_source_bytes(self) -> None:
        self.assertEqual(self.copy.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.copy.path.read_bytes(), self.source.read_bytes())

    def test_copy_repr_hides_digest_and_secret_values(self) -> None:
        text = repr(self.copy)

        self.assertNotIn(SECRET, text)
        self.assertNotIn(self.copy.digest, text)


class SecretValuesTest(unittest.TestCase):
    def test_secret_values_keeps_only_long_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / "auth.json"
            credentials.write_text(
                json.dumps({"tokens": {"access": SECRET, "kind": "oauth"}}),
                encoding="utf-8",
            )

            self.assertEqual(run_credentials.secret_values(credentials), {SECRET})

    def test_secret_values_of_a_non_json_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / "auth.json"
            credentials.write_text("not json " + SECRET, encoding="utf-8")

            self.assertEqual(run_credentials.secret_values(credentials), frozenset())


class ScrubTest(CredentialTestCase):
    def test_scrub_deletes_the_copy_and_reports_it_unchanged(self) -> None:
        report = self.scrub()

        self.assertFalse(self.copy.path.exists())
        self.assertFalse(report.changed)
        self.assertFalse(report.left)

    def test_scrub_does_not_follow_a_symlinked_credentials_directory(self) -> None:
        (self.home / ".codex").rename(self.home / ".codex.saved")
        (self.home / ".codex").symlink_to(self.outside, target_is_directory=True)

        report = self.scrub()

        self.assertTrue(self.canary.exists())
        self.assertTrue(any("symlink" in error for error in report.errors))

    def test_scrub_removes_the_copy_renamed_behind_a_symlink(self) -> None:
        (self.home / ".codex").rename(self.home / ".codex.saved")
        (self.home / ".codex").symlink_to(self.outside, target_is_directory=True)

        report = self.scrub()

        self.assertEqual(self.found_copies(), [])
        self.assertFalse(report.left)

    def test_scrub_removes_a_renamed_copy_by_content(self) -> None:
        elsewhere = self.home / "notes" / "backup.txt"
        elsewhere.parent.mkdir()
        self.copy.path.rename(elsewhere)

        report = self.scrub()

        self.assertFalse(elsewhere.exists())
        self.assertEqual(report.copies_removed, 1)

    def test_scrub_removes_a_copy_made_in_the_work_directory(self) -> None:
        (self.work / "stolen.txt").write_bytes(self.source.read_bytes())

        self.scrub()

        self.assertEqual(self.found_copies(), [])

    def test_scrub_leaves_unrelated_files_alone(self) -> None:
        other = self.home / "history.txt"
        other.write_text("keep me", encoding="utf-8")

        self.scrub()

        self.assertTrue(other.exists())

    def test_scrub_tolerates_a_file_with_mode_000(self) -> None:
        self.copy.path.chmod(0)

        report = self.scrub()

        self.assertFalse(self.copy.path.exists())
        self.assertFalse(report.left)

    def test_scrub_tolerates_a_directory_with_mode_000(self) -> None:
        (self.home / ".codex").chmod(0)

        report = self.scrub()

        self.assertFalse(self.copy.path.exists())
        self.assertFalse(report.left)

    def test_scrub_tolerates_a_renamed_copy_inside_a_mode_000_directory(self) -> None:
        hidden = self.home / "locked"
        hidden.mkdir()
        self.copy.path.rename(hidden / "x.bak")
        hidden.chmod(0)

        report = self.scrub()

        self.assertEqual(self.found_copies(), [])
        self.assertFalse(report.left)

    def test_scrub_reports_a_changed_file_and_its_new_secret_values(self) -> None:
        self.copy.path.write_text(
            json.dumps({"tokens": {"access": REFRESHED}}), encoding="utf-8"
        )

        report = self.scrub()

        self.assertTrue(report.changed)
        self.assertEqual(report.post_secrets, {REFRESHED})
        self.assertFalse(self.copy.path.exists())

    def test_scrub_removes_a_copy_of_the_refreshed_credentials(self) -> None:
        refreshed = json.dumps({"tokens": {"access": REFRESHED}})
        self.copy.path.write_text(refreshed, encoding="utf-8")
        (self.work / "copy.txt").write_text(refreshed, encoding="utf-8")

        self.scrub()

        self.assertFalse((self.work / "copy.txt").exists())

    def test_scrub_records_an_error_instead_of_raising_when_the_path_is_a_directory(
        self,
    ) -> None:
        self.copy.path.unlink()
        self.copy.path.mkdir()

        report = self.scrub()

        self.assertTrue(report.errors)

    def test_scrub_ignores_a_missing_file_and_reports_it_changed(self) -> None:
        self.copy.path.unlink()

        report = self.scrub()

        self.assertTrue(report.changed)
        self.assertFalse(report.left)

    def test_scrub_deletes_an_oversized_replacement_without_reading_it(self) -> None:
        with self.copy.path.open("r+b") as handle:
            handle.truncate(run_credentials.MAX_CREDENTIAL_BYTES + 1)

        report = self.scrub()

        self.assertFalse(self.copy.path.exists())
        self.assertTrue(report.changed)
        self.assertFalse(report.left)
        self.assertTrue(any("larger than" in error for error in report.errors))
        self.assertEqual(report.post_secrets, frozenset())

    def test_scrub_of_a_256_mib_sparse_replacement_stays_in_a_small_memory_limit(
        self,
    ) -> None:
        script = textwrap.dedent(
            """
            import resource, sys
            from pathlib import Path
            sys.path.insert(0, sys.argv[1])
            import run_credentials
            source, home = Path(sys.argv[2]), Path(sys.argv[3])
            copy = run_credentials.copy_credentials(source, home, ".codex/auth.json")
            with copy.path.open("r+b") as handle:
                handle.truncate(256 * 1024 * 1024)
            vm_kib = int(next(
                line.split()[1] for line in open("/proc/self/status")
                if line.startswith("VmSize")
            ))
            limit = vm_kib * 1024 + 128 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            report = copy.scrub([home])
            print("left" if report.left else "clean", copy.path.exists())
            """
        )

        done = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(SCRIPTS_DIR),
                str(self.source),
                str(self.home / "h2"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        self.assertEqual(done.stdout.split(), ["clean", "False"])

    def test_scrub_deletes_the_original_even_when_reading_it_fails(self) -> None:
        with mock.patch.object(
            run_credentials, "_read_credential", side_effect=MemoryError
        ):
            report = self.scrub()

        self.assertFalse(self.copy.path.exists())
        self.assertTrue(any("MemoryError" in error for error in report.errors))

    def test_scrub_finds_copies_when_hashes_are_streamed_in_tiny_chunks(self) -> None:
        (self.work / "stolen.txt").write_bytes(self.source.read_bytes())

        with mock.patch.object(run_credentials, "HASH_CHUNK_BYTES", 4):
            self.scrub()

        self.assertEqual(self.found_copies(), [])

    def test_scrub_removes_a_refreshed_credential_when_home_has_mode_000(self) -> None:
        refreshed = json.dumps({"tokens": {"access": REFRESHED}})
        self.copy.path.write_text(refreshed, encoding="utf-8")
        self.home.chmod(0)

        report = self.scrub()

        self.assertFalse(self.copy.path.exists())
        self.assertTrue(report.changed)
        self.assertEqual(report.post_secrets, {REFRESHED})
        self.assertFalse(report.left)

    def test_scrub_repairs_nested_mode_000_directories_before_the_final_read(
        self,
    ) -> None:
        refreshed = json.dumps({"tokens": {"access": REFRESHED}})
        self.copy.path.write_text(refreshed, encoding="utf-8")
        (self.home / ".codex").chmod(0)
        self.home.chmod(0)

        report = self.scrub()

        self.assertEqual(report.post_secrets, {REFRESHED})
        self.assertFalse(self.copy.path.exists())

    def test_scrub_reports_left_when_permissions_cannot_be_restored(self) -> None:
        self.home.chmod(0)

        with mock.patch.object(Path, "chmod", side_effect=PermissionError):
            report = self.scrub()

        self.assertTrue(report.left)

    def test_scrub_does_not_follow_a_symlinked_search_root(self) -> None:
        secret_file = self.outside / "copy.txt"
        secret_file.write_bytes(self.source.read_bytes())
        secret_file.chmod(0)
        link = self.run_dir / "linked_root"
        link.symlink_to(self.outside, target_is_directory=True)

        self.copy.scrub([self.home, link])

        self.assertTrue(secret_file.exists())
        self.assertEqual(secret_file.stat().st_mode & 0o777, 0)

    def test_repair_permissions_never_chmods_through_a_symlink(self) -> None:
        locked = self.outside / "locked.txt"
        locked.write_text("x", encoding="utf-8")
        locked.chmod(0)
        link = self.run_dir / "linked_root"
        link.symlink_to(self.outside, target_is_directory=True)

        run_credentials.repair_permissions(link, [])

        self.assertEqual(locked.stat().st_mode & 0o777, 0)

    def test_repair_permissions_never_chmods_a_symlinked_entry(self) -> None:
        locked = self.outside / "locked_dir"
        locked.mkdir()
        locked.chmod(0)
        (self.home / "entry").symlink_to(locked, target_is_directory=True)

        run_credentials.repair_permissions(self.home, [])

        self.assertEqual(locked.stat().st_mode & 0o777, 0)
        locked.chmod(0o700)

    def test_scrub_errors_never_contain_a_credential_value_from_a_file_name(
        self,
    ) -> None:
        named = self.work / f"{SECRET}.txt"
        named.write_bytes(self.source.read_bytes())
        named.chmod(0)

        with mock.patch.object(Path, "chmod", side_effect=PermissionError):
            report = self.scrub()

        self.assertTrue(report.errors)
        self.assertNotIn(SECRET, " ".join(report.errors))
        named.chmod(0o600)

    def test_scrub_report_repr_hides_secret_values(self) -> None:
        self.copy.path.write_text(
            json.dumps({"tokens": {"access": REFRESHED}}), encoding="utf-8"
        )

        self.assertNotIn(REFRESHED, repr(self.scrub()))

    def test_scrub_never_changes_files_outside_the_roots(self) -> None:
        before = self.canary.stat().st_mode

        self.scrub()

        self.assertEqual(self.canary.stat().st_mode, before)


class ExposureReportTest(CredentialTestCase):
    def make_run_files(self) -> None:
        self.copy.scrub([self.home])  # the runner removes the copy before it scans
        (self.run_dir / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
        (self.run_dir / "stderr.log").write_text("", encoding="utf-8")

    def test_exposure_finds_a_secret_in_the_transcript(self) -> None:
        self.make_run_files()
        (self.run_dir / "transcript.jsonl").write_text(
            f'{{"text": "{SECRET}"}}\n', encoding="utf-8"
        )

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["by_location"]["transcript"], 1)
        self.assertEqual(report["total"], 1)

    def test_exposure_finds_a_secret_in_a_native_session_log(self) -> None:
        self.make_run_files()
        sessions = self.home / ".codex" / "sessions" / "2026"
        sessions.mkdir(parents=True)
        (sessions / "rollout-1.jsonl").write_text(f"{SECRET}\n", encoding="utf-8")

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["by_location"]["home"], 1)
        self.assertEqual(report["files"], ["home/.codex/sessions/2026/rollout-1.jsonl"])

    def test_exposure_finds_a_secret_in_the_deliverable(self) -> None:
        self.make_run_files()
        (self.work / "deliverable").mkdir()
        (self.work / "deliverable" / "manuscript.md").write_text(
            SECRET, encoding="utf-8"
        )

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["by_location"]["deliverable"], 1)

    def test_exposure_finds_a_refreshed_value_when_given_both_sets(self) -> None:
        self.make_run_files()
        (self.run_dir / "stderr.log").write_text(REFRESHED, encoding="utf-8")

        report = run_credentials.exposure_report(
            self.run_dir, frozenset({SECRET, REFRESHED})
        )

        self.assertEqual(report["by_location"]["stderr"], 1)

    def test_exposure_does_not_follow_symlinks_out_of_the_run_dir(self) -> None:
        self.make_run_files()
        (self.outside / "leak.txt").write_text(SECRET, encoding="utf-8")
        (self.home / "link").symlink_to(self.outside, target_is_directory=True)

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["total"], 0)

    def test_exposure_skips_package_cache_directories(self) -> None:
        self.make_run_files()
        cache = self.home / ".cache" / "pip"
        cache.mkdir(parents=True)
        (cache / "wheel.txt").write_text(SECRET, encoding="utf-8")

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["total"], 0)

    def test_exposure_finds_a_secret_split_across_read_chunks(self) -> None:
        self.make_run_files()
        (self.run_dir / "stderr.log").write_text(
            f"padding {SECRET} padding", encoding="utf-8"
        )

        with mock.patch.object(run_credentials, "EXPOSURE_CHUNK_BYTES", 7):
            report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["by_location"]["stderr"], 1)

    def test_exposure_does_not_follow_a_symlinked_deliverable_root(self) -> None:
        self.make_run_files()
        hidden = self.outside / "notes.txt"
        hidden.write_text(SECRET, encoding="utf-8")
        hidden.chmod(0)
        (self.work / "deliverable").symlink_to(self.outside, target_is_directory=True)

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["total"], 0)
        self.assertEqual(hidden.stat().st_mode & 0o777, 0)

    def test_exposure_does_not_follow_a_symlinked_home_root(self) -> None:
        self.make_run_files()
        (self.outside / "notes.txt").write_text(SECRET, encoding="utf-8")
        shutil.rmtree(self.home)
        self.home.symlink_to(self.outside, target_is_directory=True)

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["by_location"]["home"], 0)

    def test_exposure_redacts_file_names_that_contain_a_credential(self) -> None:
        self.make_run_files()
        sessions = self.home / ".codex" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / f"rollout-{SECRET}.jsonl").write_text(SECRET, encoding="utf-8")

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertEqual(report["by_location"]["home"], 1)
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertTrue(any("[credential]" in name for name in report["files"]))

    def test_exposure_report_never_contains_secret_values(self) -> None:
        self.make_run_files()
        (self.run_dir / "stderr.log").write_text(SECRET, encoding="utf-8")

        report = run_credentials.exposure_report(self.run_dir, frozenset({SECRET}))

        self.assertNotIn(SECRET, json.dumps(report))


if __name__ == "__main__":
    unittest.main()
