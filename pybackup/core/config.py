# pybackup/core/config.py
from __future__ import annotations

"""
pybackup.core.config

Configuration models and loader for pybackup.

This module provides:
- Strongly-typed Pydantic models for configuration sections
  (HostConfig, MySQLConfig, BackupConfig).
- A single entry point (load_config) to parse a YAML file into a validated
  BackupConfig instance.
- Compatibility with Pydantic v2 (preferred) and a v1 fallback path.
- Helpful, aggregated error messages for invalid configurations.
- Path pre-processing that expands ~ and environment variables.

Example:
    >>> from pathlib import Path
    >>> from pybackup.core.config import load_config
    >>> cfg = load_config(Path("/etc/pybackup.yaml"))
    >>> cfg.backup_dir
    PosixPath('/var/backups')
"""

import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml  # Requires PyYAML at runtime

# Optional defaults from pybackup.constants with safe fallbacks.
try:
    from pybackup.constants import DEFAULT_BANDWIDTH_LIMIT as _DEFAULT_BWLIMIT
except Exception:
    _DEFAULT_BWLIMIT = 40960  # KB/s (~40 MB/s)

try:
    from pybackup.constants import (
        DEFAULT_MYSQL_DAEMON as _DEFAULT_MYSQL_DAEMON,
    )
except Exception:
    _DEFAULT_MYSQL_DAEMON = "mysqld"

try:
    from pybackup.constants import DEFAULT_LOG_PATH as _DEFAULT_LOG_PATH
except Exception:
    _DEFAULT_LOG_PATH = "/var/log/backups/backups.log"


# Prefer Pydantic v2; fall back to a v1-compatible surface if v2 is unavailable.
try:  # Pydantic v2
    from pydantic import (
        BaseModel,
        Field,
        ValidationError,
        field_validator,
        model_validator,
    )

    _PYD_V2 = True
except Exception:  # Pydantic v1
    from pydantic import BaseModel, Field, ValidationError  # type: ignore
    from pydantic import validator as field_validator  # type: ignore
    from pydantic import root_validator as model_validator  # type: ignore

    _PYD_V2 = False


class ConfigError(Exception):
    """Raised when configuration loading or validation fails."""


_PathLike = Union[str, Path]
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-\.]{0,252}$")


