# test/conftest.py
"""
Pytest configuration and shared fixtures.

This module provides:
- Repository root bootstrap for imports from the source tree.
- Helpers for loading the CLI module from bin/pybackup.py.
- A factory for building CommandResult-like objects for command mocks.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

import pytest


@pytest.fixture(scope="session", autouse=True)
def ensure_repo_on_sys_path() -> None:
    """Ensure repository root is on sys.path for direct source imports.

    This fixture lets tests import the pybackup package without installing it.
    It prepends the repository root (parent of the 'test' directory) to sys.path.
    """
    test_root = Path(__file__).resolve().parent
    repo_root = test_root.parent
    repo_str = str(repo_root)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)


@pytest.fixture()
def fake_cmdresult():
    """Factory returning a helper to create a CommandResult-like object.

    Returns:
      Callable[..., Any]: A constructor function that returns an object with
      attributes: returncode, stdout, stderr.
    """

    class _R:
        """Simple read-only container for subprocess-like results."""

        def __init__(
            self, returncode: int = 0, stdout: str = "", stderr: str = ""
        ) -> None:
            """Initialize the container.

            Args:
              returncode: Exit status (0 for success).
              stdout: Text to return as process standard output.
              stderr: Text to return as process standard error.
            """
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _factory(
        returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> Any:
        """Create a new result container.

        Args:
          returncode: Exit status code for the fake process.
          stdout: Standard output text.
          stderr: Standard error text.

        Returns:
          Any: An object with returncode/stdout/stderr attributes.
        """
        return _R(returncode=returncode, stdout=stdout, stderr=stderr)

    return _factory


@pytest.fixture()
def load_cli_module() -> Callable[[], Any]:
    """Provide a callable that loads the CLI module from bin/pybackup.py.

    Returns:
      Callable[[], Any]: Function that loads and returns the CLI module object.
    """

    def _loader() -> Any:
        test_root = Path(__file__).resolve().parent
        repo_root = test_root.parent
        cli_path = repo_root / "bin" / "pybackup.py"
        spec = importlib.util.spec_from_file_location("pybackup_cli", cli_path)
        assert spec and spec.loader, f"Cannot create spec for {cli_path}"
        mod = importlib.util.module_from_spec(spec)
        sys.modules["pybackup_cli"] = mod
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        return mod

    return _loader
