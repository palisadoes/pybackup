"""Push backup operations.

This module implements the "push" workflow: sending backup files from
the local machine to one or more remote hosts. It is designed to be
orchestrated by the CLI (bin/pybackup.py) and depends on utility helpers
for logging, command execution, cluster gating, and secure SSH command
construction.

Typical usage example:
    from pybackup.operations.push import PushManager

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

    result = PushManager().run(hosts, cluster_ip=None, continue_on_error=True)
    print("Succeeded:", result.succeeded)
    print("Failed:", result.failed)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pybackup.utils.cluster import check_cluster_master
from pybackup.utils.command import execute_command
from pybackup.utils.logging_config import log_error, log_status, log_warning
from pybackup.utils.ssh import build_rsync_args, build_scp_args


@dataclass(frozen=True)
class PushResult:
    """Summary of a push run across multiple hosts.

    Attributes:
      succeeded: List of hostnames for which the push completed successfully.
      failed: List of hostnames that failed to push.
      details: Mapping from hostname to a short status message
        (e.g., "ok" or an error).
    """

    succeeded: List[str]
    failed: List[str]
    details: Dict[str, str]


class PushManager:
    """Orchestrates pushing backups from this machine to remote hosts.

    This manager iterates the host entries provided by configuration and uses
    rsync (preferred) or scp to copy files from the local source directory to
    the remote destination directory on each host. It enforces safe, shell-less
    subprocess execution and provides cluster gating so that pushes can be
    restricted to a specific cluster node.

    Each host entry is expected to include:

      Required keys:
        - hostname (str)
        - remote_username (str)
        - remote_directory (str or Path)
        - local_directory (str or Path)

      Optional keys:
        - ssh_port (int, default: 22)
        - bwlimit (int, KB/s, default: 40960; rsync only)
        - ssh_key (str or Path, identity file)
        - scp (bool, default: False) to force scp instead of rsync
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
    ) -> PushResult:
        """Execute the push workflow for a list of remote hosts.

        Args:
          hosts: Iterable of host dictionaries with the required fields
            (hostname, remote_username, remote_directory, local_directory) and
            optional SSH/transfer parameters (see class docstring).
          cluster_ip: Cluster virtual IP. When provided, the operation proceeds
            only on the node that currently owns this IP.
          continue_on_error: If True, failures on a host do not abort the
            entire run; the remaining hosts are attempted.

        Returns:
          PushResult: Structured summary of per-host results.

        Raises:
          RuntimeError: If continue_on_error is False and one or
            more hosts fail.
        """
        log_status("BK-PUSH-START", "Starting push run")

        # Cluster gating.
        if not check_cluster_master(cluster_ip, mode_name="push"):
            log_status("BK-PUSH-SKIP", "Skipping push: not cluster master")
            return PushResult(succeeded=[], failed=[], details={})

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
                    "BK-PUSH-INT", f"Interrupted while pushing to {hostname}"
                )
                failed.append(hostname)
                details[hostname] = "interrupted"
                if not continue_on_error:
                    raise
            except Exception as exc:
                log_error(
                    "BK-PUSH-ERR", f"Failed pushing to {hostname}: {exc}"
                )
                failed.append(hostname)
                details[hostname] = str(exc)
                if not continue_on_error:
                    raise RuntimeError(
                        f"Push aborted due to failure on {hostname}: {exc}"
                    ) from exc

        log_status(
            "BK-PUSH-DONE",
            f"Push run completed: ok={len(succeeded)} fail={len(failed)}",
        )
        return PushResult(succeeded=succeeded, failed=failed, details=details)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _process_host(self, host: Dict[str, Any]) -> None:
        """Push to a single host using rsync (preferred) or scp.

        The source directory is treated as a content root: a trailing slash is
        applied so that files under the directory are copied rather than the
        directory itself.

        Local -> Remote path construction:
            local : <local_directory>/
            remote: <remote_username>@<hostname>:<remote_directory>/

        Args:
          host: Host configuration dictionary.

        Returns:
          None

        Raises:
          ValueError: If required host fields are missing.
          RuntimeError: If the transfer command returns a non-zero status.
        """
        # Required fields.
        hostname = _require(host, "hostname")
        remote_username = _require(host, "remote_username")
        remote_directory = Path(str(_require(host, "remote_directory")))
        local_directory = Path(str(_require(host, "local_directory")))

        # Optional fields.
        ssh_port = int(_get_attr_or_key(host, "ssh_port", 22))
        bwlimit = int(_get_attr_or_key(host, "bwlimit", 40960))  # KB/s
        ssh_key = _maybe_str(_get_attr_or_key(host, "ssh_key", None))
        use_scp = bool(_get_attr_or_key(host, "scp", False))
        verify_host_keys = bool(
            _get_attr_or_key(host, "verify_host_keys", True)
        )
        known_hosts_file = _maybe_str(
            _get_attr_or_key(host, "known_hosts_file", None)
        )

        # Validate local source exists (do not create).
        if not local_directory.exists() or not local_directory.is_dir():
            raise RuntimeError(
                f"Local source directory does not exist: {local_directory}"
            )

        # Ensure remote string is constructed (cannot ensure remote directory).
        src = str(local_directory.resolve()).rstrip("/") + "/"
        dst = f"""\
{remote_username}@{hostname}:{str(remote_directory).rstrip('/')}/"""

        # Build transfer argv.
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

        log_status("BK-PUSH-HOST", f"[{tool}] {src} -> {dst}")
        result = execute_command(argv)
        if result.returncode != 0:
            raise RuntimeError(
                f"""\
{tool} failed (code={result.returncode}): {result.stderr or result.stdout}"""
            )


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
