# pybackup/utils/cluster.py
"""
Cluster-awareness utilities.

This module provides helpers to:
- Discover local interface IP addresses (IPv4 and IPv6).
- Decide whether the current node should run a cluster-guarded operation
  based on ownership of a configured "cluster IP".
- Emit consistent, structured log messages for allow/deny decisions.

It is intentionally dependency-tolerant:
- If psutil is available, it is used to enumerate interface addresses.
- Otherwise, a stdlib-only fallback is used (socket.getaddrinfo).

Typical usage example:
    from pybackup.utils.cluster import check_cluster_master

    if not check_cluster_master(cluster_ip="10.0.0.10", mode_name="local"):
        return  # skip work on non-master nodes
"""
from __future__ import annotations

import ipaddress
import socket
from typing import List, Optional, Set

# Optional dependency: psutil for robust interface enumeration.
try:  # pragma: no cover - availability is environment-dependent
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from pybackup.utils.logging_config import log_status, log_warning


def get_local_ips(
    include_loopback: bool = False, ipv6: bool = False
) -> List[str]:
    """Return a list of local interface IP addresses.

    This function enumerates local interface addresses and returns either
    IPv4 or IPv6 addresses. When psutil is available it is used; otherwise
    a stdlib-only fallback (socket.getaddrinfo) provides best-effort results.

    Args:
      include_loopback: If True, include loopback addresses (e.g., 127.0.0.1, ::1).
      ipv6: If True, return IPv6 addresses; otherwise return IPv4 addresses.

    Returns:
      List[str]: A list of IP address strings present on this host.

    Notes:
      - IPv6 addresses that include a scope ID (e.g., "fe80::abcd%eth0") are
        normalized to strip the scope (result: "fe80::abcd").
      - The fallback without psutil may not enumerate every address on all
        platforms, but remains useful for common cases.
    """
    family = socket.AF_INET6 if ipv6 else socket.AF_INET
    addrs: Set[str] = set()

    if psutil is not None:  # Preferred path
        try:
            for infos in psutil.net_if_addrs().values():  # type: ignore[attr-defined]
                for info in infos:
                    if getattr(info, "family", None) == family:
                        addr = _strip_scope_id(
                            getattr(info, "address", "") or ""
                        )
                        if addr:
                            addrs.add(addr)
        except Exception:
            # Fall back to stdlib if psutil raises unexpectedly.
            addrs.update(_get_local_ips_stdlib(family))
    else:
        addrs.update(_get_local_ips_stdlib(family))

    # Optionally filter loopback
    if not include_loopback:
        if ipv6:
            addrs.discard("::1")
        else:
            addrs.discard("127.0.0.1")

    return sorted(addrs)


def cluster_master(cluster_ip: Optional[str]) -> bool:
    """Return True if this node should be considered the cluster master.

    The logic is:
      - If cluster_ip is None or empty, return True (no gating configured).
      - If cluster_ip is not a valid IP address, log a warning and return True
        (fail-open to avoid unexpectedly disabling operations).
      - Otherwise, return True if cluster_ip is present on a local interface.

    Args:
      cluster_ip: The IPv4/IPv6 address designating the active/master node.

    Returns:
      bool: True if the operation should run on this node, False otherwise.
    """
    if not cluster_ip or str(cluster_ip).strip() == "":
        return True

    ip_text = str(cluster_ip).strip()
    try:
        ip_obj = ipaddress.ip_address(ip_text)
    except Exception:
        log_warning(
            "BK-CLSTR-IP",
            f"Invalid cluster_ip '{cluster_ip}'; proceeding as not gated",
        )
        return True

    if ip_obj.version == 6:
        return ip_text in get_local_ips(include_loopback=True, ipv6=True)
    return ip_text in get_local_ips(include_loopback=True, ipv6=False)


def check_cluster_master(cluster_ip: Optional[str], mode_name: str) -> bool:
    """Gate a mode's execution based on cluster master ownership.

    If a cluster IP is configured and is not present on a local interface,
    this function emits a standardized log message and returns False so the
    caller can skip work gracefully.

    Args:
      cluster_ip: The IPv4/IPv6 address designating the active/master node,
        or None/empty for no gating.
      mode_name: A short label for the calling mode (e.g., "local", "push", "pull").

    Returns:
      bool: True if work should proceed on this node; False to skip.

    Examples:
      >>> if not check_cluster_master("10.0.0.10", "local"):
      ...     return  # skip
    """
    allowed = cluster_master(cluster_ip)
    if not allowed:
        log_status(
            "BK-CLSTR-SKIP", f"{mode_name}: skipping; not on cluster master"
        )
    return allowed


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _strip_scope_id(addr: str) -> str:
    """Strip an IPv6 scope ID (zone index) if present.

    Args:
      addr: IPv6 address string, potentially containing a scope (e.g., "%eth0").

    Returns:
      str: Address without the scope suffix.
    """
    if "%" in addr:
        return addr.split("%", 1)[0]
    return addr


def _get_local_ips_stdlib(family: int) -> Set[str]:
    """Best-effort local IP enumeration using only the Python standard library.

    This function uses socket.getaddrinfo on the current hostname to collect
    addresses for the requested family. It is not as comprehensive as psutil
    but works on many systems.

    Args:
      family: socket.AF_INET for IPv4 or socket.AF_INET6 for IPv6.

    Returns:
      Set[str]: A set of discovered IP address strings (scope-stripped for IPv6).
    """
    addrs: Set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(
            hostname, None, family=family, proto=socket.IPPROTO_TCP
        ):
            sockaddr = info[4]
            if family == socket.AF_INET6:
                ip = _strip_scope_id(sockaddr[0])
            else:
                ip = sockaddr[0]
            if ip:
                addrs.add(ip)
    except Exception:
        # As a last resort, add loopbacks so the caller has some answer.
        if family == socket.AF_INET6:
            addrs.add("::1")
        else:
            addrs.add("127.0.0.1")
    return addrs


__all__ = [
    "get_local_ips",
    "cluster_master",
    "check_cluster_master",
]
