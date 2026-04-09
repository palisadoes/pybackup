"""Local backup operations.

This module implements the local backup workflow:
- Optional database dump(s).
- Filesystem backup into a compressed tar archive.
- Optional purge of old backup archives.

It is designed to be orchestrated by the CLI (bin/pybackup.py) and depends on
utility helpers for logging, command execution, filesystem operations, and
cluster gating.

Typical usage example:
    from pathlib import Path
    from pybackup.operations.local import LocalBackupManager

    manager = LocalBackupManager(
        backup_dir=Path("/var/backups"),
        directories=[Path("/etc"), Path("/srv/www")],
        exclude=["/srv/www/*/node_modules"],
    )
    manager.run(cluster_ip=None, max_age_days=7)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence

from pybackup.constants import (
    TAR_BINARY,
    NICE_BINARY,
    BACKUP_FILE_PERMISSIONS,
)
from pybackup.utils.command import execute_command
from pybackup.utils.filesystem import ensure_directory, cleanup_files
from pybackup.utils.cluster import check_cluster_master
from pybackup.utils.logging_config import log_status, log_warning, log_error

# Optional import: local DB backups (only needed when configured).
try:  # pragma: no cover - import availability depends on repository state
    from pybackup.core.mysql import MySQLBackupManager  # type: ignore
except Exception:  # pragma: no cover
    MySQLBackupManager = None  # type: ignore


@dataclass(frozen=True)
class LocalBackupResult:
    """Summary of a local backup run.

    Attributes:
        archive (Optional[Path]): Final path of the filesystem
            archive produced, or None if the filesystem phase did not run.
        database_files (List[Path]): Paths to database dump files produced.
        purged_count (int): Number of archived files purged by retention.
    """

    archive: Optional[Path]
    database_files: List[Path]
    purged_count: int


class LocalBackupManager:
    """Orchestrates local backup activities (DB + filesystem + purge).

    This manager provides a cohesive flow for:
    1) Optionally dumping databases (when a MySQL configuration is supplied).
    2) Creating a compressed tar archive of target directories.
    3) Optionally purging older archives by a retention policy.

    The manager uses safe subprocess execution (no shell) and performs
    filesystem operations with explicit permissions where appropriate. It is
    cluster-aware by virtue of the `check_cluster_master` helper.

    Attributes:
        backup_dir (Path): Destination directory for backup outputs.
        directories (List[Path]): Directories to back up into the archive.
        exclude (List[str]): Patterns to exclude from the tar archive.
        mysqldb (Any | None): Optional MySQL configuration object. When
            provided, a database backup phase is executed before the
            filesystem phase.
        tar_binary (str): Filesystem path to the tar executable.
        nice_binary (str): Filesystem path to the nice executable.
        file_mode (int): Octal file permissions to apply to the final archive.
    """

    def __init__(
        self,
        backup_dir: Path,
        directories: Sequence[Path],
        exclude: Optional[Sequence[str]] = None,
        mysqldb: Optional[Any] = None,
        tar_binary: str = TAR_BINARY,
        nice_binary: str = NICE_BINARY,
        file_mode: int = BACKUP_FILE_PERMISSIONS,
    ) -> None:
        """Initialize a LocalBackupManager.

        Args:
            backup_dir: Destination directory for backup outputs.
            directories: Directories to back up into the archive.
            exclude: Optional list of tar exclude patterns.
            mysqldb: Optional MySQL configuration object. If provided, a DB
                backup phase is attempted. The object may be a typed model
                (e.g., MySQLConfig) or a plain dict with keys:
                {"user", "password", "socket"}.
            tar_binary: Filesystem path to the tar executable (defaults to
                pybackup.constants.TAR_BINARY).
            nice_binary: Filesystem path to the nice executable (defaults to
                pybackup.constants.NICE_BINARY).
            file_mode: File permissions to apply to the final archive (octal
                int; defaults to pybackup.constants.BACKUP_FILE_PERMISSIONS).
        """
        self.backup_dir: Path = Path(backup_dir)
        self.directories: List[Path] = [Path(d) for d in directories]
        self.exclude: List[str] = list(exclude or [])
        self.mysqldb: Optional[Any] = mysqldb

        self.tar_binary = tar_binary
        self.nice_binary = nice_binary
        self.file_mode = file_mode

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def run(
        self,
        cluster_ip: Optional[str],
        max_age_days: Optional[int] = None,
        db_fail_fast: bool = True,
    ) -> LocalBackupResult:
        """Execute the full local backup flow.

        Sequence:
          1) Gate by cluster master (if `cluster_ip` is provided).
          2) Optionally run database backup(s) if MySQL configuration supplied.
          3) Create filesystem tar archive with exclusions.
          4) Optionally purge old archives by retention days.

        Args:
            cluster_ip: Cluster virtual IP. When provided, the operation only
                proceeds on the node that currently owns this IP.
            max_age_days: Optional retention window. When provided, archives
                older than this number of days are purged from `backup_dir`.
            db_fail_fast: If True and the database backup phase fails, the
                entire run is considered failed; otherwise, the filesystem
                phase proceeds and the DB error is logged.

        Returns:
            LocalBackupResult: Summary including database outputs, the archive
            path, and the number of purged files.

        Raises:
            RuntimeError: If DB backup fails and `db_fail_fast` is True.
        """
        log_status("BK-LCL-START", "Starting local backup run")

        # 1) Cluster gating.
        if not check_cluster_master(cluster_ip, mode_name="local"):
            log_status(
                "BK-LCL-SKIP", "Skipping local backup: not cluster master"
            )
            return LocalBackupResult(
                archive=None, database_files=[], purged_count=0
            )

        # Ensure destination exists with safe permissions.
        ensure_directory(self.backup_dir)

        # 2) Database phase (optional).
        db_files: List[Path] = []
        try:
            if self.mysqldb is not None:
                db_files = self._backup_mysql(f"{self.backup_dir}{os.sep}db")
        except Exception as exc:
            log_error("BK-LCL-DB", f"Database backup failed: {exc}")
            if db_fail_fast:
                raise

        # 3) Filesystem archive phase.
        archive = self._backup_filesystem()

        # 4) Retention purge phase (optional).
        purged = 0
        if isinstance(max_age_days, int) and max_age_days >= 0:
            purged = self._purge_old_backups(max_age_days)

        log_status("BK-LCL-DONE", "Local backup run completed")
        return LocalBackupResult(
            archive=archive, database_files=db_files, purged_count=purged
        )

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    def _backup_filesystem(self) -> Path:
        """Create a compressed tar archive of configured directories.

        The archive is first written to a temporary path, then atomically
        renamed to the final destination only if the tar command succeeds.

        Returns:
            Path: The final archive path
                (e.g., /var/backups/files-YYYYmmdd-HHMMSS.tgz).

        Raises:
            RuntimeError: If the tar command fails.
        """
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        final_archive = self.backup_dir / f"files-{timestamp}.tgz"
        temp_archive = final_archive.with_suffix(final_archive.suffix + ".tmp")

        args = self._build_tar_args(temp_archive)
        log_status(
            "BK-LCL-FS", f"Creating filesystem archive: {final_archive}"
        )

        result = execute_command(args)
        if result.returncode != 0:
            # Ensure no leaked partial artifact.
            try:
                temp_archive.unlink(missing_ok=True)
            except Exception:
                pass
            raise RuntimeError(
                f"""\
