"""Copy credentials into a run home, then remove every copy and scan for leaks.

A participant has full control of the run home while its container runs, so
cleanup treats everything under it as hostile:

- It never follows a symlink, neither for a traversal root nor for an entry, and
  it never reads or chmods anything reached through one.
- It restores the owner permissions a participant removed on the whole tree
  before it reads or deletes anything.
- It bounds every read of a participant-controlled file and streams hashes and
  scans in chunks, so a huge replacement file cannot exhaust memory.
- It deletes by path, independent of any read, and finds other copies by content.
- It records problems in a report instead of raising.

Cleanup runs after the container is gone, so no participant process can race it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")
CREDENTIAL_FILE_NAMES = frozenset({"auth.json", ".credentials.json"})
MIN_SECRET_LENGTH = 20
MAX_CREDENTIAL_BYTES = 1024 * 1024
HASH_CHUNK_BYTES = 64 * 1024
EXPOSURE_CHUNK_BYTES = 1024 * 1024
EXPOSURE_MAX_FILE_BYTES = 2 * 1024**3
EXPOSURE_MAX_LISTED_FILES = 20
OWNER_RWX = 0o700
OWNER_RW = 0o600
REDACTED = "[credential]"
EXPOSURE_PRUNED_DIRS = frozenset(
    {
        ".cache",
        ".pixi",
        ".conda",
        ".rattler",
        ".npm",
        "node_modules",
        "__pycache__",
        "site-packages",
    }
)


class _TooLargeError(Exception):
    """A credential-sized read met a file over the size bound."""


@dataclass(frozen=True, slots=True)
class ScrubReport:
    """What cleanup did. `left` is true when a copy may remain in the run dir."""

    changed: bool
    copies_removed: int
    left: bool
    errors: tuple[str, ...]
    post_secrets: frozenset[str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    """Sizes and SHA-256 digests that identify a credential copy by content."""

    sizes: frozenset[int]
    digests: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Original:
    """The file at the credential path: its content if readable, and if it remains."""

    content: bytes | None
    left: bool


@dataclass(frozen=True, slots=True)
class CredentialCopy:
    """A private copy of one credentials file. Digest and values stay out of repr()."""

    home: Path
    relpath: str
    size: int
    digest: str = field(repr=False)
    secrets: frozenset[str] = field(repr=False)

    @property
    def path(self) -> Path:
        """Where the copy was written."""
        return self.home / self.relpath

    def scrub(self, search_roots: Sequence[Path]) -> ScrubReport:
        """Delete the copy and every copy of it under `search_roots`; never raises.

        Order matters: owner permissions are restored on the whole tree first, so
        nothing is hidden behind a participant's chmod. The file's final content
        is then read (bounded) because a CLI may have refreshed the token, and it
        is deleted whatever the read did. Copies are found by size and digest, so
        a renamed or copied file goes too. A symlink is never followed.
        `left` is true if anything could not be checked or deleted.
        """
        errors: list[str] = []
        repair_problems: list[str] = []
        _phase(
            "repair permissions",
            errors,
            lambda: [
                repair_permissions(root, repair_problems) for root in search_roots
            ],
            None,
        )
        original = _phase(
            "remove original",
            errors,
            lambda: _take_original(self.home, self.relpath, errors),
            _Original(None, left=True),
        )
        fingerprint = self._fingerprint(original.content)
        removed = _phase(
            "remove copies",
            errors,
            lambda: sum(
                _remove_copies(root, fingerprint, errors) for root in search_roots
            ),
            0,
        )
        remaining, verify_problems = _phase(
            "verify",
            errors,
            lambda: _count_copies(search_roots, fingerprint),
            (1, []),
        )
        post_secrets = (
            secret_values_from_bytes(original.content)
            if original.content is not None
            else frozenset()
        )
        known = self.secrets | post_secrets
        messages = [*errors, *repair_problems, *verify_problems]
        return ScrubReport(
            changed=original.content is None
            or _sha256(original.content) != self.digest,
            copies_removed=removed,
            left=remaining > 0
            or original.left
            or bool(repair_problems)
            or bool(verify_problems),
            errors=tuple(_redact(message, known) for message in messages),
            post_secrets=post_secrets,
        )

    def _fingerprint(self, final: bytes | None) -> _Fingerprint:
        sizes, digests = {self.size}, {self.digest}
        if final is not None:
            sizes.add(len(final))
            digests.add(_sha256(final))
        return _Fingerprint(frozenset(sizes), frozenset(digests))


def copy_credentials(source: Path, home: Path, relpath: str) -> CredentialCopy:
    """Copy `source` to `home/relpath` with mode 0600 inside a 0700 directory."""
    if not source.is_file():
        raise FileNotFoundError(f"credentials file not found: {source}")
    if source.stat().st_size > MAX_CREDENTIAL_BYTES:
        raise ValueError(
            f"credentials file larger than {MAX_CREDENTIAL_BYTES} bytes: {source}"
        )
    destination = home / relpath
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(OWNER_RWX)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWNER_RW)
    with os.fdopen(descriptor, "wb") as out, source.open("rb") as handle:
        shutil.copyfileobj(handle, out)
    destination.chmod(OWNER_RW)
    if destination.stat().st_mode & 0o777 != OWNER_RW:
        destination.unlink()
        raise PermissionError(
            f"cannot set mode 0600 on {destination}; use a POSIX filesystem"
        )
    data = destination.read_bytes()
    return CredentialCopy(
        home=home,
        relpath=relpath,
        size=len(data),
        digest=_sha256(data),
        secrets=secret_values_from_bytes(data),
    )


def credential_files_in(run_dir: Path) -> list[str]:
    """Relative names of files in `run_dir/home` that look like credential copies."""
    problems: list[str] = []
    return sorted(
        str(path.relative_to(run_dir))
        for path, _size in _iter_regular_files(run_dir / "home", problems)
        if path.name in CREDENTIAL_FILE_NAMES
    )


def secret_values(credentials: Path) -> frozenset[str]:
    """Long string values inside a credentials JSON file, kept only in memory."""
    try:
        return secret_values_from_bytes(credentials.read_bytes())
    except OSError:
        return frozenset()


def secret_values_from_bytes(data: bytes) -> frozenset[str]:
    """Long string values inside JSON bytes; empty when the bytes are not JSON."""
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return frozenset()
    return frozenset(v for v in _string_leaves(document) if len(v) >= MIN_SECRET_LENGTH)


def repair_permissions(root: Path, problems: list[str]) -> None:
    """Give the owner access to every real directory and file under `root`.

    Directories are fixed before they are listed, so a chmod 000 on any level
    cannot hide what is below it. Symlinks are skipped and a symlinked `root` is
    refused, so nothing outside the tree is ever changed.
    """
    info = _lstat(root)
    if info is None or not stat.S_ISDIR(info.st_mode):
        if info is not None and stat.S_ISLNK(info.st_mode):
            problems.append(f"refused to follow symlink at {root.name}")
        return
    _grant(root, info, OWNER_RWX, problems)
    stack = [root]
    while stack:
        directory = stack.pop()
        for entry in _list_directory(directory, problems):
            path = directory / entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as err:
                problems.append(f"cannot stat {entry.name}: {_reason(err)}")
                continue
            if stat.S_ISDIR(info.st_mode):
                _grant(path, info, OWNER_RWX, problems)
                stack.append(path)
            elif stat.S_ISREG(info.st_mode):
                _grant(path, info, OWNER_RW, problems)


def exposure_report(run_dir: Path, secrets: Collection[str]) -> dict:
    """Count credential values found in the logs, native session logs and deliverable.

    Covers `transcript.jsonl`, `stderr.log`, every file under `home/` (the CLIs
    keep session logs there; package caches are skipped) and `work/deliverable`.
    The report holds counts and relative file names, never values: a name that
    contains a credential value is shown with the value replaced.
    """
    needles = [secret.encode() for secret in secrets if secret]
    problems: list[str] = []
    skipped = [0]
    by_location = {
        "transcript": _files_with_hits(
            [run_dir / "transcript.jsonl"], needles, problems, skipped
        ),
        "stderr": _files_with_hits(
            [run_dir / "stderr.log"], needles, problems, skipped
        ),
        "home": _files_with_hits(
            _walk_paths(run_dir / "home", problems, EXPOSURE_PRUNED_DIRS),
            needles,
            problems,
            skipped,
        ),
        "deliverable": _files_with_hits(
            _walk_paths(run_dir / "work" / "deliverable", problems),
            needles,
            problems,
            skipped,
        ),
    }
    hit_files = sorted(
        {
            _redact(str(path.relative_to(run_dir)), secrets)
            for paths in by_location.values()
            for path in paths
        }
    )
    return {
        "total": sum(len(paths) for paths in by_location.values()),
        "by_location": {name: len(paths) for name, paths in by_location.items()},
        "files": hit_files[:EXPOSURE_MAX_LISTED_FILES],
        "skipped_large": skipped[0],
        "scan_errors": len(problems),
    }


def _phase(name: str, errors: list[str], action: Callable[[], _T], default: _T) -> _T:
    """Run one cleanup phase; an unexpected error is recorded, never raised."""
    try:
        return action()
    except Exception as err:  # noqa: BLE001  # cleanup must continue whatever one phase does
        errors.append(f"{name} failed: {type(err).__name__}")
        return default


def _files_with_hits(
    paths: Iterable[Path],
    needles: Sequence[bytes],
    problems: list[str],
    skipped: list[int],
) -> list[Path]:
    """Return the regular files whose bytes contain a needle."""
    found = []
    for path in paths:
        info = _lstat(path)
        if info is None or not stat.S_ISREG(info.st_mode):
            continue
        if info.st_size > EXPOSURE_MAX_FILE_BYTES:
            skipped[0] += 1
            continue
        if _contains_any(path, needles, problems):
            found.append(path)
    return found


def _contains_any(path: Path, needles: Sequence[bytes], problems: list[str]) -> bool:
    """Stream a file in chunks and report whether any needle occurs in it."""
    if not needles:
        return False
    overlap = max(len(needle) for needle in needles) - 1

    def scan() -> bool:
        tail = b""
        with _open_regular(path) as handle:
            while chunk := handle.read(EXPOSURE_CHUNK_BYTES):
                window = tail + chunk
                if any(needle in window for needle in needles):
                    return True
                tail = window[-overlap:] if overlap else b""
        return False

    try:
        return _retry_after_chmod(scan, path, OWNER_RW, problems)
    except OSError as err:
        problems.append(f"cannot read {path.name}: {_reason(err)}")
        return False


def _walk_paths(
    root: Path, problems: list[str], prune: Collection[str] = ()
) -> Iterator[Path]:
    for path, _size in _iter_regular_files(root, problems, prune):
        yield path


def _take_original(home: Path, relpath: str, errors: list[str]) -> _Original:
    """Read (bounded) and delete the file at `home/relpath`, never crossing a symlink.

    The delete does not depend on the read: it runs whatever the read did.
    """
    current = home
    parts = Path(relpath).parts
    for part in parts[:-1]:
        current = current / part
        if not _is_real_directory(current, home, errors):
            return _Original(None, left=False)
    target = current / parts[-1]
    info = _lstat(target)
    if info is None:
        return _Original(None, left=False)
    if not stat.S_ISREG(info.st_mode):
        errors.append(f"not a regular file: {_relative(target, home)}")
        return _Original(None, left=False)
    content: bytes | None = None
    try:
        content = _read_credential(target, errors)
    except Exception as err:  # noqa: BLE001  # the delete below must run whatever the read did
        errors.append(f"cannot read credential file: {type(err).__name__}")
    return _Original(content, left=not _unlink(target, errors))


def _read_credential(path: Path, errors: list[str]) -> bytes | None:
    """Read a credential-sized file; a larger one is reported and not read."""

    def read() -> bytes:
        with _open_regular(path) as handle:
            if os.fstat(handle.fileno()).st_size > MAX_CREDENTIAL_BYTES:
                raise _TooLargeError
            data = handle.read(MAX_CREDENTIAL_BYTES + 1)
        if len(data) > MAX_CREDENTIAL_BYTES:
            raise _TooLargeError
        return data

    try:
        return _retry_after_chmod(read, path, OWNER_RW, errors)
    except _TooLargeError:
        errors.append(
            f"credential file larger than {MAX_CREDENTIAL_BYTES} bytes: "
            f"{path.name}; deleted without reading"
        )
    except OSError as err:
        errors.append(f"cannot read {path.name}: {_reason(err)}")
    return None


def _is_real_directory(path: Path, home: Path, errors: list[str]) -> bool:
    """True when `path` is a real directory; a symlink or other type is refused."""
    info = _lstat(path)
    if info is None:
        return False
    if stat.S_ISLNK(info.st_mode):
        errors.append(f"refused to follow symlink at {_relative(path, home)}")
        return False
    if not stat.S_ISDIR(info.st_mode):
        errors.append(f"not a directory: {_relative(path, home)}")
        return False
    return True


def _remove_copies(root: Path, fingerprint: _Fingerprint, errors: list[str]) -> int:
    """Delete every regular file under `root` that matches the fingerprint."""
    removed = 0
    for path in list(_matching_copies(root, fingerprint, errors)):
        if _unlink(path, errors):
            removed += 1
    return removed


def _count_copies(
    roots: Sequence[Path], fingerprint: _Fingerprint
) -> tuple[int, list[str]]:
    """Count copies still present; the second value holds scan problems."""
    problems: list[str] = []
    count = sum(
        len(list(_matching_copies(root, fingerprint, problems))) for root in roots
    )
    return count, problems


def _matching_copies(
    root: Path, fingerprint: _Fingerprint, problems: list[str]
) -> Iterator[Path]:
    """Yield regular files whose size and digest match, without following symlinks."""
    for path, size in _iter_regular_files(root, problems):
        if (
            size in fingerprint.sizes
            and _file_digest(path, problems) in fingerprint.digests
        ):
            yield path


def _file_digest(path: Path, problems: list[str]) -> str | None:
    """SHA-256 of a regular file, streamed in chunks; None when it cannot be read."""

    def digest() -> str:
        hasher = hashlib.sha256()
        with _open_regular(path) as handle:
            while chunk := handle.read(HASH_CHUNK_BYTES):
                hasher.update(chunk)
        return hasher.hexdigest()

    try:
        return _retry_after_chmod(digest, path, OWNER_RW, problems)
    except OSError as err:
        problems.append(f"cannot read {path.name}: {_reason(err)}")
        return None


def _iter_regular_files(
    root: Path, problems: list[str], prune: Collection[str] = ()
) -> Iterator[tuple[Path, int]]:
    """Yield (path, size) of regular files under `root`; never follows symlinks.

    The root itself is checked too: a symlinked root is refused, a missing one
    is skipped.
    """
    info = _lstat(root)
    if info is None or not stat.S_ISDIR(info.st_mode):
        if info is not None and stat.S_ISLNK(info.st_mode):
            problems.append(f"refused to follow symlink at {root.name}")
        return
    stack = [root]
    while stack:
        directory = stack.pop()
        for entry in _list_directory(directory, problems):
            entry_info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(entry_info.st_mode) and entry.name not in prune:
                stack.append(directory / entry.name)
            elif stat.S_ISREG(entry_info.st_mode):
                yield directory / entry.name, entry_info.st_size


def _list_directory(directory: Path, problems: list[str]) -> list[os.DirEntry]:
    """List a directory, restoring owner access if a participant removed it."""

    def scan() -> list[os.DirEntry]:
        with os.scandir(directory) as entries:
            return list(entries)

    try:
        return _retry_after_chmod(scan, directory, OWNER_RWX, problems)
    except OSError as err:
        problems.append(f"cannot list {directory.name}: {_reason(err)}")
        return []


def _open_regular(path: Path):  # noqa: ANN202  # a binary file object
    """Open a file for reading without following a symlink at the last component."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    return os.fdopen(descriptor, "rb")


