#!/usr/bin/env python3
"""pybackup CLI entrypoint.

This module provides a thin command-line interface (CLI) for the pybackup
package. It is responsible for:
- Parsing command-line arguments.
- Initializing logging.
- Loading and validating the YAML configuration file.
- Dispatching to the appropriate operation (local, push, pull).

Design
- The CLI intentionally contains no business logic. It delegates to the
  corresponding modules in the pybackup package (e.g., pybackup.local).
- The script supports running directly from a source checkout by inserting
  the repository root into sys.path. This enables `python bin/pybackup.py`
  without requiring `pip install -e .`.

Examples:
  Run a local backup and purge backups older than 7 days:
    $ python bin/pybackup.py local --config-file /etc/pybackup.yaml --max-age 7

  Push backups to remote servers:
    $ python bin/pybackup.py push --config-file /etc/pybackup.yaml

  Pull backups from remote servers:
    $ python bin/pybackup.py pull --config-file /etc/pybackup.yaml
"""

from __future__ import annotations
import os
import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

# Import the pybackup directory
LIBRARY = f"{Path(__file__).resolve().parent.parent}{os.sep}pybackup"
if LIBRARY not in sys.path:
    sys.path.insert(0, LIBRARY)


# --------------------------------------------------------------------------------------
# Import path bootstrap
# --------------------------------------------------------------------------------------
def _ensure_repo_root_on_sys_path() -> None:
    """Ensure the repository root is present on sys.path.

    This helper makes the CLI runnable directly from a clean source checkout
    (e.g., `python bin/pybackup.py`) without requiring an editable install.

    It assumes the following layout:
      repo_root/
        bin/pybackup.py
        pybackup/...

    If the repository root is not present in sys.path, it is inserted at the
    front so local sources take precedence over any globally installed package.

    Raises:
      None
    """
    repo_root = Path(__file__).resolve().parents[1]
    repo_str = str(repo_root)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)


_ensure_repo_root_on_sys_path()


# --------------------------------------------------------------------------------------
# Constants (with safe fallbacks)
# --------------------------------------------------------------------------------------
try:
    from pybackup.constants import (
        EXIT_SUCCESS,
        EXIT_ERROR,
        DEFAULT_LOG_PATH,
    )
except Exception:  # pragma: no cover - fallback for early bootstrapping
    EXIT_SUCCESS = 0
    EXIT_ERROR = 2
    DEFAULT_LOG_PATH = "/var/log/backups/backups.log"


# ----------------------------------------------------------------------------
# Logging setup (with safe fallbacks)
# ----------------------------------------------------------------------------
try:
    from pybackup.utils.logging_config import (
        setup_logging,
        log_status,
        log_warning,
        log_error,
    )
except Exception:  # pragma: no cover - minimal fallback
    import logging

    def setup_logging(
        level: str = "INFO",
    ) -> logging.Logger:
        """Use minimal logger if pybackup.utils.logging_config is unavailable.

        Args:
          level: Logging level name (e.g., 'INFO', 'DEBUG').

        Returns:
          logging.Logger: Configured stdlib logger.

        """
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s - pybackup - %(levelname)s - %(message)s",
        )
        return logging.getLogger("pybackup")

    def log_status(code: str, message: str) -> None:
        """Log a non-error status message (fallback)."""
        import logging as _logging

        _logging.getLogger("pybackup").info(f"[{code}] {message}")

    def log_warning(code: str, message: str) -> None:
        """Log a warning message (fallback)."""
        import logging as _logging

        _logging.getLogger("pybackup").warning(f"[{code}] {message}")

    def log_error(code: str, message: str) -> None:
        """Log an error message (fallback)."""
        import logging as _logging

        _logging.getLogger("pybackup").error(f"[{code}] {message}")


# --------------------------------------------------------------------------------------
# Config loading (typed loader preferred; YAML fallback)
# --------------------------------------------------------------------------------------
try:
    # Preferred: validated, typed config loader.
    from pybackup.core.config import load_config as _typed_load_config
except Exception:  # pragma: no cover
    _typed_load_config = None

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # YAML fallback won't be available if this import fails.


def _load_config_fallback(config_path: Path) -> Dict[str, Any]:
    """Load configuration using a minimal YAML fallback.

    This function is used only when pybackup.core.config.load_config is not
    available. It returns a plain dict without type validation.

    Args:
      config_path: Absolute path to the YAML configuration file.

    Returns:
      Dict[str, Any]: Unvalidated configuration dictionary.

    Raises:
      RuntimeError: If PyYAML is not installed.
      ValueError: If the top-level YAML document is not a mapping.
    """
    if yaml is None:
        raise RuntimeError(
            "PyYAML is not installed; cannot load configuration."
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            "Configuration file must contain a YAML mapping/object."
        )
    return data


def load_config(config_path: Path) -> Any:
    """Load the configuration model (typed loader preferred).

    Args:
      config_path: Absolute path to the YAML configuration file.

    Returns:
      Any: A typed configuration model (if available) or a plain dictionary
      (fallback) representing the configuration content.

    Raises:
      Exception: Propagates exceptions from the underlying loader.
    """
    if _typed_load_config is not None:
        return _typed_load_config(config_path)
    return _load_config_fallback(config_path)


