"""Tests for pybackup.utils.mysql_credentials.

These tests verify that the context manager creates a 0600 file containing
the correct [client] stanza and removes it on exit.
"""

from __future__ import annotations

import os
from pathlib import Path

from pybackup.utils.mysql_credentials import (
    build_defaults_file_content,
    mysql_defaults_file,
)


def test_build_defaults_file_content_includes_fields() -> None:
    """Test builder includes required fields and optional socket line."""
    s = build_defaults_file_content("u", "p", "/tmp/sock")
    assert (
        "[client]" in s
        and "user=u" in s
        and "password=p" in s
        and "socket=/tmp/sock" in s
    )


def test_mysql_defaults_file_creates_and_removes(tmp_path) -> None:
    """Test context manager creates 0600 file and deletes it after exit."""
    with mysql_defaults_file("user", "pass", "/tmp/sock") as cnf_path:
        st = os.stat(cnf_path)
        assert st.st_mode & 0o777 == 0o600
        assert (
            Path(cnf_path).read_text(encoding="utf-8").startswith("[client]")
        )
    # After context, file should be gone
    assert not Path(cnf_path).exists()