def _unlink(path: Path, errors: list[str]) -> bool:
    """Delete a regular file, restoring write access to its directory if needed."""
    try:
        _retry_after_chmod(path.unlink, path.parent, OWNER_RWX, errors)
    except FileNotFoundError:
        return True
    except OSError as err:
        errors.append(f"cannot delete {path.name}: {_reason(err)}")
        return False
    return True


def _retry_after_chmod(
    action: Callable[[], _T], fix_path: Path, mode: int, problems: list[str]
) -> _T:
    """Run `action`; on a permission error, chmod `fix_path` and run it once more."""
    try:
        return action()
    except PermissionError:
        if not _chmod(fix_path, mode | _current_bits(fix_path), problems):
            raise
    return action()


def _grant(path: Path, info: os.stat_result, needed: int, problems: list[str]) -> None:
    """Add the owner bits in `needed` to a real file or directory that lacks them."""
    if info.st_mode & needed != needed:
        _chmod(path, (info.st_mode & 0o7777) | needed, problems)


def _chmod(path: Path, mode: int, errors: list[str]) -> bool:
    """Set a mode, refusing a symlink: a chmod would follow it out of the run."""
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            errors.append(f"refused to chmod symlink {path.name}")
            return False
        path.chmod(mode)
    except OSError as err:
        errors.append(f"cannot chmod {path.name}: {_reason(err)}")
        return False
    return True


def _current_bits(path: Path) -> int:
    """The permission bits a path has now, so a repair only adds owner bits."""
    try:
        return path.lstat().st_mode & 0o7777
    except OSError:
        return 0


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except OSError:
        return None


def _reason(err: OSError) -> str:
    return err.strerror or type(err).__name__


def _relative(path: Path, home: Path) -> str:
    return str(path.relative_to(home))


def _redact(text: str, secrets: Collection[str]) -> str:
    """Replace each credential value inside `text`, longest first."""
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _string_leaves(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_leaves(item)
