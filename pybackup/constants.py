"""Constants for pybackup.

This module contains all constants used throughout the pybackup package,
organized by category for easy maintenance and reference.
"""

# Exit Codes
EXIT_SUCCESS = 0
EXIT_ERROR = 2

# File Permissions (octal)
BACKUP_FILE_PERMISSIONS = 0o640  # Owner read/write, group read
LOG_DIR_PERMISSIONS = 0o750  # Owner rwx, group rx, others none

# Network Defaults
DEFAULT_BANDWIDTH_LIMIT = 40960  # 40 MB/s (in kilobytes per second)
DEFAULT_SSH_PORT = 22

# MySQL/Database Defaults
DEFAULT_MYSQL_DAEMON = "mysqld"
MYSQL_DEFAULT_HOST = "localhost"
MYSQL_DEFAULT_DB = "mysql"

# Compression Settings
GZIP_COMPRESSION_LEVEL = 9  # Maximum compression (1-9 scale)

# Time Constants
SECONDS_PER_DAY = 86400  # 24 hours * 60 minutes * 60 seconds

# CLI Configuration
CLI_WIDTH = 80  # Width for help text wrapping in argparse

# Paths
DEFAULT_LOG_PATH = "/var/log/backups/backups.log"

# Binary Paths (configurable but with sensible defaults)
TAR_BINARY = "/bin/tar"
NICE_BINARY = "/bin/nice"
GZIP_BINARY = "/bin/gzip"
RSYNC_BINARY = "/usr/bin/rsync"
SCP_BINARY = "/usr/bin/scp"
MYSQLDUMP_BINARY = "/usr/bin/mysqldump"
SSH_KEYGEN_BINARY = "ssh-keygen"

# Application Metadata
APP_NAME = "pybackup"

# File Extensions
BACKUP_FILE_EXTENSION = ".tgz"
SQL_BACKUP_EXTENSION = ".sql.gz"
YAML_EXTENSION = ".yaml"
EXCLUDE_FILE_PREFIX = "daily_backup_"
EXCLUDE_FILE_SUFFIX = ".exclude"

# Tar Options
TAR_RETURN_CODE_FATAL = 2  # Tar return code 2 indicates fatal error
# Return code 1 is just a warning (file changed during backup)

# MySQL Backup
MYSQL_REGEX_SKIP_PATTERN = "#mysql50#"  # Skip databases with this pattern

# SSH Options
SSH_STRICT_HOST_CHECKING_DISABLED = (
    "-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
)
