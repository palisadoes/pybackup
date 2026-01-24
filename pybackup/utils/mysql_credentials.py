# pybackup/utils/mysql_credentials.py

"""
MySQL credentials file utilities.

This module provides a safe way to create and manage temporary MySQL/MariaDB
options files for use with --defaults-extra-file. The goals are:

- Avoid exposing passwords on the command line or in process listings.
- Ensure created files are readable only by the current user (0600).
- Clean up files automatically via a context manager.
- Offer clear, Google-style docstrings and type hints.

Typical usage example:
    from pathlib import Path
    from pybackup.utils.mysql_credentials import mysql_defaults_file
    from pybackup.constants import MYSQLDUMP_BINARY

    user = "backup"
    password = "s3cr3t"
    socket = "/var/run/mysqld/mysqld.sock"

    with mysql_defaults_file(user, password, socket) as cnf:
        argv = [MYSQLDUMP_BINARY, f"--defaults-extra-file={cnf}", "--single-transaction", "mydb"]
        # run subprocess with shell=False
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


def build_defaults_file_content(
    user: str, password: str, socket: Optional[str] = None
) -> str:
    """Build the contents of a MySQL options file for --defaults-extra-file.

    The returned string follows the standard MySQL client format:

        [client]
        user=<user>
        password=<password>
        socket=<socket>    # optional

    Args:
      user: MySQL username. Must be non-empty.
      password: MySQL password. May be any non-empty string; not logged.
      socket: Optional filesystem path to the MySQL/MariaDB UNIX socket.

    Returns:
      str: The complete text to be written to the options file.

    Raises:
      ValueError: If `user` or `password` is empty/whitespace-only.

    Security:
      - The caller must write this content to a file with 0600 permissions.
      - Do not log the returned string; it contains credentials.
    """
    if not str(user).strip():
        raise ValueError("user must be non-empty")
    if not str(password).strip():
        raise ValueError("password must be non-empty")

    lines = [
        "[client]",
        f"user={user}",
        f"password={password}",
    ]
    if socket:
        lines.append(f"socket={socket}")
    return "\n".join(lines) + "\n"


@contextmanager
def mysql_defaults_file(
    user: str, password: str, socket: Optional[str] = None
) -> Iterator[Path]:
    """Create a secure temporary MySQL options file and yield its path.

    The file is created with permissions 0600 and removed automatically when
    the context exits (best effort). Use the yielded path with
    --defaults-extra-file=<path> for MySQL client utilities.

    Example:
      >>> from pybackup.utils.mysql_credentials import mysql_defaults_file
      >>> with mysql_defaults_file("backup", "s3cr3t") as cnf:
      ...     assert cnf.exists()

    Args:
      user: MySQL username for the [client] stanza.
      password: MySQL password corresponding to `user`.
      socket: Optional filesystem path to the MySQL/MariaDB UNIX socket.

    Yields:
      Path: Filesystem path to the temporary options file.

    Raises:
      OSError: If the file cannot be created, written, or permissioned.
      ValueError: If `user` or `password` is empty.

    Notes:
      - The file is created using a low-level, race-safe API and permissioned
        to 0600 via fchmod/chmod to avoid secret leakage.
      - Cleanup errors are suppressed to avoid masking primary failures.
      - On Windows, 0600 maps to user-only read/write within the platform's
        semantics; chmod behavior is limited but harmless.
    """
    fd, path = _secure_mkstemp(suffix=".cnf", prefix="pybackup_")
    p = Path(path)
    try:
        # Set restrictive permissions on the file descriptor first (best effort).
        _set_secure_permissions(fd, p)

        # Write the credentials content using the open file descriptor.
        content = build_defaults_file_content(
            user=user, password=password, socket=socket
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)

        # Redundant chmod on the named file to guard against platform quirks.
        try:
            os.chmod(p, 0o600)
        except Exception:
            # Non-fatal; permissions should already be restricted by fchmod above.
            pass

        yield p
    finally:
        try:
            # Best-effort cleanup; secrecy is more important than error reporting here.
            p.unlink(missing_ok=True)
        except Exception:
            # Suppress cleanup errors to avoid masking the original exception.
            pass


# Backward-compatibility alias (if older code refers to this name).
create_mysql_options_file = mysql_defaults_file


def _secure_mkstemp(
    suffix: str = "",
    prefix: str = "pybackup_",
    directory: Optional[str] = None,
) -> tuple[int, str]:
    """Create a secure temporary file and return (fd, path).

    This is a thin wrapper around tempfile.mkstemp that ensures the newly
    created file is not inherited by child processes (close-on-exec) on
    platforms that support it.

    Args:
      suffix: Optional filename suffix, e.g., ".cnf".
      prefix: Optional filename prefix; defaults to "pybackup_".
      directory: Optional directory in which to create the file. Defaults to the
        platform's secure temp directory.

    Returns:
      tuple[int, str]: The file descriptor and absolute path to the file.

    Raises:
      OSError: If the file cannot be created.
    """
    # Use text mode for compatibility with MySQL option file parsing.
    fd, path = tempfile.mkstemp(
        suffix=suffix, prefix=prefix, dir=directory, text=True
    )
    # Attempt to set close-on-exec (POSIX). If it fails or is unsupported, ignore.
    try:
        if hasattr(os, "set_inheritable"):
            os.set_inheritable(fd, False)  # Python-level CLOEXEC guard
    except Exception:
        pass
    return fd, path


def _set_secure_permissions(fd: int, path: Path) -> None:
    """Set restrictive permissions (0600) on the given file.

    This function attempts to set 0600 via fchmod on the open file descriptor
    where available, and falls back to chmod on the path if needed.

    Args:
      fd: Open file descriptor for the temporary file.
      path: Filesystem path to the same file (used for chmod fallback).

    Returns:
      None

    Raises:
      OSError: If permission changes fail at both fchmod and chmod steps.
    """
    fchmod_error: Optional[Exception] = None
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)  # type: ignore[attr-defined]
        else:
            raise AttributeError("os.fchmod not available")
    except Exception as exc:
        fchmod_error = exc
        try:
            os.chmod(path, 0o600)
        except Exception:
            # Prefer to raise the original fchmod error for better diagnostics.
            if fchmod_error:
                raise OSError(
                    f"Failed to set 0600 permissions: {fchmod_error}"
                ) from None
            raise
