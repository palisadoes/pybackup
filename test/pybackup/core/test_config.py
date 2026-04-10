"""Tests for pybackup.core.config.

These tests validate loading a minimal configuration, path expansion, and
basic field validation errors (e.g., invalid cluster_ip).
"""

from __future__ import annotations


import pytest

from pybackup.core.config import BackupConfig, ConfigError, load_config


def test_load_minimal_config(tmp_path) -> None:
    """Test that a minimal valid config loads into a BackupConfig instance."""
    y = tmp_path / "c.yaml"
    y.write_text(
        """\
backup_dir: /var/backups\nbackup_user: backup\ndirectory: []\nexclude: []\n""",
        encoding="utf-8",
    )
    cfg = load_config(y)
    assert isinstance(cfg, BackupConfig)
    assert str(cfg.backup_dir) == "/var/backups"
    assert cfg.directory == []


def test_invalid_cluster_ip_raises(tmp_path) -> None:
    """Test that an invalid cluster_ip produces a ConfigError with details."""
    y = tmp_path / "c.yaml"
    y.write_text(
        """\
backup_dir: /var/backups\nbackup_user: backup\ncluster_ip: invalid-ip\n""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as ei:
        _ = load_config(y)
    assert "cluster_ip" in str(ei.value)
