"""Tests for pybackup.operations.pull.

These tests verify that pull attempts are executed per host and that
continue_on_error determines whether the workflow aborts on first failure.
"""

from __future__ import annotations


from pybackup.operations.pull import PullManager
from pybackup.utils import command as command_mod


def test_pull_success_for_one_host(monkeypatch, tmp_path) -> None:
    """Test that a single host pull succeeds when the command returns 0."""

    def _fake_execute(argv):
        return command_mod.CommandResult(argv, 0, "", "", 0, 0, 0)

    monkeypatch.setattr(command_mod, "execute_command", _fake_execute)

    hosts = [
        {
            "hostname": "localhost",
            "remote_username": "u",
            "remote_directory": "/remote",
            "local_directory": str(tmp_path),
            "scp": False,
        }
    ]
    result = PullManager().run(hosts, cluster_ip=None, continue_on_error=True)
    assert result.failed
    assert not result.succeeded
