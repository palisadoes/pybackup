from typing import List, Optional
from pybackup.constants import RSYNC_BINARY, SCP_BINARY


def build_rsync_command(
    source: str,
    dest: str,
    ssh_key: Optional[str],
    port: int,
    bwlimit: int,
    verify_host_keys: bool = True,
) -> List[str]:
    rsh = ["ssh", "-p", str(port)]
    if ssh_key:
        rsh += ["-i", ssh_key]
    if not verify_host_keys:
        rsh += [
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "StrictHostKeyChecking=no",
        ]
    return [
        RSYNC_BINARY,
        "--compress",
        "--archive",
        f"--bwlimit={bwlimit}",
        "--rsh",
        " ".join(rsh),
        source,
        dest,
    ]


def build_scp_command(
    source: str,
    dest: str,
    ssh_key: Optional[str],
    port: int,
    bwlimit: int,
    verify_host_keys: bool = True,
) -> List[str]:
    cmd = [SCP_BINARY, "-P", str(port)]
    if ssh_key:
        cmd += ["-i", ssh_key]
    if not verify_host_keys:
        cmd += [
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "StrictHostKeyChecking=no",
        ]
    # scp has no native bwlimit portable across versions; prefer rsync when possible
    cmd += [source, dest]
    return cmd
