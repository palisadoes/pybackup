r"""Filesystem utilities for pybackup.

This module centralizes small, reusable helpers for safe and predictable
filesystem operations used across pybackup. Goals:

- Create directories idempotently with explicit permissions.
- Validate the existence and kind of paths (file/dir).
- Write files atomically (to avoid partial writes on crashes).
- Manage retention (delete files older than N days).
- Provide small conveniences (set permissions, safe unlink, etc.).

The functions here avoid using a shell and rely on the Python standard
library so they work consistently across platforms.

Typical usage examples:
    from pathlib import Path
    from pybackup.utils.filesystem import (
        ensure_directory, write_text_atomic, cleanup_files
    )

    ensure_directory(Path("/var/backups"), dir_mode=0o750)
    write_text_atomic(Path("/var/backups/info.txt"), "ok\\n")
    removed = cleanup_files(Path("/var/backups"), "*.tgz", max_age_days=7)
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Iterable, List

from pybackup.constants import (
    BACKUP_FILE_PERMISSIONS,
    LOG_DIR_PERMISSIONS,
    SECONDS_PER_DAY,
)
from pybackup.utils.logging_config import log_status, log_warning


# Public API exported by this module.
__all__ = [
    "ensure_directory",
    "ensure_parent_dir",
    "validate_path_exists",
    "validate_paths_exist",
    "is_writable_directory",
    "set_permissions",
    "write_text_atomic",
    "write_bytes_atomic",
    "list_files_older_than",
    "cleanup_files",
    "safe_unlink",
]


def ensure_directory(path: Path, dir_mode: int = LOG_DIR_PERMISSIONS) -> Path:
    """Create a directory and its parents if needed (idempotent).

    The directory will be created with the specified permissions, when the
    platform and effective permissions allow it. Failure to chmod is not
    fatal but will be logged.

    Args:
      path: Directory to create.
      dir_mode: Octal mode to apply to the directory (e.g., 0o750).

    Returns:
      Path: The same Path that was ensured.

    Examples:
      >>> from pathlib import Path
      >>> ensure_directory(Path("/var/backups"), 0o750)
      PosixPath('/var/backups')
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, dir_mode)
    except Exception as exc:  # Non-fatal on some platforms.
        log_warning("BK-FS-PERM", f"Failed to chmod directory {path}: {exc}")
    return path


def ensure_parent_dir(path: Path, dir_mode: int = LOG_DIR_PERMISSIONS) -> Path:
    """Ensure that the parent directory of a file path exists.

    Args:
      path: A file path whose parent directory should exist.
      dir_mode: Octal mode to apply to any created parent directories.

    Returns:
      Path: The ensured parent directory path.

    Raises:
      ValueError: If `path` does not have a parent component.

    Examples:
      >>> from pathlib import Path
      >>> ensure_parent_dir(Path("/var/backups/info.txt"))
      PosixPath('/var/backups')
    """
    path = Path(path)
    parent = path.parent
    if not str(parent):
        raise ValueError(f"Path has no parent: {path}")
    return ensure_directory(parent, dir_mode=dir_mode)


