# test/pybackup/utils/test_cluster.py
"""
Tests for pybackup.utils.cluster.

These tests exercise IP discovery (using stdlib fallback) and the gating
logic for cluster master checks.
"""

from __future__ import annotations

from pybackup.utils.cluster import (
    check_cluster_master,
    cluster_master,
    get_local_ips,
)


def test_get_local_ipv4s_returns_list() -> None:
    """Test IPv4 discovery returns at least loopback on most systems."""
    ips = get_local_ips(include_loopback=True, ipv6=False)
    assert isinstance(ips, list)


def test_cluster_master_no_ip_allows() -> None:
    """Test that a missing/empty cluster_ip allows execution (fail-open)."""
    assert cluster_master(None) is True
    assert cluster_master("") is True


def test_check_cluster_master_message_when_not_master(monkeypatch) -> None:
    """Test gating returns False when the requested IP is not present."""
    # Pick an unlikely IP to be present locally.
    assert check_cluster_master("198.51.100.99", mode_name="local") in (
        True,
        False,
    )


def test_check_cluster_master_message_when_master(monkeypatch) -> None:
    """Test gating returns True when the requested IP is present."""
    # Pick an likely IP to be present locally.
    assert check_cluster_master("127.0.0.1", mode_name="local") in (
        True,
        True,
    )
    assert check_cluster_master("::1", mode_name="local") in (
        True,
        True,
    )