# --------------------------------------------------------------------------------------
# Operation imports (tolerant for incremental refactors)
# --------------------------------------------------------------------------------------
try:
    from pybackup.operations.local import LocalBackupManager  # type: ignore
except Exception:  # pragma: no cover
    LocalBackupManager = None  # type: ignore

try:
    from pybackup.operations.push import PushManager  # type: ignore
except Exception:  # pragma: no cover
    PushManager = None  # type: ignore

try:
    from pybackup.operations.pull import PullManager  # type: ignore
except Exception:  # pragma: no cover
    PullManager = None  # type: ignore


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def _get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    """Retrieve a configuration attribute from a typed model or mapping.

    This helper checks for a named attribute (e.g., Pydantic model) first and
    then falls back to a dictionary lookup.

    Args:
      obj: Configuration object (typed model or dictionary).
      name: Attribute/key name to retrieve.
      default: Default value to return when the field is absent.

    Returns:
      Any: Retrieved value or default if not present.
    """
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


def _as_dict(obj: Any) -> Dict[str, Any]:
    """Convert a typed configuration object to a plain dictionary.

    The function attempts common adapter methods (Pydantic v2 `.model_dump()`,
    Pydantic v1 `.dict()`) before falling back to a shallow `__dict__` copy.

    Args:
      obj: Typed model or dict.

    Returns:
      Dict[str, Any]: A dictionary representation of the input object.
    """
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        return obj.model_dump()  # Pydantic v2
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        return obj.dict()  # Pydantic v1
    if isinstance(obj, dict):
        return obj
    # Fallback: shallow attribute extraction (excludes private attributes)
    return {
        k: getattr(obj, k)
        for k in dir(obj)
        if not k.startswith("_") and hasattr(obj, k)
    }


def _as_path_list(items: Iterable[Union[str, Path]]) -> List[Path]:
    """Convert an iterable of strings/Paths to a list of Path objects.

    Args:
      items: Iterable of path-like elements.

    Returns:
      List[Path]: List of Path objects.
    """
    return [p if isinstance(p, Path) else Path(str(p)) for p in items]


def _normalize_hosts(hosts: Iterable[Any]) -> List[Dict[str, Any]]:
    """Normalize a list of host entries into plain dictionaries.

    Args:
      hosts: Iterable of typed host models or dictionaries.

    Returns:
      List[Dict[str, Any]]: Normalized host dictionaries.
    """
    return [_as_dict(h) for h in (hosts or [])]


# --------------------------------------------------------------------------------------
# Subcommand implementations
# --------------------------------------------------------------------------------------
def run_local(cfg: Any, args: argparse.Namespace) -> int:
    """Execute the local backup flow.

    This function performs validation, optional dry-run reporting, and invokes
    the LocalBackupManager to conduct a filesystem/database backup as
    configured.

    Args:
      cfg: Typed configuration model or dict containing backup settings.
      args: Parsed CLI arguments for the 'local' subcommand.

    Returns:
      int: EXIT_SUCCESS (0) on success; EXIT_ERROR (non-zero) on failure.

    Raises:
      None: Exceptions are caught and converted to error logs and exit codes.
    """
    if LocalBackupManager is None:
        log_error("BK-CLI", "Local mode is unavailable (module not found).")
        return EXIT_ERROR

    backup_dir = _get_attr_or_key(cfg, "backup_dir")
    if not backup_dir:
        log_error("BK-CLI", "Configuration is missing 'backup_dir'.")
        return EXIT_ERROR

    directories = _get_attr_or_key(cfg, "directory", []) or []
    exclude = _get_attr_or_key(cfg, "exclude", []) or []
    cluster_ip = _get_attr_or_key(cfg, "cluster_ip", None)

    dir_paths = _as_path_list(directories)

    if getattr(args, "dry_run", False):
        log_status(
            "BK-DRY",
            (
                "[local] dry-run: "
                f"backup_dir={backup_dir}, directories={directories}, "
                f"""\
exclude={exclude}, cluster_ip={cluster_ip}, max_age={args.max_age}"""
            ),
        )
        return EXIT_SUCCESS

    try:
        mgr = LocalBackupManager(Path(backup_dir), dir_paths, exclude)
        mgr.run(cluster_ip=cluster_ip, max_age_days=args.max_age)
        log_status("BK-DONE", "Local backup completed.")
        return EXIT_SUCCESS
    except KeyboardInterrupt:
        log_warning("BK-INT", "Interrupted by user.")
        return EXIT_ERROR
    except Exception as exc:  # pragma: no cover - depends on environment
        log_error("BK-LOC", f"Local backup failed: {exc}")
        return EXIT_ERROR


