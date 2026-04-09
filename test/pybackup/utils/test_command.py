"""Tests for pybackup.utils.command.

These tests verify that commands are executed without a shell, output is
captured, non-zero codes raise CommandError in check_call, and utilities like
quote_argv and which behave as expected.
"""

from __future__ import annotations

import subprocess

import pytest

from pybackup.utils import command as cmd


def test_execute_command_success(monkeypatch, fake_cmdresult) -> None:
    """Test execute_command() success using a subprocess.run monkeypatch."""

    def _fake_run(argv, **kwargs):
        # Ensure shell=False behavior
        # (no 'shell' key is passed when using .run direct)
        assert isinstance(argv, list)
        return fake_cmdresult(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    res = cmd.execute_command(["/bin/echo", "ok"])
    assert res.returncode == 0
    assert res.stdout.strip() == "ok"
    assert "echo" in cmd.quote_argv(["/bin/echo", "ok"])


def test_check_call_raises_on_failure(monkeypatch, fake_cmdresult) -> None:
    """Test that check_call() raises CommandError on non-zero exit."""

    def _fake_run(argv, **kwargs):
        return fake_cmdresult(returncode=2, stdout="", stderr="bad")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with pytest.raises(cmd.CommandError) as ei:
        _ = cmd.check_call(["/bin/false"])
    assert "exit=2" in str(ei.value)


def test_which(monkeypatch) -> None:
    """Test which() returns normalized string or None."""
    monkeypatch.setenv("PATH", "")
    assert cmd.which("python") is None