def _expand_path(value: Optional[_PathLike]) -> Optional[Path]:
    """Expand ~ and environment variables for a path-like input.

    Args:
      value: String or Path to expand, or None.

    Returns:
      Path: A Path with ~ and environment variables expanded; or None if the
      input is None or becomes empty after stripping.

    Notes:
      This function does not verify the resulting path exists. Resolution is
      intentionally non-strict to allow provisioning flows.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return Path(os.path.expandvars(os.path.expanduser(s)))


def _valid_hostname(hostname: str) -> bool:
    """Perform a basic, RFC-like sanity check for hostnames.

    This permits alphanumerics, dashes, and dots, and enforces a maximum
    overall length. It is intentionally conservative and not a full RFC 1123
    validator, but is sufficient to catch obvious errors.

    Args:
      hostname: Hostname string to validate.

    Returns:
      bool: True if the hostname passes the basic pattern; False otherwise.
    """
    if not hostname or len(hostname) > 253:
        return False
    return bool(_HOSTNAME_RE.match(hostname))


class HostConfig(BaseModel):
    """Per-host configuration for push/pull operations.

    Attributes:
      hostname: Remote DNS name or IP literal (basic format validated).
      remote_username: Username to use over SSH/SCP/rsync.
      remote_directory: Destination (push) or source (pull) directory on the remote host.
      local_directory: Source (push) or destination (pull) directory on this host.
      ssh_port: SSH port (1..65535). Defaults to 22.
      bwlimit: Bandwidth limit in KB/s for rsync. Defaults to ~40 MB/s.
      ssh_key: Optional identity file path; overrides global ssh_key when set.
      scp: If True, use scp for transfer (otherwise prefer rsync).
      verify_host_keys: Enforce host-key verification when True (recommended).
      known_hosts_file: Optional custom known_hosts file path.
    """

    hostname: str
    remote_username: str
    remote_directory: Path
    local_directory: Path

    ssh_port: int = 22
    bwlimit: int = _DEFAULT_BWLIMIT

    ssh_key: Optional[Path] = None
    scp: bool = False

    verify_host_keys: bool = True
    known_hosts_file: Optional[Path] = None

    if _PYD_V2:

        @field_validator(
            "remote_directory",
            "local_directory",
            "ssh_key",
            "known_hosts_file",
            mode="before",
        )
        @classmethod
        def _expand_paths_v2(cls, v: Any) -> Any:
            """Pydantic v2 pre-validation path expander.

            Args:
              v: Field value to expand.

            Returns:
              Any: Expanded path, or the original value if not applicable.
            """
            return _expand_path(v)

    else:

        @field_validator(
            "remote_directory",
            "local_directory",
            "ssh_key",
            "known_hosts_file",
            pre=True,
        )
        def _expand_paths_v1(cls, v: Any) -> Any:  # type: ignore[override]
            """Pydantic v1 pre-validation path expander.

            Args:
              v: Field value to expand.

            Returns:
              Any: Expanded path, or the original value if not applicable.
            """
            return _expand_path(v)

    if _PYD_V2:

        @field_validator("ssh_port")
        @classmethod
        def _port_range_v2(cls, v: int) -> int:
            """Validate that ssh_port falls within 1..65535.

            Args:
              v: Proposed SSH port value.

            Returns:
              int: Validated port.

            Raises:
              ValueError: If the port is outside the valid range.
            """
            if not (1 <= v <= 65535):
                raise ValueError("ssh_port must be between 1 and 65535")
            return v

        @field_validator("bwlimit")
        @classmethod
        def _bwlimit_positive_v2(cls, v: int) -> int:
            """Validate that bwlimit is a positive integer (KB/s).

            Args:
              v: Proposed bandwidth limit.

            Returns:
              int: Validated, positive limit.

            Raises:
              ValueError: If the value is not positive.
            """
            if v <= 0:
                raise ValueError("bwlimit must be a positive integer (KB/s)")
            return v

    else:

        @field_validator("ssh_port")
        def _port_range_v1(cls, v: int) -> int:  # type: ignore[override]
            """Validate that ssh_port falls within 1..65535.

            Args:
              v: Proposed SSH port value.

            Returns:
              int: Validated port.

            Raises:
              ValueError: If the port is outside the valid range.
            """
            if not (1 <= v <= 65535):
                raise ValueError("ssh_port must be between 1 and 65535")
            return v

        @field_validator("bwlimit")
        def _bwlimit_positive_v1(cls, v: int) -> int:  # type: ignore[override]
            """Validate that bwlimit is a positive integer (KB/s).

            Args:
              v: Proposed bandwidth limit.

            Returns:
              int: Validated, positive limit.

            Raises:
              ValueError: If the value is not positive.
            """
            if v <= 0:
                raise ValueError("bwlimit must be a positive integer (KB/s)")
            return v

    if _PYD_V2:

        @field_validator("hostname")
        @classmethod
        def _hostname_ok_v2(cls, v: str) -> str:
            """Basic sanity check for the hostname field.

            Args:
              v: Proposed hostname.

            Returns:
              str: Validated hostname.

            Raises:
              ValueError: If the hostname fails the format check.
            """
            if not _valid_hostname(v):
                raise ValueError(f"Invalid hostname: {v!r}")
            return v

    else:

        @field_validator("hostname")
        def _hostname_ok_v1(cls, v: str) -> str:  # type: ignore[override]
            """Basic sanity check for the hostname field.

            Args:
              v: Proposed hostname.

            Returns:
              str: Validated hostname.

            Raises:
              ValueError: If the hostname fails the format check.
            """
            if not _valid_hostname(v):
                raise ValueError(f"Invalid hostname: {v!r}")
            return v


class MySQLConfig(BaseModel):
    """MySQL/MariaDB backup settings.

    Attributes:
      user: Database user used for discovery and dump operations.
      password: Password for the database user (callers must handle securely).
      socket: Optional UNIX-domain socket path for local daemon.
      daemon_name: Name of the DB daemon (e.g., "mysqld" or "mariadbd").
    """

    user: str
    password: str
    socket: Optional[Path] = None
    daemon_name: str = _DEFAULT_MYSQL_DAEMON

    if _PYD_V2:

        @field_validator("socket", mode="before")
        @classmethod
        def _expand_sock_v2(cls, v: Any) -> Any:
            """Pydantic v2 pre-validation path expander for the socket path.

            Args:
              v: Field value to expand.

            Returns:
              Any: Expanded path, or the original value if not applicable.
            """
            return _expand_path(v)

        @field_validator("user", "password")
        @classmethod
        def _not_empty_v2(cls, v: str) -> str:
            """Ensure essential credential fields are not empty.

            Args:
              v: Proposed string value.

            Returns:
              str: Validated, non-empty string.

            Raises:
              ValueError: If the string is empty or whitespace only.
            """
            if not str(v).strip():
                raise ValueError("must not be empty")
            return v

    else:

        @field_validator("socket", pre=True)
        def _expand_sock_v1(cls, v: Any) -> Any:  # type: ignore[override]
            """Pydantic v1 pre-validation path expander for the socket path.

            Args:
              v: Field value to expand.

            Returns:
              Any: Expanded path, or the original value if not applicable.
            """
            return _expand_path(v)

        @field_validator("user", "password")
        def _not_empty_v1(cls, v: str) -> str:  # type: ignore[override]
            """Ensure essential credential fields are not empty.

            Args:
              v: Proposed string value.

            Returns:
              str: Validated, non-empty string.

            Raises:
              ValueError: If the string is empty or whitespace only.
            """
            if not str(v).strip():
                raise ValueError("must not be empty")
            return v


class BackupConfig(BaseModel):
    """Top-level backup configuration.

    Attributes:
      backup_dir: Local directory where backup artifacts are written.
      backup_user: OS user that should own or be associated with artifacts.
      directory: Directories included in filesystem backups (expanded paths).
      exclude: Patterns to exclude (e.g., "*.log", "/home/*/.cache").
      cluster_ip: Optional IP address; when present, operations that should
        run only on the cluster master must verify the local node owns this
        address.
      ssh_key: Optional global SSH identity file; per-host can override.
      push_servers: List of HostConfig entries for push operations.
      pull_servers: List of HostConfig entries for pull operations.
      mysql: Optional MySQLConfig for database backup.
      log_file: Optional global log file path override.
    """

    backup_dir: Path
    backup_user: str

    directory: List[Path] = Field(default_factory=list)
    exclude: List[str] = Field(default_factory=list)

    cluster_ip: Optional[str] = None
    ssh_key: Optional[Path] = None

    push_servers: List[HostConfig] = Field(default_factory=list)
    pull_servers: List[HostConfig] = Field(default_factory=list)

    mysql: Optional[MySQLConfig] = None
    log_file: Optional[Path] = Field(default=_DEFAULT_LOG_PATH)

    if _PYD_V2:

        @field_validator("backup_dir", "ssh_key", "log_file", mode="before")
        @classmethod
        def _expand_paths_v2(cls, v: Any) -> Any:
            """Pydantic v2 pre-validation path expander for top-level paths.

            Args:
              v: Field value to expand.

            Returns:
              Any: Expanded path, or the original value if not applicable.
            """
            return _expand_path(v)

        @field_validator("directory", mode="before")
        @classmethod
        def _expand_dir_list_v2(cls, v: Any) -> Any:
            """Normalize 'directory' to a list and expand each entry.

            Args:
              v: A single path-like or a list of path-like entries.

            Returns:
              list: List of expanded Path objects (None entries removed later).
            """
            if v is None:
                return []
            if isinstance(v, (str, Path)):
                v = [v]
            return [_expand_path(i) for i in v]

    else:

        @field_validator("backup_dir", "ssh_key", "log_file", pre=True)
        def _expand_paths_v1(cls, v: Any) -> Any:  # type: ignore[override]
            """Pydantic v1 pre-validation path expander for top-level paths.

            Args:
              v: Field value to expand.

            Returns:
              Any: Expanded path, or the original value if not applicable.
            """
            return _expand_path(v)

        @field_validator("directory", pre=True)
        def _expand_dir_list_v1(cls, v: Any) -> Any:  # type: ignore[override]
            """Normalize 'directory' to a list and expand each entry.

            Args:
              v: A single path-like or a list of path-like entries.

            Returns:
              list: List of expanded Path objects (None entries removed later).
            """
            if v is None:
                return []
            if isinstance(v, (str, Path)):
                v = [v]
            return [_expand_path(i) for i in v]

    if _PYD_V2:

        @field_validator("backup_user")
        @classmethod
        def _user_not_empty_v2(cls, v: str) -> str:
            """Ensure backup_user is not empty or whitespace-only.

            Args:
              v: Proposed username.

            Returns:
              str: Validated username.

            Raises:
              ValueError: If the value is empty or whitespace-only.
            """
            if not str(v).strip():
                raise ValueError("backup_user must not be empty")
            return v

    else:

        @field_validator("backup_user")
        def _user_not_empty_v1(cls, v: str) -> str:  # type: ignore[override]
            """Ensure backup_user is not empty or whitespace-only.

            Args:
              v: Proposed username.

            Returns:
              str: Validated username.

            Raises:
              ValueError: If the value is empty or whitespace-only.
            """
            if not str(v).strip():
                raise ValueError("backup_user must not be empty")
            return v

    if _PYD_V2:

        @field_validator("cluster_ip")
        @classmethod
        def _cluster_ip_ok_v2(cls, v: Optional[str]) -> Optional[str]:
            """Validate that cluster_ip, when present, is a valid IPv4/IPv6.

            Args:
              v: Proposed IP as a string or None.

            Returns:
              Optional[str]: Normalized IP string or None.

            Raises:
              ValueError: If the IP is provided but invalid.
            """
            if v is None or str(v).strip() == "":
                return None
            s = str(v).strip()
            try:
                ipaddress.ip_address(s)
            except Exception as e:
                raise ValueError(
                    f"cluster_ip must be a valid IP address: {e}"
                ) from e
            return s

    else:

        @field_validator("cluster_ip")
        def _cluster_ip_ok_v1(cls, v: Optional[str]) -> Optional[str]:  # type: ignore[override]
            """Validate that cluster_ip, when present, is a valid IPv4/IPv6.

            Args:
              v: Proposed IP as a string or None.

            Returns:
              Optional[str]: Normalized IP string or None.

            Raises:
              ValueError: If the IP is provided but invalid.
            """
            if v is None or str(v).strip() == "":
                return None
            s = str(v).strip()
            try:
                ipaddress.ip_address(s)
            except Exception as e:
                raise ValueError(
                    f"cluster_ip must be a valid IP address: {e}"
                ) from e
            return s

    if _PYD_V2:

        @model_validator(mode="after")
        def _normalize_after_v2(self) -> "BackupConfig":
            """Normalize fields after parsing.

            Drops any None entries in directory that may result from pre-
            expansion of invalid/blank elements.

            Returns:
              BackupConfig: The same instance, normalized.
            """
            self.directory = [p for p in self.directory if p is not None]  # type: ignore[assignment]
            return self

    else:

        @model_validator(pre=False)
        def _normalize_after_v1(cls, values: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
            """Normalize fields after parsing.

            Drops any None entries in directory that may result from pre-
            expansion of invalid/blank elements.

            Args:
              values: Model values dict provided by Pydantic.

            Returns:
              Dict[str, Any]: Normalized values dict.
            """
            dirs = values.get("directory") or []
            values["directory"] = [p for p in dirs if p is not None]
            return values


def _format_validation_error(err: ValidationError) -> str:
    """Convert a Pydantic ValidationError into a readable multi-line message.

    Each failing field is rendered on its own line with a dotted field path and
    a short error message.

    Args:
      err: The Pydantic ValidationError instance to format.

    Returns:
      str: A multi-line string beginning with an overview and followed by one
      line per field error in the form "- <field.path>: <message>".
    """
    lines: List[str] = []
    for e in err.errors():  # type: ignore[attr-defined]
        loc = ".".join(str(x) for x in e.get("loc", []))
        msg = e.get("msg") or e.get("type") or "invalid value"
        lines.append(f"- {loc}: {msg}")
    if not lines:
        lines.append(str(err))
    return "Configuration validation failed:\n" + "\n".join(lines)


def load_config(path: _PathLike) -> BackupConfig:
    """Load and validate configuration from a YAML file.

    This reads the provided path (with ~ and environment-variable expansion),
    parses it as YAML, validates it against BackupConfig, and returns the
    resulting model instance. All errors are presented as ConfigError with a
    readable, aggregated message.

    Args:
      path: Path-like to the YAML configuration file (.yaml or .yml).

    Returns:
      BackupConfig: A fully validated configuration object.

    Raises:
      ConfigError: If the path is empty after expansion; if the file does not
        exist; if the extension is not .yaml/.yml; if the YAML cannot be
        parsed; or if validation fails (aggregated details included).

    Examples:
      Basic usage:
        >>> from pathlib import Path
        >>> cfg = load_config(Path("/etc/pybackup.yaml"))
        >>> isinstance(cfg.push_servers, list)
        True
    """
    p = _expand_path(path)
    if p is None:
        raise ConfigError("Configuration path is empty.")
    if not p.exists():
        raise ConfigError(f"Configuration file does not exist: {p}")
    if p.suffix.lower() not in (".yaml", ".yml"):
        raise ConfigError(
            f"Configuration file must end with .yaml or .yml: {p}"
        )

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"Failed to read/parse YAML: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError("Top-level YAML document must be a mapping/object.")

    try:
        return BackupConfig(**raw)
    except ValidationError as ve:
        raise ConfigError(_format_validation_error(ve)) from ve
    except Exception as e:
        raise ConfigError(f"Unexpected configuration error: {e}") from e


__all__ = [
    "ConfigError",
    "HostConfig",
    "MySQLConfig",
    "BackupConfig",
    "load_config",
]
