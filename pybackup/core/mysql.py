"""MySQL/MariaDB backup operations.

This module provides a small, testable wrapper for creating logical backups of
MySQL or MariaDB databases. The design goals are:

- Do not expose credentials on the command line or in logs.
- Use safe subprocess execution (shell=False).
- Keep responsibilities narrowly scoped and easily unit-testable.
- Provide clear, Google-style docstrings and type hints.

Typical usage example:
    from pathlib import Path
    from pybackup.core.mysql import MySQLConnInfo, MySQLBackupManager

    conn = MySQLConnInfo(
        user="backup", password="s3cr3t",
        socket=Path("/var/run/mysqld/mysqld.sock"))
    mgr = MySQLBackupManager(conn=conn, output_dir=Path("/var/backups/db"))
    mgr.validate_environment()
    files = mgr.backup_all()

"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set

from pybackup.constants import (
    MYSQLDUMP_BINARY,
    GZIP_BINARY,
    BACKUP_FILE_PERMISSIONS,
)
from pybackup.utils.command import execute_command
from pybackup.utils.logging_config import log_status, log_warning, log_error
from pybackup.utils.mysql_credentials import mysql_defaults_file

# pymysql is required at runtime for listing databases.
try:
    import pymysql  # type: ignore
except (
    Exception
) as _:  # pragma: no cover - import errors are handled at call-time
    print(_)
    pymysql = None  # type: ignore


_SYSTEM_DATABASES: Set[str] = {
    "information_schema",
    "performance_schema",
    "mysql",
    "sys",
}


@dataclass(frozen=True)
class MySQLConnInfo:
    """Connection parameters for MySQL/MariaDB.

    Attributes:
        user: Database user to connect as (e.g., "backup").
        password: Password for the database user.
        socket: Optional UNIX-domain socket path
            (e.g., /var/run/mysqld/mysqld.sock).
        host: Optional hostname to connect to when no socket is provided.
            Defaults to "localhost" if not set and socket is None.
        port: Optional port for TCP connections. Defaults to 3306 when used.
    """

    user: str
    password: str
    socket: Optional[Path] = None
    host: Optional[str] = None
    port: Optional[int] = None


class MySQLBackupManager:
    """Create logical dumps of databases and writes compressed files.

    This manager encapsulates the following phases:
    1) Environment checks (output directory).
    2) Database discovery (SHOW DATABASES).
    3) Dumping each database via mysqldump (using a secure options file).
    4) Compressing dumps to <name>-YYYYmmdd-HHMMSS.sql.gz and
       setting permissions.

    The implementation intentionally:
    - Avoids shell pipelines by capturing mysqldump stdout and then
        calling gzip.
    - Ensures passwords are not present in process lists or logs by using
      --defaults-extra-file with a temporary credentials file.

    Attributes:
        conn: MySQLConnInfo containing credentials and socket/host info.
        output_dir: Directory where dump files are written.
        exclude: Database names to skip (in addition to system databases).
        gzip_level: Compression level for gzip, from 1..9 (9 is maximum).
    """

    def __init__(
        self,
        conn: MySQLConnInfo,
        output_dir: Path,
        exclude: Iterable[str] | None = None,
        gzip_level: int = 9,
    ) -> None:
        """Initialize a MySQLBackupManager.

        Args:
            conn: Connection info (user, password, and optional
                socket/host/port).
            output_dir: Directory where dump files will be written.
            exclude: Iterable of database names to skip
                (in addition to system DBs).
            gzip_level: Gzip compression level (1..9). Defaults to 9.

        Raises:
            ValueError: If gzip_level is outside the 1..9 range.
        """
        if not 1 <= gzip_level <= 9:
            raise ValueError("gzip_level must be in the range 1..9")

        self.conn = conn
        self.output_dir = Path(output_dir)
        self.exclude: Set[str] = set(exclude or [])
        self.gzip_level = gzip_level

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def validate_environment(self) -> None:
        """Validate and prepare the backup environment.

        This method ensures the output directory exists and is writable. It
        does not attempt to check for mysqldump or gzip executables on disk,
        assuming they are available in their configured locations.

        Raises:
            PermissionError: If the output directory cannot be created/written.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Attempt a lightweight write test (optional; failures will occur
        # at dump time otherwise).
        test_path = self.output_dir / ".pybackup_write_test"
        try:
            test_path.write_text("ok", encoding="utf-8")
            test_path.unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise PermissionError(
                f"Cannot write to output directory: {self.output_dir} ({exc})"
            ) from exc

    def list_databases(self) -> List[str]:
        """Return a list of database names to dump.

        This connects to the server using either a UNIX socket or TCP
        (host/port) and runs "SHOW DATABASES". System databases and
        any explicitly excluded names are filtered out.

        Returns:
            List[str]: Database names eligible for dumping.

        Raises:
            RuntimeError: If pymysql is not installed or a connection
                error occurs.
        """
        if pymysql is None:
            raise RuntimeError(
                "The 'pymysql' package is required to "
                "list databases but is not installed."
            )

        # Construct connection kwargs for socket or TCP.
        kwargs: dict = {"user": self.conn.user, "password": self.conn.password}
        if self.conn.socket:
            kwargs["unix_socket"] = str(self.conn.socket)
        else:
            kwargs["host"] = self.conn.host or "localhost"
            if self.conn.port:
                kwargs["port"] = int(self.conn.port)

        names: List[str] = []
        try:
            connection = pymysql.connect(**kwargs)  # type: ignore[arg-type]
            try:
                with connection.cursor() as cur:
                    cur.execute("SHOW DATABASES")
                    rows = cur.fetchall()
                    names = [row[0] for row in rows]
            finally:
                connection.close()
        except Exception as exc:
            raise RuntimeError(f"Failed to list databases: {exc}") from exc

        # Filter system DBs and user-specified excludes.
        forbidden = _SYSTEM_DATABASES | self.exclude
        return [n for n in names if n not in forbidden]

    def backup_database(self, name: str) -> Path:
        """Dump a single database to a compressed file.

        The dump is written as:
            <output_dir>/<name>-YYYYmmdd-HHMMSS.sql.gz

        Implementation details:
        - A temporary MySQL options file is created with user/password/socket.
        - mysqldump is executed with --defaults-extra-file pointing
            to that file.
        - The SQL text is captured and written to a temporary .sql file.
        - gzip is invoked to compress the .sql file into .sql.gz.
        - The .sql.gz file's permissions are set to BACKUP_FILE_PERMISSIONS.

        Args:
            name: Target database name.

        Returns:
            Path: Final path to the compressed dump file.

        Raises:
            RuntimeError: If mysqldump or gzip fails.
        """
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        tmp_sql = self.output_dir / f"{name}-{timestamp}.sql"
        final_gz = self.output_dir / f"{name}-{timestamp}.sql.gz"

        # 1) Run mysqldump safely with a temporary defaults file.
        with mysql_defaults_file(
            self.conn.user,
            self.conn.password,
            str(self.conn.socket) if self.conn.socket else None,
        ) as cnf:
            dump_args = self._build_dump_args(cnf_path=cnf, db=name)
            log_status("BK-SQL-DUMP", f"Dumping database: {name}")

            res = execute_command(dump_args)
            if res.returncode != 0:
                raise RuntimeError(f"""\
mysqldump failed for '{name}' (code={res.returncode}): \
{res.stderr or res.stdout}""")

        # 2) Write stdout to a temporary .sql (avoid shell pipelines).
        try:
            tmp_sql.write_text(res.stdout, encoding="utf-8")
        except Exception as exc:
            # Attempt cleanup of partially written file.
            try:
                tmp_sql.unlink(missing_ok=True)
            except Exception:
                pass
            raise RuntimeError(
                f"Failed to write SQL file for '{name}': {exc}"
            ) from exc

        # 3) Compress to .gz using system gzip (still without shell).
        log_status("BK-SQL-GZIP", f"Compressing dump for database: {name}")
        gz_args = [GZIP_BINARY, f"-{self.gzip_level}", str(tmp_sql)]
        gz = execute_command(gz_args)
        if gz.returncode != 0:
            # If gzip fails, the .sql file may remain present.
            raise RuntimeError(f"""\
gzip failed for '{name}' (code={gz.returncode}): {gz.stderr or gz.stdout}""")

        # 4) Harden permissions on the final .gz.
        try:
            os.chmod(final_gz, BACKUP_FILE_PERMISSIONS)
        except Exception as exc:  # Non-fatal, log a warning and continue.
            log_warning(
                "BK-SQL-PERM", f"Failed to chmod dump '{final_gz}': {exc}"
            )

        return final_gz

    def backup_all(self) -> List[Path]:
        """Dump all databases returned by list_databases().

        Returns:
            List[Path]: Paths to compressed dump files for each database.
        """
        self.validate_environment()
        produced: List[Path] = []
        dbs = self.list_databases()
        if not dbs:
            log_status("BK-SQL-NOOP", "No databases to back up")
            return produced

        log_status("BK-SQL-START", f"Backing up {len(dbs)} database(s)")
        for name in dbs:
            try:
                produced.append(self.backup_database(name))
            except Exception as exc:
                # Log but continue to next database.
                log_error(
                    "BK-SQL-DB-FAIL", f"Failed backing up '{name}': {exc}"
                )
        log_status(
            "BK-SQL-DONE",
            f"Database backup completed: {len(produced)} file(s)",
        )
        return produced

    # --------------------------------------------------------------------- #
    # Internals
    # --------------------------------------------------------------------- #
    def _build_dump_args(self, cnf_path: Path, db: str) -> List[str]:
        """Construct mysqldump argv for a single database.

        The credentials are supplied using --defaults-extra-file=<path>. To
        avoid schema drift and locking issues, reasonable defaults are used:
        - --single-transaction for transactional dumps
        - --routines and --triggers to include all definitions
        - --events to include scheduled events
        - --hex-blob for binary data safety

        Args:
            cnf_path: Path to the temporary MySQL options file.
            db: Database name to dump.

        Returns:
            List[str]: Argument vector for mysqldump (safe for shell=False).
        """
        args: List[str] = [
            MYSQLDUMP_BINARY,
            f"--defaults-extra-file={cnf_path}",
            "--single-transaction",
            "--routines",
            "--triggers",
            "--events",
            "--hex-blob",
        ]
        # When socket is not configured, mysqldump could ignore
        # MySQLConnInfo.host/port, risking backups from the wrong endpoint.
        # These fix the potential error.
        if not self.conn.socket:
            args.extend(["--host", self.conn.host or "localhost"])
        if self.conn.port is not None:
            args.extend(["--port", str(self.conn.port)])
        args.append(db)
        return args
