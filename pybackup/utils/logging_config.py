# pybackup/utils/logging_config.py

"""
Centralized logging configuration for pybackup.

This module provides a small API for configuring and using structured logging
across the project. It standardizes:
- A single named logger (APP_NAME).
- A rotating file handler and an optional console handler.
- A consistent log format that includes the invoking username and a short
  "code" tag (e.g., BK-0001) for easy filtering and triage.
- Lightweight wrapper functions for common log actions (status/warning/error).

Typical usage example:
    from pybackup.utils.logging_config import setup_logging, log_status

    # Initialize once at process start:
    setup_logging(level="INFO")

    # Log messages anywhere:
    log_status("BK-INIT", "Service started")

Design notes:
- Repeated calls to setup_logging() replace handlers to avoid duplicates.
- The file handler captures DEBUG-and-above; the console handler defaults to
  INFO-and-above.
"""
from __future__ import annotations

import getpass
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional

from pybackup.constants import (
    DEFAULT_LOG_PATH,
    LOG_DIR_PERMISSIONS,
    EXIT_ERROR,
    APP_NAME,
)


class LogContextFilter(logging.Filter):
    """Injects contextual fields into log records.

    This filter ensures that each log record contains:
    - username: The OS username of the invoking user.
    - code: A short tag (e.g., BK-0000) used for grouping/finding events.

    The wrappers in this module pass an explicit "code" via `extra=...`. This
    filter supplies a default when one is not provided.

    Attributes:
      None
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Enrich a log record with default fields.

        Args:
          record: The record to mutate.

        Returns:
          bool: True to allow the record to proceed through the logging chain.
        """
        if not hasattr(record, "username"):
            record.username = getpass.getuser()
        if not hasattr(record, "code"):
            record.code = "BK-0000"
        return True


# Module-wide default logger cache (initialized lazily by get_logger()).
_default_logger: Optional[logging.Logger] = None


def setup_logging(
    log_file: Optional[str] = None,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console_output: bool = True,
) -> logging.Logger:
    """Configure and return the project's root logger.

    This function initializes a rotating file handler and, optionally, a console
    handler. It also installs a LogContextFilter to enrich records with a
    "username" and default "code" if not provided.

    The function is idempotent with respect to handlers: any existing handlers
    on the named logger are cleared before adding new ones, ensuring repeated
    calls do not accumulate duplicate outputs.

    Args:
      log_file: Destination path for the log file. If None, uses
        pybackup.constants.DEFAULT_LOG_PATH.
      level: Logging level name for the logger (e.g., "DEBUG", "INFO").
      max_bytes: Maximum size (in bytes) for a single log file before rotation.
      backup_count: Number of rotated log files to retain.
      console_output: If True, add a console (stdout) handler.

    Returns:
      logging.Logger: The configured logger instance for APP_NAME.

    Raises:
      OSError: If the log directory cannot be created with the requested
        permissions.

    Examples:
      >>> logger = setup_logging(level="DEBUG", console_output=False)
      >>> logger.name == APP_NAME
      True
    """
    global _default_logger

    target_path = Path(log_file or DEFAULT_LOG_PATH)
    log_dir = target_path.parent

    # Ensure log directory exists with the intended permissions.
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Best effort; may be ignored by some platforms.
        os.chmod(log_dir, LOG_DIR_PERMISSIONS)
    except Exception:
        # Do not fail hard if chmod is not supported or not permitted.
        pass

    logger = logging.getLogger(APP_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.handlers.clear()  # Avoid duplicates on reconfigure.
    logger.propagate = False

    # Standardized formatter includes username and code in brackets.
    fmt = "%(asctime)s - %(name)s - %(levelname)s - [%(username)s] (%(code)s): %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    # Rotating file handler captures DEBUG and above.
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(target_path),
        maxBytes=int(max_bytes),
        backupCount=int(backup_count),
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Optional console handler for user-facing progress.
    if console_output:
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # Install a context filter to add "username" and default "code".
    logger.addFilter(LogContextFilter())

    _default_logger = logger
    return logger


def get_logger() -> logging.Logger:
    """Return the process-wide logger for APP_NAME, initializing if needed.

    Returns:
      logging.Logger: The logger instance configured by setup_logging().

    Notes:
      - If setup_logging() has not been called yet, this function will call it
        with default parameters.
    """
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logging()
    return _default_logger


def log_status(code: str, message: str) -> None:
    """Log a non-error status message at INFO level.

    Args:
      code: A short tag for the event (e.g., "BK-INIT", "BK-STEP").
      message: Human-readable message to record.

    Returns:
      None
    """
    get_logger().info(
        message, extra={"code": code, "username": getpass.getuser()}
    )


def log_warning(code: str, message: str) -> None:
    """Log a warning message at WARNING level.

    Args:
      code: A short tag for the event (e.g., "BK-WARN").
      message: Human-readable message to record.

    Returns:
      None
    """
    get_logger().warning(
        message, extra={"code": code, "username": getpass.getuser()}
    )


def log_error(code: str, message: str) -> None:
    """Log an error message at ERROR level.

    Args:
      code: A short tag for the event (e.g., "BK-ERR").
      message: Human-readable message to record.

    Returns:
      None
    """
    get_logger().error(
        message, extra={"code": code, "username": getpass.getuser()}
    )


def log_debug(code: str, message: str) -> None:
    """Log a diagnostic message at DEBUG level.

    Args:
      code: A short tag for the event (e.g., "BK-DBG").
      message: Human-readable message to record.

    Returns:
      None
    """
    get_logger().debug(
        message, extra={"code": code, "username": getpass.getuser()}
    )


def log_and_exit(code: str, message: str, exit_code: int = EXIT_ERROR) -> None:
    """Log an error and terminate the process with the specified exit code.

    Args:
      code: A short tag for the event (e.g., "BK-FAIL").
      message: Human-readable message to record prior to termination.
      exit_code: Process exit code. Defaults to pybackup.constants.EXIT_ERROR.

    Returns:
      None

    Raises:
      SystemExit: Always raised after logging, with the specified exit code.
    """
    get_logger().error(
        message, extra={"code": code, "username": getpass.getuser()}
    )
    raise SystemExit(int(exit_code))


def log2die(code: str, message: str, die: bool = True) -> None:
    """Backward-compatible wrapper for legacy log2die() usage.

    The pybackup codebase historically used log2die(code, message, die=True|False).
    This wrapper preserves that API while delegating to the new logging helpers.

    Args:
      code: A short tag for the event (e.g., "BK-XXXX").
      message: Human-readable message to record.
      die: If True, log as an error and exit; otherwise log as a status line.

    Returns:
      None
    """
    if die:
        log_and_exit(code, message)
    else:
        log_status(code, message)
