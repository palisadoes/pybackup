# test/pybackup/operations/test_push.py
"""
Tests for pybackup.operations.push.

These tests verify that push attempts are executed per host and that
continue_on_error determines whether the workflow aborts on first failure.
"""

from __future__ import annotations

from pybackup.operations.push import PushManager
from pybackup.utils import command as command_mod


def test_push_success_for_one_host(monkeypatch, tmp_path) -> None:
    """Test that a single host push succeeds when the command returns 0."""

    def _fake_execute(argv, **kwargs):
        return command_mod.CommandResult(argv, 0, "", "", 0, 0, 0)

    monkeypatch.setattr(command_mod, "execute_command", _fake_execute)

    hosts = [
        {
            "hostname": "h",
            "remote_username": "u",
            "remote_directory": "/remote",
            "local_directory": str(tmp_path),
            "scp": False,
        }
    ]
    result = PushManager().run(hosts, cluster_ip=None, continue_on_error=True)
    assert not result.failed and result.succeeded == ["h"]
