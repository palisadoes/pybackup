# test/pybackup/utils/test_logging_config.py
"""
Tests for pybackup.utils.logging_config.

These tests configure logging to a temporary file and verify that log
entries include the expected code tag and formatting.
"""

from __future__ import annotations


from pybackup.utils.logging_config import get_logger, log_status, setup_logging


def test_setup_logging_and_write(tmp_path) -> None:
    """Test that setup_logging writes to a file and messages include code tags."""
    log_file = tmp_path / "pybackup.log"
    logger = setup_logging(
        log_file=str(log_file), level="DEBUG", console_output=False
    )
    log_status("BK-TEST", "hello")
    # Flush handlers to ensure contents are written
    for h in logger.handlers:
        if hasattr(h, "flush"):
            h.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "BK-TEST" in content and "hello" in content
