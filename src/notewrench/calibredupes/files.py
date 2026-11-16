"""Opt-in, descriptor-relative verification of declared format files.

No resolve-then-open containment checks: every directory and final file is
opened with O_NOFOLLOW. Never search for, repair, or rename format files.
"""

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import stat

from .reader import Book, Library, ReviewError, _library_path


def _require_safe_open() -> None:
    if (os.name != "posix" or not all(hasattr(os, flag) for flag in
                                     ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
            or os.open not in os.supports_dir_fd):
        raise ReviewError("file verification requires POSIX no-follow descriptor-relative opens")


@contextmanager
def _root_descriptor(root: Path):
    _require_safe_open()
    fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(root.anchor, flags)
        for component in root.parts[1:]:
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as exc:
        raise ReviewError(f"cannot open verification root without symlinks {root}: {exc}") from exc
    finally:
        if fd is not None:
            os.close(fd)


def validate_verification_input(value: str | os.PathLike[str]) -> None:
    """Validate path text and reject aliases before locate_library resolves it."""
    path = _library_path(value)
    try:
        expanded = path.expanduser()
    except RuntimeError as exc:
        raise ReviewError(f"cannot expand library path {path}: {exc}") from exc
    path = expanded.absolute()
    root = path if path.is_dir() else path.parent
    with _root_descriptor(root) as fd:
        if not stat.S_ISREG(os.stat("metadata.db", dir_fd=fd, follow_symlinks=False).st_mode):
            raise ReviewError("verification root must contain a regular, non-symlink metadata.db")


def _component(value: str) -> bool:
    # Also reject Windows separators/drive syntax rather than interpreting them
    # differently if a library has been copied between operating systems.
    return bool(value) and value not in (".", "..") and not any(c in value for c in "/\\\x00:")


def _signature(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass
class _ReadBudget:
    limit: int | None
    total: int = 0

    def check_file(self, size: int, ref: str, fmt: str) -> None:
        if self.limit is not None and size > self.limit - self.total:
            raise ReviewError(
                f"--max-total-file-bytes limit {self.limit} exceeded by {ref}/{fmt}: "
                f"{self.total} bytes already read, file needs {size} bytes")


def _inspect(root_fd: int, book: Book, record: dict, budget: _ReadBudget) -> dict:
    result = {"ref": book.key, "format": record["format"],
              "declaredBytes": record["uncompressedSize"]}
    components = book.path.split("/")
    if not all(_component(c) for c in components) or not _component(record["name"]):
        return {**result, "status": "unsafe-path"}
    filename = record["name"] + "." + record["format"].lower()
    result["relativePath"] = "/".join([*components, filename])
    directory = os.dup(root_fd)
    file_fd = None
    try:
        for component in components:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.stat(filename, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            return {**result, "status": "blocked-path"}
        if not stat.S_ISREG(info.st_mode):
            return {**result, "status": "not-regular"}
        # O_NONBLOCK also prevents hanging if a regular file is swapped for a
        # FIFO after lstat. fstat checks the object actually opened before reads.
        file_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=directory)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            return {**result, "status": "not-regular"}
        if _signature(info) != _signature(before):
            return {**result, "status": "changed-during-read"}
        budget.check_file(before.st_size, book.key, record["format"])
        digest = hashlib.sha256()
        count = 0
        # The whole observed length fits the remaining budget. Never probe one
        # byte beyond it: the final signature check also detects file growth.
        while count < before.st_size:
            chunk = os.read(file_fd, min(1024 * 1024, before.st_size - count))
            if not chunk:
                break
            # Failed/changed files still consumed I/O; do not refund their bytes.
            budget.total += len(chunk)
            digest.update(chunk)
            count += len(chunk)
        after = os.fstat(file_fd)
        if count != before.st_size or _signature(before) != _signature(after):
            return {**result, "status": "changed-during-read"}
        return {**result, "status": "verified", "bytes": count,
                "sha256": digest.hexdigest(),
                "declaredSizeMatches": count == record["uncompressedSize"]}
    except OSError as exc:
        status = {errno.ENOENT: "missing", errno.ELOOP: "blocked-path",
                  errno.ENOTDIR: "blocked-path", errno.EACCES: "unreadable",
                  errno.EPERM: "unreadable"}.get(exc.errno, "read-error")
        return {**result, "status": status, "error": exc.strerror}
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory)


def validate_verification_budgets(*, enabled: bool,
                                max_total_file_bytes: int | None = None,
                                max_file_records: int | None = None) -> None:
    """Validate options without accessing libraries or the filesystem."""
    if type(enabled) is not bool:
        raise ReviewError("--verify-files must be a boolean")
    if max_file_records is not None:
        if type(max_file_records) is not int or max_file_records < 0:
            raise ReviewError("--max-file-records must be a nonnegative integer")
        if not enabled:
            raise ReviewError("--max-file-records requires --verify-files")
    if max_total_file_bytes is not None:
        if type(max_total_file_bytes) is not int or max_total_file_bytes < 0:
            raise ReviewError("--max-total-file-bytes must be a nonnegative integer")
        if not enabled:
            raise ReviewError("--max-total-file-bytes requires --verify-files")


def verify_libraries(libraries: list[Library], *, enabled: bool,
                     max_total_file_bytes: int | None = None,
                     max_file_records: int | None = None) -> dict:
    validate_verification_budgets(enabled=enabled,
                                  max_total_file_bytes=max_total_file_bytes,
                                  max_file_records=max_file_records)
    if not enabled:
        return {"enabled": False, "evidenceLevel": "metadata-only", "records": []}
    budget = _ReadBudget(max_total_file_bytes)
    records = []
    for library in libraries:
        with _root_descriptor(library.root) as fd:
            for book in library.books:
                for record in book.data:
                    # Count declarations, including rejected/missing paths. Check
                    # before _inspect so N+1 never reaches format-path handling.
                    if max_file_records is not None and len(records) >= max_file_records:
                        raise ReviewError(
                            f"--max-file-records limit {max_file_records} exceeded by "
                            f"{book.key}/{record['format']}: "
                            f"{len(records)} declarations already inspected")
                    records.append(_inspect(fd, book, record, budget))
    return {"enabled": True, "evidenceLevel": "format-verification", "records": records,
            "maxFileRecords": max_file_records, "fileRecordsInspected": len(records),
            "maxTotalFileBytes": budget.limit, "totalBytesRead": budget.total,
            "summary": dict(sorted(Counter(r["status"] for r in records).items()))}


def content_evidence(a: Book, b: Book, records: dict, *, enabled: bool) -> dict:
    comparisons = []
    for fmt in sorted(a.formats & b.formats):
        left, right = records.get((a.key, fmt)), records.get((b.key, fmt))
        verified = left and right and left["status"] == right["status"] == "verified"
        status = "unverified"
        if verified:
            status = ("same-bytes-sha256" if (left["bytes"], left["sha256"]) ==
                      (right["bytes"], right["sha256"]) else "different-bytes")
        comparisons.append({"format": fmt, "status": status,
                            "aStatus": left["status"] if left else "not-inspected",
                            "bStatus": right["status"] if right else "not-inspected"})
    level = "metadata-only"
    if enabled and comparisons:
        statuses = {c["status"] for c in comparisons}
        if "different-bytes" in statuses:
            level = "content-different"
        elif statuses == {"same-bytes-sha256"}:
            level = "content-match"
        else:
            level = "content-incomplete"
    return {"level": level, "formats": comparisons}
