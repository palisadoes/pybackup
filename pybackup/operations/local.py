from pathlib import Path
from typing import List, Optional
from pybackup.utils.logging_config import log_status
from pybackup.utils.filesystem import ensure_directory
from pybackup.constants import BACKUP_FILE_PERMISSIONS


class LocalBackupManager:
    def __init__(self, backup_dir: str):
        self.backup_dir = Path(backup_dir)

    def run(self, max_age: Optional[int] = None) -> None:
        ensure_directory(self.backup_dir)
        log_status("BK-1000", f"Local backup to {self.backup_dir}")
        # TODO: call filesystem tar, mysql backup, purge, etc.
