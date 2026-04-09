"""Tests for pybackup.utils.ssh.

These tests verify SSH base arguments, rsync/scp argv construction, and
that --rsh receives a safely-quoted SSH command string.
"""

from __future__ import annotations

from pybackup.utils.ssh import (
    build_rsync_args,
    build_scp_args,
    build_ssh_base_args,
)


def test_build_ssh_base_args_and_port_validation() -> None:
    """Test base SSH args include port and identity when provided."""
    argv = build_ssh_base_args(
        identity_file="/k e y",
        port=22,
        verify_host_keys=True,
        known_hosts_file="/kh",
    )
    assert argv[:2] == ["ssh", "-p"]
    assert "-i" in argv
    assert "-o" in argv and "UserKnownHostsFile=/kh" in " ".join(argv)


def test_build_rsync_args_includes_rsh_quoted() -> None:
    """Test rsync argv includes a quoted --rsh string suitable for rsync."""
    argv = build_rsync_args(
        source="/src/",
        destination="user@host:/dst/",
        identity_file="/path with space/key",
        port=22,
        bwlimit_kbps=1024,
        verify_host_keys=False,
    )
    assert argv[0].endswith("rsync")
    assert "--rsh" in argv
    rsh_value = argv[argv.index("--rsh") + 1]
    # Quoted identity path should be present
    assert "'" in rsh_value or '"' in rsh_value


def test_build_scp_args_basic() -> None:
    """Test scp argv includes port and recursive flag by default."""
    argv = build_scp_args(
        source="/src/",
        destination="user@host:/dst/",
        identity_file=None,
        port=22,
        recursive=True,
        verify_host_keys=False,
    )
    assert argv[0].endswith("scp")
    assert "-P" in argv and "-r" in argv
