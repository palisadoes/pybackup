#!/usr/bin/env python3
"""
pybackup CLI

A thin, user-facing entry point that:
- Parses command-line arguments
- Loads and validates configuration
- Sets up logging
- Dispatches to the appropriate operation (local, push, pull)

This script is designed to work with the modular pybackup package. It also
includes safe fallbacks so it can run during incremental refactors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union


def _ensure_repo_root_on_sys_path() -> None:
    # bin/pybackup.py -> repo/bin -> parents[1] is the repository root
    repo_root = Path(__file__).resolve().parents[1]
    repo_str = str(repo_root)
    if repo_str not in sys.path:
        # Prepend so local sources win over any globally installed versions
        sys.path.insert(0, repo_str)


_ensure_repo_root_on_sys_path()


# ---------------------------------------------------------------------------
# Constants (with fallbacks)
# ---------------------------------------------------------------------------

try:
    from pybackup.constants import (
        EXIT_SUCCESS,
        EXIT_ERROR,
        DEFAULT_LOG_PATH,
        CLI_WIDTH,
    )
except Exception:
    EXIT_SUCCESS = 0
    EXIT_ERROR = 2
    DEFAULT_LOG_PATH = "/var/log/backups/backups.log"
    CLI_WIDTH = 80


# ---------------------------------------------------------------------------
# Logging setup (with fallbacks)
# ---------------------------------------------------------------------------

try:
    from pybackup.utils.logging_config import (
        setup_logging,
        log_status,
        log_warning,
        log_error,
    )
except Exception:
    # Minimal fallback using stdlib logging
    import logging

    def setup_logging(
        log_file: Optional[str] = None,
        level: str = "INFO",
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        console_output: bool = True,
    ) -> logging.Logger:
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s - pybackup - %(levelname)s - %(message)s",
        )
        return logging.getLogger("pybackup")

    def log_status(code: str, message: str) -> None:
        import logging as _logging

        _logging.getLogger("pybackup").info(f"[{code}] {message}")

    def log_warning(code: str, message: str) -> None:
        import logging as _logging

        _logging.getLogger("pybackup").warning(f"[{code}] {message}")

    def log_error(code: str, message: str) -> None:
        import logging as _logging

        _logging.getLogger("pybackup").error(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# Config loading (with fallbacks)
# ---------------------------------------------------------------------------

try:
    # Preferred: typed config loader
    from pybackup.core.config import load_config as _typed_load_config  # type: ignore
except Exception:
    _typed_load_config = None

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # Will raise if fallback is needed but unavailable


print("boo")
sys.exit()


def _load_config_fallback(config_path: Path) -> Dict[str, Any]:
    """Fallback YAML loader returning a plain dict."""
    if yaml is None:
        raise RuntimeError(
            "PyYAML is not installed; cannot load configuration."
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            "Configuration file did not contain a YAML mapping/object."
        )
    return data


def load_config(config_path: Path) -> Any:
    """Load configuration using the typed loader when available; otherwise YAML fallback."""
    if _typed_load_config is not None:
        return _typed_load_config(config_path)
    return _load_config_fallback(config_path)


# ---------------------------------------------------------------------------
# Operations (import with tolerance for incremental refactors)
# ---------------------------------------------------------------------------

try:
    from pybackup.local import LocalBackupManager  # type: ignore
except Exception:
    LocalBackupManager = None  # type: ignore

try:
    from pybackup.push import PushManager  # type: ignore
except Exception:
    PushManager = None  # type: ignore

try:
    from pybackup.pull import PullManager  # type: ignore
except Exception:
    PullManager = None  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    """Return obj.name if present, else obj[name] if obj is a mapping, else default."""
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


def _as_dict(obj: Any) -> Dict[str, Any]:
    """Best-effort conversion of a typed model to plain dict."""
    # Pydantic v2
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        return obj.model_dump()  # type: ignore
    # Pydantic v1
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        return obj.dict()  # type: ignore
    # Already a dict or a simple object
    if isinstance(obj, dict):
        return obj
    # Fallback: shallow __dict__ copy
    return {
        k: getattr(obj, k)
        for k in dir(obj)
        if not k.startswith("_") and hasattr(obj, k)
    }


def _as_path_list(items: Iterable[Union[str, Path]]) -> List[Path]:
    paths: List[Path] = []
    for it in items:
        paths.append(it if isinstance(it, Path) else Path(str(it)))
    return paths


def _normalize_hosts(hosts: Iterable[Any]) -> List[Dict[str, Any]]:
    """Return a list of host dictionaries from typed or dynamic config objects."""
    return [_as_dict(h) for h in (hosts or [])]


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def run_local(cfg: Any, args: argparse.Namespace) -> int:
    """Execute local backup flow."""
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

    # Optional dry-run
    if getattr(args, "dry_run", False):
        log_status(
            "BK-DRY",
            f"[local] dry-run: backup_dir={backup_dir}, directories={directories}, "
            f"exclude={exclude}, cluster_ip={cluster_ip}, max_age={args.max_age}",
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
    except Exception as exc:
        log_error("BK-LOC", f"Local backup failed: {exc}")
        return EXIT_ERROR


def run_push(cfg: Any, args: argparse.Namespace) -> int:
    """Execute push flow."""
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
    except Exception as exc:
        log_error("BK-PUSH", f"Push failed: {exc}")
        return EXIT_ERROR


def run_pull(cfg: Any, args: argparse.Namespace) -> int:
    """Execute pull flow."""
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
    except Exception as exc:
        log_error("BK-PULL", f"Pull failed: {exc}")
        return EXIT_ERROR


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_global_args(parser: argparse.ArgumentParser) -> None:
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
    parser = argparse.ArgumentParser(
        prog="pybackup",
        description="Backup utility with local, push, and pull modes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_global_args(parser)

    sub = parser.add_subparsers(dest="command", required=True)

    # local
    p_local = sub.add_parser(
        "local",
        help="Create local backups (filesystem and optional database).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_local.add_argument(
        "--config-file", required=True, help="Path to YAML config file."
    )
    p_local.add_argument(
        "--max-age",
        type=int,
        default=None,
        help="Purge backup files older than this many days (optional).",
    )
    p_local.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without executing them.",
    )

    # push
    p_push = sub.add_parser(
        "push",
        help="Push local backup files to remote servers.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_push.add_argument(
        "--config-file", required=True, help="Path to YAML config file."
    )
    p_push.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without executing them.",
    )

    # pull
    p_pull = sub.add_parser(
        "pull",
        help="Pull backup files from remote servers to this host.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_pull.add_argument(
        "--config-file", required=True, help="Path to YAML config file."
    )
    p_pull.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without executing them.",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    # Initialize logging first thing so subsequent steps are captured
    setup_logging(log_file=args.log_file, level=args.log_level)

    # Load configuration
    cfg_path = Path(args.config_file) if hasattr(args, "config_file") else None
    if not cfg_path or not cfg_path.exists():
        log_error("BK-CLI", f"Configuration file not found: {cfg_path}")
        return EXIT_ERROR

    try:
        cfg = load_config(cfg_path)
    except Exception as exc:
        log_error("BK-CFG", f"Failed to load configuration: {exc}")
        return EXIT_ERROR

    # Dispatch
    cmd = args.command
    if cmd == "local":
        return run_local(cfg, args)
    if cmd == "push":
        return run_push(cfg, args)
    if cmd == "pull":
        return run_pull(cfg, args)

    log_error("BK-CLI", f"Unknown command: {cmd}")
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
