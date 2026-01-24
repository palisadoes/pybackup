from typing import Dict, Any
from pybackup.utils.ssh import build_rsync_command
from pybackup.utils.command import execute_command


def run(config: Dict[str, Any]) -> None:
    for host in config.get("pull_servers", []):
        src = f'{host["remote_username"]}@{host["hostname"]}:{host["remote_directory"].rstrip("/")}/'
        dst = host["local_directory"].rstrip("/") + "/"
        cmd = build_rsync_command(
            src,
            dst,
            host.get("ssh_key"),
            host.get("ssh_port", 22),
            host.get("bwlimit", 40960),
            host.get("verify_host_keys", True),
        )
        result = execute_command(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"Pull failed: {result.stderr}")