tar failed (code={result.returncode}): {result.stderr or result.stdout}"""
            )

        # Apply permissions to the temporary file, then atomically rename.
        try:
            os.chmod(temp_archive, self.file_mode)
        except Exception as exc:  # Non-fatal; log and continue to rename.
            log_warning("BK-LCL-FS-PERM", f"Failed to chmod archive: {exc}")

        temp_archive.replace(final_archive)
        return final_archive

    def _backup_mysql(self, output_dir: Path) -> List[Path]:
        """Run database backup(s) when a MySQL configuration is supplied.

        This method is a no-op when `MySQLBackupManager` is unavailable at
        runtime. When available, it expects `self.mysqldb` to provide
        attributes or mapping keys: "user", "password", and optional "socket".

        Args:
            output_dir: Directory into which database dumps should be written.

        Returns:
            List[Path]: A list of paths to database dump files produced.

        Raises:
            RuntimeError: If the database phase fails critically.
        """
        if MySQLBackupManager is None:
            log_warning(
                "BK-LCL-DB-SKIP",
                "MySQL backup manager not available; skipping DB phase",
            )
            return []

        user = _get_attr_or_key(self.mysqldb, "user", None)
        password = _get_attr_or_key(self.mysqldb, "password", None)
        socket_value = _get_attr_or_key(self.mysqldb, "socket", None)

        if not user or not password:
            raise RuntimeError(
                """\
