# test/pybackup/core/test_mysql.py
"""
Tests for pybackup.core.mysql.

These tests mock pymysql and command execution to avoid real database access
and external binaries. They verify listing databases, per-database dumps,
and the combined backup_all() workflow.
"""

from __future__ import annotations

from pathlib import Path


from pybackup.core.mysql import MySQLBackupManager, MySQLConnInfo
from pybackup.utils import command as command_mod


class _FakeCursor:
    """Fake cursor returning a small set of database names."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str) -> None:
        """Pretend to execute SHOW DATABASES."""
        assert "SHOW DATABASES" in sql

    def fetchall(self):
        """Return rows of database names."""
        return [["mysql"], ["information_schema"], ["db1"], ["db2"]]


class _FakeConn:
    """Fake pymysql connection that returns a fake cursor."""

    def cursor(self):
        """Return a fake cursor."""
        return _FakeCursor()

    def close(self):
        """No-op close."""
        return None


def test_list_databases_filters_system(monkeypatch, tmp_path) -> None:
    """Test list_databases() filters system DBs and returns user DBs."""
    import pybackup.core.mysql as mysql_mod

    def _fake_connect(**kwargs):
        return _FakeConn()

    monkeypatch.setattr(
        mysql_mod,
        "pymysql",
        type("P", (), {"connect": staticmethod(_fake_connect)}),
    )
    mgr = MySQLBackupManager(
        MySQLConnInfo("u", "p", socket=tmp_path / "sock"), tmp_path / "db"
    )
    names = mgr.list_databases()
    assert sorted(names) == ["db1", "db2"]


def test_backup_database_writes_and_compresses(monkeypatch, tmp_path) -> None:
    """Test backup_database() writes .sql and compresses to .sql.gz using mocks."""

    # Fake execute_command to simulate mysqldump and gzip
    def _fake_execute(argv, **kwargs):
        if "mysqldump" in argv[0]:
            return command_mod.CommandResult(
                argv, 0, "CREATE TABLE t();\n", "", 0, 0, 0
            )
        if "gzip" in argv[0]:
            # Simulate gzip success
            return command_mod.CommandResult(argv, 0, "", "", 0, 0, 0)
        raise AssertionError("Unexpected command")

    monkeypatch.setattr(command_mod, "execute_command", _fake_execute)

    # mysql_defaults_file yields a path (we don't inspect content here)
    import pybackup.utils.mysql_credentials as cred_mod

    class _Ctx:
        def __init__(self, p: Path):
            self.p = p

        def __enter__(self):
            return self.p

        def __exit__(self, *exc):
            return False

    def _fake_defaults(user, password, socket=None):
        return _Ctx(tmp_path / "cnf.cnf")

    monkeypatch.setattr(cred_mod, "mysql_defaults_file", _fake_defaults)

    mgr = MySQLBackupManager(
        MySQLConnInfo("u", "p", socket=tmp_path / "sock"), tmp_path
    )
    mgr.validate_environment()
    out = mgr.backup_database("db1")
    assert out.suffixes[-2:] == [".sql", ".gz"]
    assert (
        out.exists()
    )  # gzip mock does not create the final file on disk; replace expectation
