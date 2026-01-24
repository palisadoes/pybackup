# pybackup/utils/ssh.py

"""
SSH/transfer command builders.

This module centralizes the construction of safe argument vectors for rsync and
scp without using a shell. It also provides a helper to assemble a consistent
set of SSH options (identity file, port, host-key verification, known_hosts).

Key design notes:
- All subprocess calls should use shell=False with the argv lists returned here.
- For rsync, the --rsh argument must be a single string describing the SSH
  command; we quote each token using shlex.quote so rsync’s internal shell can
  parse it safely when it spawns the remote shell.
- For scp, we pass arguments as a normal argv list; no shell is involved.

Typical usage example:
    from pybackup.utils.ssh import build_rsync_args, build_scp_args
    from pybackup.utils.command import execute_command

    argv = build_rsync_args(
        source="/var/backups/",
        destination="backup@example.com:/srv/backups/",
        identity_file="/root/.ssh/backup_rsa",
        port=22,
        bwlimit_kbps=40960,
        verify_host_keys=True,
        known_hosts_file="/root/.ssh/known_hosts",
        verbose=True,
        progress=True,
    )
    result = execute_command(argv)
"""
from __future__ import annotations

import shlex
from typing import Iterable, List, Optional, Sequence

from pybackup.constants import RSYNC_BINARY, SCP_BINARY


def build_ssh_base_args(
    identity_file: Optional[str],
    port: int = 22,
    verify_host_keys: bool = True,
    known_hosts_file: Optional[str] = None,
    connect_timeout: Optional[int] = 10,
    extra: Optional[Sequence[str]] = None,
) -> List[str]:
    """Build a base SSH command argument list.

    The returned list is suitable for:
    - Passing to rsync via --rsh after quoting (see build_rsync_args).
    - Passing to scp directly when combined with scp-specific options.

    Args:
      identity_file: Optional path to a private key file passed via -i.
      port: SSH port (1..65535).
      verify_host_keys: If False, host key verification is disabled using
        -o UserKnownHostsFile=/dev/null and -o StrictHostKeyChecking=no.
      known_hosts_file: Optional path to a known_hosts file. Only applied when
        verify_host_keys is True.
      connect_timeout: Optional connection timeout (seconds). If None, not set.
      extra: Optional additional raw -o options or other ssh flags (e.g.,
        ["-o", "KexAlgorithms=..."]). Each element is treated as an argument
        token, not a single string with spaces.

    Returns:
      List[str]: SSH argument vector beginning with "ssh".

    Raises:
      ValueError: If the port is outside the range 1..65535.

    Examples:
      Basic:
        >>> build_ssh_base_args("/root/.ssh/id_rsa", port=22)
        ['ssh', '-p', '22', '-i', '/root/.ssh/id_rsa']

      With host key verification disabled:
        >>> build_ssh_base_args(None, verify_host_keys=False)
        ['ssh', '-p', '22', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'StrictHostKeyChecking=no']
    """
    _validate_port(port)
    args: List[str] = ["ssh", "-p", str(port)]

    if identity_file:
        args += ["-i", identity_file]

    if verify_host_keys:
        if known_hosts_file:
            args += ["-o", f"UserKnownHostsFile={known_hosts_file}"]
        # Default StrictHostKeyChecking is secure enough (ask/yes via user config).
    else:
        # Disable known_hosts usage and host-key checking explicitly (less secure).
        args += [
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "StrictHostKeyChecking=no",
        ]

    if connect_timeout is not None:
        args += ["-o", f"ConnectTimeout={int(connect_timeout)}"]

    if extra:
        args += list(extra)

    return args