MySQL credentials incomplete: 'user' and 'password' are required"""
            )

        sock_path = Path(socket_value) if socket_value else None

        # Initialize and run DB backup manager.
        ensure_directory(output_dir)
        manager = MySQLBackupManager(
            # The MySQLBackupManager constructor is expected to
            # accept user, password, and socket. If your implementation
            # differs, adjust the calls below accordingly.
            conn=type(
                "Conn",
                (),
                {"user": user, "password": password, "socket": sock_path},
            )(),  # lightweight shim
            output_dir=output_dir,
        )
        log_status("BK-LCL-DB", "Starting database backup phase")
        produced: List[Path] = manager.backup_all()
        log_status(
            "BK-LCL-DB",
            f"Database backup phase completed: {len(produced)} file(s)",
        )
        return produced

    def _purge_old_backups(self, max_age_days: int) -> int:
        """Delete backup archives older than the specified retention.

        Args:
            max_age_days: Number of days beyond which archives (matching *.tgz)
                are deleted from `backup_dir`.

        Returns:
            int: Number of files deleted.
        """
        purged = cleanup_files(self.backup_dir, "*.tgz", max_age_days)
        if purged > 0:
            log_status(
                "BK-LCL-PURGE",
                f"Purged {purged} old archive(s) (>{max_age_days}d)",
            )
        else:
            log_status("BK-LCL-PURGE", "No archives eligible for purge")
        return purged

    # --------------------------------------------------------------------- #
    # Tar helpers
    # --------------------------------------------------------------------- #
    def _build_tar_args(self, archive_path: Path) -> List[str]:
        """Construct argv for creating a gzip-compressed tar archive.

        The resulting command line uses `nice` as the outer executable and
        invokes tar with the following structure:

            [nice, tar, --create, --gzip, --file, <archive>, --absolute-names,
             --exclude, <pattern>..., <dir1>, <dir2>, ...]

        Args:
            archive_path: Destination path for the (temporary) tar archive.

        Returns:
            List[str]: Argument vector suitable for subprocess execution
            without involving a shell.
        """
        args: List[str] = [
            self.nice_binary,
            self.tar_binary,
            "--create",
            "--gzip",
            "--file",
            str(archive_path),
            "--absolute-names",
        ]
        for pat in self.exclude:
            args.extend(["--exclude", pat])
        args.extend([str(p) for p in self.directories])
        return args


# ------------------------------------------------------------------------- #
# Local utilities (private)
# ------------------------------------------------------------------------- #
def _get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    """Return an attribute or dict key from a configuration object.

    This is a lightweight adapter that supports typed configuration models
    (e.g., Pydantic) and plain dictionaries with identical field names.

    Args:
        obj: An arbitrary object or mapping containing configuration fields.
        name: Attribute or key name to read.
        default: Value to return when the field is not present.

    Returns:
        Any: The retrieved value or `default` if absent.
    """
    if obj is None:
        return default
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default