def validate_path_exists(path: Path, kind: str = "dir") -> None:
    """Validate that a path exists and is of the requested kind.

    Args:
      path: The path to validate.
      kind: Either "dir" for directory or "file" for regular file.

    Returns:
      None

    Raises:
      FileNotFoundError: If the path does not exist.
      NotADirectoryError: If kind="dir" but path is not a directory.
      IsADirectoryError: If kind="file" but path is a directory.

    Examples:
      >>> from pathlib import Path
      >>> validate_path_exists(Path("/etc"), kind="dir")
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    if kind == "dir":
        if not path.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")
    elif kind == "file":
        if path.is_dir():
            raise IsADirectoryError(f"Expected file, found directory: {path}")
    else:
        raise ValueError("kind must be 'dir' or 'file'")


def validate_paths_exist(paths: Iterable[Path], kind: str = "dir") -> None:
    """Validate a sequence of paths with the same kind requirement.

    Args:
      paths: Iterable of paths to validate.
      kind: Either "dir" or "file".

    Returns:
      None

    Raises:
      FileNotFoundError: If any path does not exist.
      NotADirectoryError: If kind="dir" but a path is not a directory.
      IsADirectoryError: If kind="file" but a path is a directory.
    """
    for p in paths or []:
        validate_path_exists(Path(p), kind=kind)


def is_writable_directory(path: Path) -> bool:
    """Return True if the given path is an existing, writable directory.

    Args:
      path: Directory path to check.

    Returns:
      bool: True when the directory exists and is writable by the process.

    Examples:
      >>> from pathlib import Path
      >>> is_writable_directory(Path("/tmp"))  # doctest: +SKIP
      True
    """
    path = Path(path)
    return path.is_dir() and os.access(path, os.W_OK)


def set_permissions(path: Path, mode: int) -> None:
    """Set permissions on a path (best-effort).

    This function applies the specified mode to the path. Failures are logged
    but not raised, allowing the caller to proceed when permissions are only
    advisory.

    Args:
      path: Path whose permissions are to be changed.
      mode: Octal permission bits (e.g., 0o640).

    Returns:
      None
    """
    try:
        os.chmod(Path(path), mode)
    except Exception as exc:
        log_warning("BK-FS-PERM", f"Failed to chmod {path}: {exc}")


def write_text_atomic(
    path: Path,
    text: str,
    mode: int = BACKUP_FILE_PERMISSIONS,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> Path:
    """Write text to a file atomically with a temporary sibling and replace.

    The function writes to a temporary file in the destination directory,
    flushes and fsyncs it, sets permissions, and then atomically replaces
    the target path. If any step fails, the temporary file is cleaned up.

    Args:
      path: Destination file path.
      text: Unicode text to write.
      mode: Octal permission bits to set on the resulting file.
      encoding: Text encoding for serialization.
      newline: Newline normalization; passed to open(..., newline=...).

    Returns:
      Path: The destination file path.

    Raises:
      OSError: On I/O errors while writing or replacing.
    """
    path = Path(path)
    ensure_parent_dir(path)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".tmp_", dir=str(path.parent), text=True
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "w", encoding=encoding, newline=newline) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        set_permissions(tmp_path, mode)
        os.replace(str(tmp_path), str(path))
        return path
    except Exception:
        # Best-effort cleanup of the temp file on failure.
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise


def write_bytes_atomic(
    path: Path, data: bytes, mode: int = BACKUP_FILE_PERMISSIONS
) -> Path:
    r"""Write bytes to a file atomically with a temporary sibling and replace.

    Args:
      path: Destination file path.
      data: Byte sequence to write.
      mode: Octal permission bits to set on the resulting file.

    Returns:
      Path: The destination file path.

    Raises:
      OSError: On I/O errors while writing or replacing.

    Examples:
      >>> from pathlib import Path
      >>> _ = write_bytes_atomic(Path("/tmp/example.bin"), b"\\x00")
    """
    path = Path(path)
    ensure_parent_dir(path)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        set_permissions(tmp_path, mode)
        os.replace(str(tmp_path), str(path))
        return path
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise


def list_files_older_than(
    directory: Path, pattern: str, max_age_days: int
) -> List[Path]:
    """Return files in a directory matching a pattern and older than N days.

    Args:
      directory: Directory to scan.
      pattern: Glob-style file pattern (e.g., "*.tgz").
      max_age_days: Age threshold in whole days; files older than
        this are returned.

    Returns:
      List[Path]: Matching files older than the threshold. Returns an
        empty list if the directory does not exist.

    Examples:
      >>> from pathlib import Path
      >>> list_files_older_than(Path('/var/backups'), '*.tgz', 7)
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    cutoff = time.time() - (max_age_days * SECONDS_PER_DAY)
    results: List[Path] = []
    for p in directory.glob(pattern):
        try:
            if not p.is_file():
                continue
            mtime = p.stat().st_mtime
            if mtime < cutoff:
                results.append(p)
        except FileNotFoundError:
            # The file may have been concurrently removed; skip it.
            continue
    return results


def cleanup_files(directory: Path, pattern: str, max_age_days: int) -> int:
    """Delete files in a directory matching a pattern older than N days.

    Args:
      directory: Directory to scan.
      pattern: Glob-style file pattern (e.g., "*.tgz").
      max_age_days: Age threshold in whole days; files older than this
        are deleted.

    Returns:
      int: Count of files successfully removed.

    Notes:
      - Files that disappear between listing and deletion are ignored.
      - Failures are logged but do not abort the process.

    Examples:
      >>> from pathlib import Path
      >>> # cleanup_files(Path('/var/backups'), '*.tgz', 30)  # doctest: +SKIP
    """
    old_files = list_files_older_than(directory, pattern, max_age_days)
    removed = 0
    for p in old_files:
        try:
            p.unlink()
            removed += 1
        except FileNotFoundError:
            # Already gone; treat as removed.
            removed += 1
        except Exception as exc:
            log_warning("BK-FS-RM", f"Failed to remove {p}: {exc}")

    if removed > 0:
        log_status(
            "BK-FS-CLEAN", f"Removed {removed} file(s) from {directory}"
        )
    return removed


def safe_unlink(path: Path) -> None:
    """Unlink (delete) a path if it exists, suppressing common errors.

    Args:
      path: File path to remove.

    Returns:
      None

    Notes:
      - Any FileNotFoundError is ignored.
      - Other exceptions are logged and suppressed.
    """
    p = Path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:
        log_warning("BK-FS-RM", f"Failed to unlink {p}: {exc}")
