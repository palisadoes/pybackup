# test/bin/test_pybackup_cli.py
"""
CLI tests for bin/pybackup.py.

These tests load the CLI module directly from its file path and exercise
argument parsing, config loading, and dispatch glue using monkeypatches to
avoid real side effects.
"""

from __future__ import annotations


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

    def _fake_load_config(path):
        return {
            "backup_dir": str(tmp_path / "out"),
            "backup_user": "backup",
            "directory": [],
            "exclude": [],
            "cluster_ip": None,
            "push_servers": [],
            "pull_servers": [],
        }

    called = {}

    def _fake_run_local(cfg, args):
        called["ran"] = True
        return cli.EXIT_SUCCESS

    monkeypatch.setattr(cli, "load_config", _fake_load_config)
    monkeypatch.setattr(cli, "run_local", _fake_run_local)

    rc = cli.main(
        [
            "local",
            "--config-file",
            str(tmp_path / "c.yaml"),
            "--max-age",
            "1",
            "--log-file",
            str(tmp_path / "t.log"),
        ]
    )
    assert rc == cli.EXIT_SUCCESS
    assert called.get("ran") is True


def test_main_unknown_command_returns_error(load_cli_module) -> None:
    """Test that an unknown subcommand results in an error exit code."""
    cli = load_cli_module()
    rc = cli.main(["unknown", "--config-file", "/tmp/x.yaml"])
    assert rc == cli.EXIT_ERROR
