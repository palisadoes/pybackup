"""Pull backup operations.

This module implements the "pull" workflow: retrieving backup files from
remote hosts to the local machine. It is designed to be orchestrated by the
CLI (bin/pybackup.py) and depends on utility helpers for logging, command
execution, cluster gating, and secure SSH command construction.

Typical usage example:
    from pathlib import Path
    from pybackup.operations.pull import PullManager

    hosts = [
        {
            "hostname": "backup1.example.com",
            "remote_username": "backup",
            "remote_directory": "/srv/backups/serverA",
            "local_directory": "/var/backups/serverA",
            "ssh_port": 22,
            "bwlimit": 40960,
            "ssh_key": "/root/.ssh/backup_rsa",
            "verify_host_keys": True,
            "scp": False,
        }
    ]

    result = PullManager().run(hosts, cluster_ip=None, continue_on_error=True)
    print("Succeeded:", result.succeeded)
    print("Failed:", result.failed)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pybackup.utils.cluster import check_cluster_master
from pybackup.utils.command import execute_command
from pybackup.utils.filesystem import ensure_directory
from pybackup.utils.logging_config import log_error, log_status, log_warning
from pybackup.utils.ssh import build_rsync_args, build_scp_args


@dataclass(frozen=True)
class PullResult:
    """Summary of a pull run across multiple hosts.

    Attributes:
      succeeded: List of hostnames for which pull completed successfully.
      failed: List of hostnames that failed to pull.
      details: Mapping from hostname to a short status message (e.g., error).
    """

    succeeded: List[str]
    failed: List[str]
    details: Dict[str, str]


class PullManager:
    """Orchestrates pulling backups from remote hosts to this machine.

    This manager iterates the host entries provided by configuration and uses
    rsync (preferred) or scp to copy files from each remote host into a local
    directory. It enforces safe, shell-less subprocess execution and provides
    cluster gating so that pulls can be restricted to a specific cluster node.

    The manager expects each host entry to include the following keys:
      - hostname (str)
      - remote_username (str)
      - remote_directory (str or Path)
      - local_directory (str or Path)

    Optional keys include:
      - ssh_port (int, default: 22)
      - bwlimit (int, KB/s, default: 40960; rsync only)
      - ssh_key (str or Path, identity file)
      - scp (bool, default: False) to force scp
      - verify_host_keys (bool, default: True)
      - known_hosts_file (str or Path, optional)

    The manager logs a concise summary of successes and failures at the end
    of the run.

    Attributes:
      None
    """

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(
        self,
        hosts: Sequence[Dict[str, Any]],
        cluster_ip: Optional[str] = None,
        continue_on_error: bool = True,
    ) -> PullResult:
        """Execute the pull workflow for a list of remote hosts.

        Args:
          hosts: Iterable of host dictionaries with the required fields
            (hostname, remote_username, remote_directory, local_directory) and
            optional SSH/transfer parameters.
          cluster_ip: Cluster virtual IP. When provided, the operation proceeds
            only on the node that currently owns this IP.
          continue_on_error: If True, failures on a host do not abort the
            entire run; the remaining hosts are attempted.

        Returns:
          PullResult: Structured summary of per-host results.

        Raises:
          RuntimeError: If continue_on_error is False and one or more
            hosts fail.
        """
        log_status("BK-PULL-START", "Starting pull run")

        # Cluster gating
        if not check_cluster_master(cluster_ip, mode_name="pull"):
            log_status("BK-PULL-SKIP", "Skipping pull: not cluster master")
            return PullResult(succeeded=[], failed=[], details={})

        succeeded: List[str] = []
        failed: List[str] = []
        details: Dict[str, str] = {}

        for host in hosts or []:
            hostname = _get_attr_or_key(host, "hostname", "<unknown>")
            try:
                self._process_host(host)
                succeeded.append(hostname)
                details[hostname] = "ok"
            except KeyboardInterrupt:
                log_warning(
                    "BK-PULL-INT", f"Interrupted while pulling from {hostname}"
                )
                failed.append(hostname)
                details[hostname] = "interrupted"
                if not continue_on_error:
                    raise
            except Exception as exc:
                log_error(
                    "BK-PULL-ERR", f"Failed pulling from {hostname}: {exc}"
                )
                failed.append(hostname)
                details[hostname] = str(exc)
                if not continue_on_error:
                    raise RuntimeError(
                        f"Pull aborted due to failure on {hostname}: {exc}"
                    ) from exc

        log_status(
            "BK-PULL-DONE",
            f"Pull run completed: ok={len(succeeded)} fail={len(failed)}",
        )
        return PullResult(succeeded=succeeded, failed=failed, details=details)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _process_host(self, host: Dict[str, Any]) -> None:
        """Pull from a single host using rsync (preferred) or scp.

            remote: <remote_username>@<hostname>:<remote_directory>/
            local : <local_directory>/

        Args:
          host: Host configuration dictionary.

        Returns:
          None

        Raises:
          ValueError: If required host fields are missing.
          RuntimeError: If the transfer command returns a non-zero status.
        """
        # Required fields
        hostname = _require(host, "hostname")
        remote_username = _require(host, "remote_username")
        remote_directory = Path(str(_require(host, "remote_directory")))
        local_directory = Path(str(_require(host, "local_directory")))

        # Optional fields
        ssh_port = int(_get_attr_or_key(host, "ssh_port", 22))
        bwlimit = int(_get_attr_or_key(host, "bwlimit", 40960))
        ssh_key = _maybe_str(_get_attr_or_key(host, "ssh_key", None))
        use_scp = bool(_get_attr_or_key(host, "scp", False))
        verify_host_keys = bool(
            _get_attr_or_key(host, "verify_host_keys", True)
        )
        known_hosts_file = _maybe_str(
            _get_attr_or_key(host, "known_hosts_file", None)
        )

        # Ensure local destination exists
        ensure_directory(local_directory)

        # Construct rsync/scp arguments
        src = f"""\
{remote_username}@{hostname}:{str(remote_directory).rstrip('/')}/"""
        dst = str(local_directory.resolve()).rstrip("/") + "/"

        if use_scp:
            argv = build_scp_args(
                source=src,
                destination=dst,
                identity_file=ssh_key,
                port=ssh_port,
                recursive=True,
                verify_host_keys=verify_host_keys,
                known_hosts_file=known_hosts_file,
            )
            tool = "scp"
        else:
            argv = build_rsync_args(
                source=src,
                destination=dst,
                identity_file=ssh_key,
                port=ssh_port,
                bwlimit_kbps=bwlimit,
                verify_host_keys=verify_host_keys,
                known_hosts_file=known_hosts_file,
            )
            tool = "rsync"

        log_status("BK-PULL-HOST", f"[{tool}] {hostname}: {src} -> {dst}")
        result = execute_command(argv)
        if result.returncode != 0:
            raise RuntimeError(f"""\
{tool} failed (code={result.returncode}): {result.stderr or result.stdout}""")

    # ------------------------------------------------------------------ #
    # Private helpers (module-level style but grouped here for clarity)
    # ------------------------------------------------------------------ #


# ---------------------------------------------------------------------- #
# Support functions
# ---------------------------------------------------------------------- #
def _get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    """Return an attribute or dict key from a configuration object.

    This utility supports typed configuration models (e.g., Pydantic) and
    plain dictionaries with identical field names.

    Args:
      obj: An arbitrary object or mapping containing configuration fields.
      name: Attribute or key name to read.
      default: Value to return when the field is not present.

    Returns:
      Any: The retrieved value or `default` if absent.
    """
    if obj is None:
        return default
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


def _require(obj: Dict[str, Any], key: str) -> Any:
    """Return a required key from a dict, raising if missing or empty.

    Args:
      obj: Mapping that must contain the key.
      key: Key to retrieve.

    Returns:
      Any: Value stored under the key.

    Raises:
      ValueError: If the key is missing or the value is empty/None.
    """
    if key not in obj or obj[key] in (None, ""):
        raise ValueError(
            f"Host configuration is missing required field: {key!r}"
        )
    return obj[key]


def _maybe_str(value: Any) -> Optional[str]:
    """Convert a value to str if not None.

    Args:
      value: Arbitrary value or None.

    Returns:
      Optional[str]: str(value) if value is not None; otherwise None.
    """
    if value is None:
        return None
    return str(value)
