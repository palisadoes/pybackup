# pybackup/utils/command.py
"""
Safe subprocess helpers for pybackup.

This module centralizes a few thin wrappers around the Python standard library's
subprocess APIs so the rest of the codebase can invoke external commands in a
consistent, testable, and secure way (always shell=False).

Key features:
- CommandResult dataclass capturing argv, exit status, stdout, stderr, and timing.
- execute_command() to run a command and capture output.
- check_call() to run a command and raise CommandError on non‑zero exit.
- quote_argv() to render argv for logs and diagnostics.
- which() convenience wrapper around shutil.which.

Typical usage example:
    from pybackup.utils.command import execute_command, check_call

    # Capture output and examine return code
    res = execute_command(["/usr/bin/rsync", "--version"])
    if res.returncode != 0:
        ...

    # Enforce success (raises on non‑zero exit)
    check_call(["/bin/tar", "--version"])
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Union
import os
import shlex
import shutil
import subprocess
import time


# Public API exported by this module.
__all__ = [
    "CommandResult",
    "CommandError",
    "execute_command",
    "check_call",
    "quote_argv",
    "which",
]


@dataclass(frozen=True)
class CommandResult:
    """Immutable record of a completed subprocess invocation.

    Attributes:
      args: The exact argv list passed to subprocess (no shell).
      returncode: The process exit status (0 indicates success).
      stdout: Standard output captured as text (UTF‑8 by default).
      stderr: Standard error captured as text (UTF‑8 by default).
      start: Monotonic timestamp just before invocation (seconds).
      end: Monotonic timestamp after completion (seconds).
      duration: end - start, in seconds.
    """

    args: List[str]
    returncode: int
    stdout: str
    stderr: str
    start: float
    end: float
    duration: float


class CommandError(RuntimeError):
    """Raised when a subprocess returns a non‑zero status under check_call().

    The exception wraps a CommandResult instance so callers can inspect the
    exit status and any output produced.

    Attributes:
      result: The CommandResult corresponding to the failed command.
    """

    def __init__(
        self, result: CommandResult, message: Optional[str] = None
    ) -> None:
        """Initialize a CommandError.

        Args:
          result: A CommandResult capturing the failed invocation.
          message: Optional custom message. If omitted, a default message is
            composed using argv, return code and a short stderr excerpt.
        """
        self.result = result
        default = (
            f"Command failed (exit={result.returncode}): "
            f"{quote_argv(result.args)}; stderr={result.stderr.strip()[:500]}"
        )
        super().__init__(message or default)


def execute_command(
    args: Sequence[Union[str, os.PathLike]],
    *,
    cwd: Optional[Union[str, os.PathLike]] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> CommandResult:
    """Run a command (shell=False), capture output, and return a CommandResult.

    Security:
      - Always uses shell=False. Callers must pass argv as a sequence of tokens.
      - Callers are responsible for input validation and quoting/escaping at
        argument boundaries as appropriate for the target executable.

    Args:
      args: Command argv (e.g., ["/usr/bin/rsync", "--version"]). Elements can
        be str or Path‑like; Paths are converted to strings.
      cwd: Optional working directory for the child process.
      env: Optional environment overlay. Values here override the current
        process environment; absent keys inherit from os.environ.
      timeout: Optional timeout in seconds. If exceeded, subprocess.TimeoutExpired
        is raised (no CommandResult is returned).
      encoding: Text encoding used to decode stdout/stderr (capture is always in text mode).
      errors: Error handler for decoding (e.g., "replace", "ignore").

    Returns:
      CommandResult: Immutable record containing argv, exit status, output, and timing.

    Raises:
      subprocess.TimeoutExpired: If the process does not complete within `timeout`.
      OSError: If the executable cannot be found or the process cannot be started.

    Examples:
      >>> res = execute_command(["/bin/echo", "hello"])
      >>> res.stdout.strip()
      'hello'
    """
    argv: List[str] = [str(a) for a in args]
    start = time.monotonic()

    # Merge environment: overlay onto a copy of current environment.
    effective_env: Optional[Dict[str, str]] = None
    if env is not None:
        effective_env = os.environ.copy()
        # Coerce values to str to avoid surprises in subprocess
        effective_env.update({str(k): str(v) for k, v in env.items()})

    completed = subprocess.run(  # nosec: B603 - shell is False by default here
        argv,
        cwd=None if cwd is None else str(cwd),
        env=effective_env,
        timeout=timeout,
        capture_output=True,
        text=True,  # decode to text using encoding/errors below
        encoding=encoding,
        errors=errors,
        check=False,  # never auto‑raise here; we return CommandResult
    )
    end = time.monotonic()
    return CommandResult(
        args=argv,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        start=start,
        end=end,
        duration=end - start,
    )


def check_call(
    args: Sequence[Union[str, os.PathLike]],
    *,
    cwd: Optional[Union[str, os.PathLike]] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> CommandResult:
    """Run a command and raise CommandError if it returns a non‑zero status.

    This is a convenience wrapper around execute_command(). On success, it
    returns the CommandResult. On failure (returncode != 0), it raises a
    CommandError that includes the full CommandResult for inspection.

    Args:
      args: Command argv (no shell). Elements can be str or Path‑like.
      cwd: Optional working directory for the child process.
      env: Optional environment overlay; see execute_command().
      timeout: Optional timeout in seconds; see execute_command().
      encoding: Text encoding used to decode stdout/stderr.
      errors: Error handler for decoding.

    Returns:
      CommandResult: The successful command's result.

    Raises:
      CommandError: If the process exits with a non‑zero return code.
      subprocess.TimeoutExpired: If the process exceeds `timeout`.
      OSError: If the executable cannot be found or the process cannot start.

    Examples:
      >>> _ = check_call(["/bin/true"])
      >>> # check_call(["/bin/false"])  # doctest: +SKIP (raises CommandError)
    """
    result = execute_command(
        args,
        cwd=cwd,
        env=env,
        timeout=timeout,
        encoding=encoding,
        errors=errors,
    )
    if result.returncode != 0:
        raise CommandError(result)
    return result


def quote_argv(args: Sequence[Union[str, os.PathLike]]) -> str:
    """Return a shell‑quoted string representation of argv for logs.

    This is intended for human consumption in logs and diagnostics; it should
    not be executed via a shell. Each token is quoted using shlex.quote to
    minimize ambiguity in presentation.

    Args:
      args: Sequence of argv tokens (str or Path‑like).

    Returns:
      str: A single string rendering of argv, with tokens quoted as needed.

    Examples:
      >>> quote_argv(["/bin/echo", "a b", "$HOME"])
      "'/bin/echo' 'a b' '$HOME'"
    """
    return " ".join(shlex.quote(str(a)) for a in args)


def which(program: Union[str, os.PathLike]) -> Optional[str]:
    """Locate an executable in PATH.

    This is a thin wrapper around shutil.which that normalizes the result
    to None when not found and to a string path when found.

    Args:
      program: Executable name or path‑like object.

    Returns:
      Optional[str]: Absolute path to the executable if found; otherwise None.

    Examples:
      >>> isinstance(which("python"), (str, type(None)))
      True
    """
    found = shutil.which(str(program))
    return found if found else None