def build_rsync_args(
    source: str,
    destination: str,
    identity_file: Optional[str],
    port: int,
    bwlimit_kbps: int,
    verify_host_keys: bool = True,
    known_hosts_file: Optional[str] = None,
    extra: Optional[Sequence[str]] = None,
    archive: bool = True,
    compress: bool = True,
    delete: bool = False,
    relative: bool = False,
    excludes: Optional[Iterable[str]] = None,
    verbose: bool = False,
    progress: bool = False,
) -> List[str]:
    """Build a safe rsync command argument list.

    The returned list uses --rsh with a single, quoted SSH command string. This
    ensures rsync can correctly invoke the remote shell without the caller
    using a system shell.

    Args:
      source: Source path (local or remote) with trailing slash semantics
        handled by the caller. For directories, trailing "/" usually indicates
        “copy the contents” rather than the directory itself.
      destination: Destination path (local or remote).
      identity_file: Optional path to a private key file for SSH.
      port: SSH port (1..65535).
      bwlimit_kbps: Bandwidth limit in KB/s for rsync. Must be > 0.
      verify_host_keys: If False, host key verification is disabled.
      known_hosts_file: Optional path to known_hosts when verification is on.
      extra: Optional additional raw SSH args appended to the SSH command used
        by rsync --rsh (e.g., ["-o", "KexAlgorithms=..."]).
      archive: If True, enable --archive (preserve metadata).
      compress: If True, enable --compress.
      delete: If True, delete extraneous files from destination dirs.
      relative: If True, enable --relative path handling.
      excludes: Optional iterable of patterns passed as repeated --exclude.
      verbose: If True, enable -v.
      progress: If True, enable --progress (useful for large transfers).

    Returns:
      List[str]: rsync argv suitable for subprocess with shell=False.

    Raises:
      ValueError: If the port is invalid or bwlimit_kbps <= 0.

    Examples:
      >>> argv = build_rsync_args(
      ...     "/var/backups/", "user@host:/srv/backup/",
      ...     identity_file="/root/.ssh/id_rsa",
      ...     port=22,
      ...     bwlimit_kbps=40960,
      ...     verify_host_keys=True,
      ... )
      >>> argv[0]
      '/usr/bin/rsync'
    """
    _validate_port(port)
    if bwlimit_kbps <= 0:
        raise ValueError("bwlimit_kbps must be a positive integer")

    ssh_tokens = build_ssh_base_args(
        identity_file=identity_file,
        port=port,
        verify_host_keys=verify_host_keys,
        known_hosts_file=known_hosts_file,
        extra=extra,
    )
    rsh_value = _quote_for_rsync_rsh(ssh_tokens)

    args: List[str] = [RSYNC_BINARY, f"--bwlimit={bwlimit_kbps}"]

    if archive:
        args.append("--archive")
    if compress:
        args.append("--compress")
    if delete:
        args.append("--delete")
    if relative:
        args.append("--relative")
    if verbose:
        args.append("--verbose")
    if progress:
        args.append("--progress")

    if excludes:
        for pat in excludes:
            args += ["--exclude", str(pat)]

    args += ["--rsh", rsh_value, source, destination]
    return args


def build_scp_args(
    source: str,
    destination: str,
    identity_file: Optional[str],
    port: int,
    recursive: bool = True,
    verify_host_keys: bool = True,
    known_hosts_file: Optional[str] = None,
    preserve_times: bool = True,
    compress: bool = False,
    extra: Optional[Sequence[str]] = None,
) -> List[str]:
    """Build a safe scp command argument list.

    Args:
      source: Source path (local or remote). For directories, recursive should
        be True to copy contents.
      destination: Destination path (local or remote).
      identity_file: Optional path to a private key file for SSH.
      port: SSH port (1..65535).
      recursive: If True, pass -r to scp.
      verify_host_keys: If False, host key verification is disabled.
      known_hosts_file: Optional path to known_hosts when verification is on.
      preserve_times: If True, pass -p to preserve times.
      compress: If True, pass -C to enable compression (not always beneficial).
      extra: Optional additional raw SSH args appended as -o or other flags.

    Returns:
      List[str]: scp argv suitable for subprocess with shell=False.

    Raises:
      ValueError: If the port is invalid.

    Examples:
      >>> build_scp_args("/var/backups/", "user@host:/srv/backup/", None, 22)[:2]
      ['/usr/bin/scp', '-P']
    """
    _validate_port(port)

    args: List[str] = [SCP_BINARY, "-P", str(port)]
    if recursive:
        args.append("-r")
    if compress:
        args.append("-C")
    if preserve_times:
        args.append("-p")
    if identity_file:
        args += ["-i", identity_file]

    # Host key policy
    if verify_host_keys:
        if known_hosts_file:
            args += ["-o", f"UserKnownHostsFile={known_hosts_file}"]
    else:
        args += [
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "StrictHostKeyChecking=no",
        ]

    if extra:
        args += list(extra)

    args += [source, destination]
    return args


def get_transfer_method(host_config: dict) -> str:
    """Return the transfer method name for a host configuration.

    This helper centralizes the convention that a host config key "scp"
    indicates that scp should be used; otherwise rsync is preferred.

    Args:
      host_config: Host configuration mapping.

    Returns:
      str: "scp" when host_config.get("scp") is truthy; "rsync" otherwise.

    Examples:
      >>> get_transfer_method({"scp": True})
      'scp'
      >>> get_transfer_method({})
      'rsync'
    """
    return "scp" if bool(host_config.get("scp")) else "rsync"


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _validate_port(port: int) -> None:
    """Validate an SSH port number.

    Args:
      port: Port value to validate.

    Raises:
      ValueError: If the port is outside 1..65535.
    """
    if not 1 <= int(port) <= 65535:
        raise ValueError("port must be between 1 and 65535")


def _quote_for_rsync_rsh(tokens: Sequence[str]) -> str:
    """Quote SSH tokens for inclusion in rsync's --rsh string.

    rsync expects --rsh to be a single string describing the remote shell
    command. Since rsync typically invokes a shell to run that command,
    each token is individually quoted to avoid injection via spaces or
    metacharacters.

    Args:
      tokens: Sequence of SSH tokens (e.g., ['ssh', '-p', '22', '-i', '/path']).

    Returns:
      str: A single safely-quoted string suitable for --rsh.

    Examples:
      >>> _quote_for_rsync_rsh(['ssh', '-p', '22', '-i', '/path with space'])
      "ssh -p '22' -i '/path with space'"
    """
    return " ".join(shlex.quote(t) for t in tokens)


__all__ = [
    "build_ssh_base_args",
    "build_rsync_args",
    "build_scp_args",
    "get_transfer_method",
]
