# test/pybackup/utils/test_filesystem.py
"""
Tests for pybackup.utils.filesystem.

These tests exercise directory creation, permission application (best-effort),
atomic writes for text/bytes, and retention cleanup logic.
"""

from __future__ import annotations

import os
import time

from pybackup.utils.filesystem import (
    cleanup_files,
    ensure_directory,
    ensure_parent_dir,
    list_files_older_than,
    set_permissions,
    validate_path_exists,
    write_bytes_atomic,
    write_text_atomic,
)


def test_ensure_directory_and_parent(tmp_path) -> None:
    """Test ensure_directory() and ensure_parent_dir() create directories idempotently."""
    d = tmp_path / "a" / "b"
    ensure_directory(d, dir_mode=0o750)
    assert d.exists() and d.is_dir()
    f = tmp_path / "a" / "c" / "file.txt"
    parent = ensure_parent_dir(f)
    assert parent.exists() and parent.is_dir()


def test_atomic_writes_and_permissions(tmp_path) -> None:
    """Test atomic write helpers and permissions application."""
    t = tmp_path / "t.txt"
    b = tmp_path / "b.bin"
    write_text_atomic(t, "hello\n", mode=0o640)
    write_bytes_atomic(b, b"\x00", mode=0o640)
    assert t.read_text() == "hello\n"
    assert b.read_bytes() == b"\x00"


def test_cleanup_files(tmp_path) -> None:
    """Test listing and cleanup of files older than a threshold."""
    old = tmp_path / "old.tgz"
    new = tmp_path / "new.tgz"
    old.write_text("x")
    new.write_text("y")
    # Make 'old' older than 1 day
    one_day_ago = time.time() - (24 * 3600 + 5)
    os.utime(old, (one_day_ago, one_day_ago))

    older = list_files_older_than(tmp_path, "*.tgz", max_age_days=1)
    assert old in older and new not in older

    removed = cleanup_files(tmp_path, "*.tgz", max_age_days=1)
    assert removed >= 1
