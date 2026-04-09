# test/bin/test_pybackup_cli.py
"""
CLI tests for bin/py_backup.py.

These tests load the CLI module directly from its file path and exercise
argument parsing, config loading, and dispatch glue using monkeypatches to
avoid real side effects.
"""

from __future__ import annotations
import os
import uuid
from pathlib import Path
import pytest
import yaml


def test_parser_builds(load_cli_module) -> None:
    """Test that the CLI builds an argparse parser successfully."""
    cli = load_cli_module()
    parser = cli._build_parser()
    assert parser is not None
    help_text = parser.format_help()
    assert "pybackup" in help_text


def test_main_local_success(monkeypatch, tmp_path, load_cli_module) -> None:
    """Test main() happy path for 'local' mode with a minimal config stub.

    This test monkeypatches:
    - load_config to return a dictionary matching expected fields.
    - run_local to return EXIT_SUCCESS without doing real work.
    """
    cli = load_cli_module()
    config_file = f"{tmp_path}{os.sep}c.yaml"

    # Create a random directory for testing purposes
    directory = Path(f"{tmp_path}{os.sep}{str(uuid.uuid4())}")
    directory.mkdir(parents=True, exist_ok=True)

    def _create_fake_config(path):
        config = {
            "backup_dir": create_random_directory(path, add_file=False),
            "backup_user": "backup",
            "directory": [create_random_directory(path, add_file=True)],
            "exclude": [],
            "cluster_ip": None,
            "push_servers": [],
            "pull_servers": [],
        }
        with open(f"{path}{os.sep}c.yaml", "w", encoding="utf-8") as file:
            yaml.dump(config, file, indent=4)

        return path

    called = {}

    def _fake_run_local(cls, args):
        called["ran"] = True
        return cli.EXIT_SUCCESS

    _create_fake_config(tmp_path)
    monkeypatch.setattr(cli, "run_local", _fake_run_local)

    rc = cli.main(
        [
            "local",
            "--config-file",
            config_file,
            "--max-age",
            "1",
            "--log-file",
            f"{tmp_path}{os.sep}t.log",
        ]
    )
    assert rc == cli.EXIT_SUCCESS
    assert called.get("ran") is True


def test_main_unknown_command_returns_error(load_cli_module) -> None:
    """Test that an unknown subcommand results in an error exit code."""
    cli = load_cli_module()
    with pytest.raises(SystemExit) as rc:
        cli.main(["unknown", "--config-file", "/tmp/x.yaml"])
    assert rc.value.code == cli.EXIT_ERROR


def create_random_directory(root_directory, add_file=False):
    """Create a random directoy.

    Args:
      root_directory: Directory in which the directory must be placed.
      add_file: Create an empty file in the directory if True.

    Returns:
      full_path: Path to the created directory.

    """
    # Create the directory
    directory = Path(f"{root_directory}{os.sep}{str(uuid.uuid4())}")
    directory.mkdir(parents=True, exist_ok=True)
    full_path = str(directory.resolve())

    # Add file
    if bool(add_file):
        with open(
            Path(f"{full_path}{os.sep}{str(uuid.uuid4())}"),
            "w",
            encoding="utf-8",
        ) as _:
            pass

    return full_path