def run_push(cfg: Any, args: argparse.Namespace) -> int:
    """Execute the push flow (transfer backups to remote servers).

    Args:
      cfg: Typed configuration model or dict with 'push_servers' entries.
      args: Parsed CLI arguments for the 'push' subcommand.

    Returns:
      int: EXIT_SUCCESS (0) on success; EXIT_ERROR (non-zero) on failure.
    """
    if PushManager is None:
        log_error("BK-CLI", "Push mode is unavailable (module not found).")
        return EXIT_ERROR

    push_hosts = _get_attr_or_key(cfg, "push_servers", []) or []
    hosts = _normalize_hosts(push_hosts)

    if getattr(args, "dry_run", False):
        hostnames = [h.get("hostname") for h in hosts]
        log_status("BK-DRY", f"[push] dry-run: hosts={hostnames}")
        return EXIT_SUCCESS

    try:
        PushManager().run(hosts)
        log_status("BK-DONE", "Push completed.")
        return EXIT_SUCCESS
    except KeyboardInterrupt:
        log_warning("BK-INT", "Interrupted by user.")
        return EXIT_ERROR
    except Exception as exc:  # pragma: no cover - depends on environment
        log_error("BK-PUSH", f"Push failed: {exc}")
        return EXIT_ERROR


def run_pull(cfg: Any, args: argparse.Namespace) -> int:
    """Execute the pull flow (retrieve backups from remote servers).

    Args:
      cfg: Typed configuration model or dict with 'pull_servers' entries.
      args: Parsed CLI arguments for the 'pull' subcommand.

    Returns:
      int: EXIT_SUCCESS (0) on success; EXIT_ERROR (non-zero) on failure.
    """
    if PullManager is None:
        log_error("BK-CLI", "Pull mode is unavailable (module not found).")
        return EXIT_ERROR

    pull_hosts = _get_attr_or_key(cfg, "pull_servers", []) or []
    hosts = _normalize_hosts(pull_hosts)

    if getattr(args, "dry_run", False):
        hostnames = [h.get("hostname") for h in hosts]
        log_status("BK-DRY", f"[pull] dry-run: hosts={hostnames}")
        return EXIT_SUCCESS

    try:
        PullManager().run(hosts)
        log_status("BK-DONE", "Pull completed.")
        return EXIT_SUCCESS
    except KeyboardInterrupt:
        log_warning("BK-INT", "Interrupted by user.")
        return EXIT_ERROR
    except Exception as exc:  # pragma: no cover - depends on environment
        log_error("BK-PULL", f"Pull failed: {exc}")
        return EXIT_ERROR


# --------------------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------------------
def _add_global_args(parser: argparse.ArgumentParser) -> None:
    """Add global CLI options to the root argument parser.

    Args:
      parser: The root argparse.ArgumentParser to extend.

    Returns:
      None
    """
    parser.add_argument(
        "--config-file", required=True, help="Path to YAML config file."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without executing them.",
    )
    parser.add_argument(
        "--log-file",
        default=DEFAULT_LOG_PATH,
        help=f"Path to log file (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level.",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser with subcommands.

    Returns:
      argparse.ArgumentParser: Configured parser for the pybackup CLI.
    """
    parser = argparse.ArgumentParser(
        prog="pybackup",
        description="Backup utility with local, push, and pull modes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Create sub parsers
    sub = parser.add_subparsers(dest="command", required=True)

    # local
    p_local = sub.add_parser(
        "local",
        help="Create local backups (filesystem and optional database).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_local.add_argument(
        "--max-age",
        type=int,
        default=None,
        help="Purge backup files older than this many days (optional).",
    )
    _add_global_args(p_local)

    # push
    p_push = sub.add_parser(
        "push",
        help="Push local backup files to remote servers.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_global_args(p_push)

    # pull
    p_pull = sub.add_parser(
        "pull",
        help="Pull backup files from remote servers to this host.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_global_args(p_pull)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Program entry point.

    This function:
    1) Parses CLI arguments.
    2) Initializes logging.
    3) Loads the configuration file.
    4) Dispatches to the requested subcommand.

    Args:
      argv: Optional explicit sequence of CLI arguments (for testing). If None,
        sys.argv[1:] is used.

    Returns:
      int: EXIT_SUCCESS (0) on success; EXIT_ERROR (non-zero) on failure.

    Raises:
      None: All errors are handled and converted into exit codes and logs.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Initialize logging first so subsequent steps are captured.
    setup_logging(log_file=args.log_file, level=args.log_level)

    # Load configuration.
    cfg_path = Path(args.config_file)
    if not cfg_path.exists():
        log_error("BK-CLI", f"Configuration file not found: {cfg_path}")
        return EXIT_ERROR

    try:
        cfg = load_config(cfg_path)
    except Exception as exc:  # pragma: no cover - depends on environment
        log_error(
            "BK-CFG", f"Failed to load configuration from {cfg_path}: {exc}"
        )
        return EXIT_ERROR

    # Dispatch.
    cmd = args.command
    if cmd == "local":
        return run_local(cfg, args)
    if cmd == "push":
        return run_push(cfg, args)
    if cmd == "pull":
        return run_pull(cfg, args)

    log_error("BK-CLI", f"Unknown command: {cmd}")
    return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
