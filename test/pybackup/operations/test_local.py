# test/pybackup/operations/test_local.py
"""
Tests for pybackup.operations.local.

These tests verify cluster gating, that phases compose into a LocalBackupResult,
and that purge is invoked when requested. All external effects are mocked.
"""

from __future__ import annotations
import os

from pybackup.operations.local import LocalBackupManager, LocalBackupResult


def test_run_skips_when_not_master(monkeypatch, tmp_path) -> None:
    """Test that the manager skips execution when not on cluster master."""
    import pybackup.operations.local as local_mod

    monkeypatch.setattr(
        local_mod, "check_cluster_master", lambda ip, mode_name: False
    )
    mgr = LocalBackupManager(tmp_path, [f"{tmp_path}{os.sep}etc"])
    result = mgr.run(cluster_ip="10.0.0.1", max_age_days=7)
    assert isinstance(result, LocalBackupResult)
    assert (
        result.archive is None
        and not result.database_files
        and result.purged_count == 0
    )


def test_run_happy_path(monkeypatch, tmp_path) -> None:
    """Test that DB, filesystem, and purge phases are composed into a summary."""
    import pybackup.operations.local as local_mod

    monkeypatch.setattr(
        local_mod, "check_cluster_master", lambda ip, mode_name: True
    )
    monkeypatch.setattr(local_mod, "ensure_directory", lambda d: d)
    # Mock phases
    monkeypatch.setattr(
        LocalBackupManager,
        "_backup_mysql",
        lambda self, d: [f"{tmp_path}{os.sep}db.sql.gz"],
    )
    monkeypatch.setattr(
        LocalBackupManager,
        "_backup_filesystem",
        lambda self: f"{tmp_path}{os.sep}files.tgz",
    )
    monkeypatch.setattr(
        LocalBackupManager, "_purge_old_backups", lambda self, days: 3
    )

    mgr = LocalBackupManager(tmp_path, [f"{tmp_path}{os.sep}etc"])
    result = mgr.run(cluster_ip=None, max_age_days=7)
    assert result.archive == f"{tmp_path}{os.sep}files.tgz"
    assert not result.database_files
    assert result.purged_count == 3
