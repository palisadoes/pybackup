#!/usr/bin/env python3
"""Script to do Linux server backups.

Test

"""

# Standard imports
import shutil
import tempfile
import getpass
import copy
import socket
import locale
import subprocess
import time
import datetime
import textwrap
import argparse
import os
import sys
import pwd
import re

# PIP3 imports
import pymysql
import netifaces
import yaml

__author__ = "Peter Harrison"
__version__ = "0.0.3"


def main():
    """Process backup options.

    Args:
        None

    Returns:
        None

    """
    # Initialize key variables
    additional_help = """\
This program backs up files on this server.

"""

    # Process the CLI
    cli_args = get_cli(additional_help=additional_help)

    # Process CLI options
    if cli_args.mode == "local":
        # Do the local backup
        do_local(cli_args)

        # Do purge
        do_purge(cli_args)

    # Process CLI options
    if cli_args.mode == "push":
        # Push files to remote server via SCP
        do_push(cli_args)

    # Process CLI options
    if cli_args.mode == "pull":
        # Pull files from remote server via SCP
        do_pull(cli_args)


def do_push(cli_args):
    """Push files from this server to a remote one using recursive SCP.

    Args:
        cli_args: CLI arguments object

    Returns:
        None

    """
    # Read the config
    config = read_config(cli_args.config_file)

    # Verify we are the cluster master. Exit if not
    if "cluster_ip" in config:
        if cluster_master(config["cluster_ip"]) is False:
            # Log ignoring of database files
            log_message = (
                "Not backing up database files as this is not "
                "the cluster master. No duplication of effort with master "
                "and/or filesystem may not be mounted."
            )
            log2die("BK-0023", log_message, die=False)

            # Return
            return

    # Log success
    log_message = "Starting backup file push"
    log2die("BK-0012", log_message, die=False)

    # Cycle through each host
    for host in config["push_servers"]:
        # Remove any keys related to host. A Reinstallation can
        # cause completely new key pairs to be generated.
        # Delete any old ones as a precaution
        success = kill_keys(host.get("hostname"))
        if success is False:
            continue

        # Set bandwidth speed
        bwlimit = host.get("bwlimit", 40960)

        # Create rsync command
        if rsync(host) is True:
            # Setup command
            backup_command = f"""\
/usr/bin/rsync --bwlimit={bwlimit} --compress --archive --ignore-existing --rsh \
"ssh -i {config.get("ssh_key")} -p {host.get("ssh_port")} \
-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no" {host.get("local_directory")} \
{host.get("remote_username")}@{host.get("hostname")}:{host.get("remote_directory")}"""

        else:
            backup_command = f"""\
/usr/bin/scp -qp -l {bwlimit} -i {config.get("ssh_key")} -P {host.get("ssh_port")} \
-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no \
{host.get("local_directory")}/*.tgz \
{host.get("remote_username")}@{host.get("hostname")}:{host.get("remote_directory")}"""

        # Log start
        log_message = f'''\
Starting to push files to {host.get("hostname")}:{host.get("remote_directory")} from \
{host.get("local_directory")}. COMMAND "{backup_command}"'''
        log2die("BK-0016", log_message, die=False)

        # Create backups
        if run_script(backup_command):
            # Log success
            log_message = f"""\
Successfully pushed files to {host.get("hostname")}:\
{host.get("remote_directory")} from {host.get("local_directory")}"""
        else:
            # Log success
            log_message = f"""\
Failed to push files to {host.get("hostname")}:{host.get("remote_directory")} \
from {host.get("local_directory")}"""
        log2die("BK-0007", log_message, die=False)

    # Log success
    log_message = "Completed backup push"
    log2die("BK-0006", log_message, die=False)


def do_pull(cli_args):
    """Pull files from remote server to this one using recursive SCP.

    Args:
        cli_args: CLI arguments object

    Returns:
        None

    Logic:

        Data needs to be placed in an active cluster managed and
        mounted filesystem

        0) Verify we are running on the cluster master.
        1) If so, recursively SCP files from the remote host and directory
           to the local directory location stated in the configuration file

    """
    # Read the config
    config = read_config(cli_args.config_file)

    # Verify we are the cluster master. Exit if not
    if "cluster_ip" in config:
        if cluster_master(config["cluster_ip"]) is False:
            # Log ignoring of database files
            log_message = (
                "Not downloading remote client server bakcup files "
                "to this host. No duplication of effort with master "
                "and/or filesystem may not be mounted."
            )
            log2die("BK-0025", log_message, die=False)

            # Return
            return

    # Log success
    log_message = "Starting backup file retrieval"
    log2die("BK-0008", log_message, die=False)

    # Cycle through each host
    for host in config["pull_servers"]:
        # Remove any keys related to host. A Reinstallation can
        # cause completely new key pairs to be generated.
        # Delete any old ones as a precaution
        success = kill_keys(host.get("hostname"))
        if success is False:
            continue

        # If local_directory is a file, then delete it
        if os.path.exists(host.get("local_directory")) is True:
            if os.path.isdir(host.get("local_directory")) is False:
                os.remove(host.get("local_directory"))

        # Create local directory if if doesn't yet exist
        if os.path.exists(host.get("local_directory")) is False:
            os.makedirs(host.get("local_directory"))

        # Set bandwidth speed
        bwlimit = host.get("bwlimit", 40960)

        # Create rsync command
        if rsync(host) is True:
            # Setup command
            backup_command = f"""\
/usr/bin/rsync --bwlimit={bwlimit} --compress --archive --rsh \
"ssh -i {host.get("ssh_key")} -p {host.get("ssh_port")} \
-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no" \
{host.get("remote_username")}@{host.get("hostname")}:\
{host.get("remote_directory")}/*.tgz {host.get("local_directory")}"""
        else:
            backup_command = f"""\
/usr/bin/scp -qp -l {bwlimit} -i {host.get('ssh_key')} \
-P {host.get('ssh_port')} -o UserKnownHostsFile=/dev/null \
-o StrictHostKeyChecking=no \
{host.get('remote_username')}@{host.get('hostname')}:\
{host.get('remote_directory')}/*.tgz {host.get('local_directory')}\
"""

        # Log Start
        log_message = f"""\
Starting file retrieval from {host.get("hostname")}:\
{host.get("remote_directory")} to {host.get("local_directory")}"""
        log2die("BK-0015", log_message, die=False)

        # Create backups
        if run_script(backup_command):
            # Log success
            log_message = f"""\
Successfully retrieved files from {host.get("hostname")}:\
{host.get("remote_directory")} to {host.get("local_directory")}"""
        else:
            # Log failure
            log_message = f"""\
Failed to retrieve files from {host.get("hostname")}:\
{host.get("remote_directory")} to {host.get("local_directory")}"""
        log2die("BK-0011", log_message, die=False)

    # Log success
    log_message = "Successfully completed backup retrieval"
    log2die("BK-0013", log_message, die=False)


def do_local(cli_args):
    """Do local backups.

    Args:
        cli_args: CLI arguments object

    Returns:
        None

    Logic:
        0) Read config
        1) Create a suffix for the names of the database backups
        2) Create a list of files / directories to backup
        3) Backup MySQL databases to files with suffixes above
        4) Add the list of MySQL backup files to the backup files
           list found in the config file
        5) Backup the files using "tar". Place the resulting
           file in the backup directory specified in the configuration file

    """
    # Log start of backup
    log_message = "Starting local backup."
    log2die("BK-0018", log_message, die=False)

    # Read the config
    config = read_config(cli_args.config_file)

    # Make sure backup directory exists
    backup_dir = config["backup_dir"].rstrip("/")
    if os.path.isdir(backup_dir) is False:
        log_message = f"""Backup directory {backup_dir} does not exist!"""
        log2die("BK-0024", log_message)

    # Create temporary directory
    mysql_tmp_dir = f"{backup_dir}/mysql_backup_tmp_{socket.gethostname()}"
    if os.path.isdir(mysql_tmp_dir) is False:
        os.makedirs(mysql_tmp_dir)

        # Log directory creation
        log_message = f"Creating temporary backup directory {mysql_tmp_dir}"
        log2die("BK-0021", log_message, die=False)

    # Create time string to be used in each database's backup filename
    time_object = datetime.datetime.fromtimestamp(time.time())
    timestring = time_object.strftime("_%Y%m%d")

    # Create a list of items to backup
    file_list = copy.deepcopy(config["directory"])

    # Verify we are the cluster master. Backup databases if True
    if "cluster_ip" in config:
        if cluster_master(config["cluster_ip"]) is True:
            # Get list of MySQL tgz files to backup
            backup_mysql(
                config=config, suffix=timestring, backup_dir=mysql_tmp_dir
            )
        else:
            # Log ignoring of database files
            log_message = (
                "Not backing up database files as this is not "
                "the cluster master. No duplication of effort with master "
                "and/or filesystem may not be mounted."
            )
            log2die("BK-0017", log_message, die=False)
    else:
        # Mention no backup of database files
        log_message = (
            "Not backing up database files as no cluster_ip found in "
            "configuratiion"
        )
        log2die("BK-0019", log_message, die=False)

    # Add the temporary db backup directory to the file list
    file_list.append(f"{mysql_tmp_dir}/")

    # Do backup
    backup_command = backup_files(
        config=config, file_list=file_list, suffix=timestring
    )

    # Delete our sql files if they exist
    db_filelist = [
        db_bkp_filename
        for db_bkp_filename in os.listdir(mysql_tmp_dir)
        if db_bkp_filename.endswith("gz")
    ]
    for db_bkp_filename in db_filelist:
        os.remove(f"{mysql_tmp_dir}/{db_bkp_filename}")

        # Log success of extraneous file deletion
        log_message = f'''\
Successfully removed temporary database file: "{db_bkp_filename}"'''
        log2die("BK-0014", log_message, die=False)

    # Delete any files that may be in mysql_tmp_dir
    # from previous failed backups
    shutil.rmtree(mysql_tmp_dir)
    log_message = f"Removing temporary backup directory {mysql_tmp_dir}"
    log2die("BK-0022", log_message, die=False)

    # Log success
    log_message = f'Successfully completed backup: "{backup_command}"'
    log2die("BK-0003", log_message, die=False)


def do_purge(cli_args):
    """Purge files in the local directory if defined.

    Args:
        cli_args: CLI arguments object

    Returns:
        None

    Logic:

        Data needs to be placed in an active cluster managed and
        mounted filesystem

        0) Verify we are running on the cluster master.
        1) If so, recursively SCP files from the remote host and directory
           to the local directory location stated in the configuration file

    """
    # Read the config
    config = read_config(cli_args.config_file)

    # Initialize key variables
    folder = config["backup_dir"]
    now = int(time.time())
    desired_age = None

    # Do purge
    if cli_args.max_age is not None:
        # Log success
        log_message = "Starting local directory backup file purge."
        log2die("BK-0026", log_message, die=False)

        # Delete files in folder
        if os.path.exists(folder) is True and os.path.isdir(folder) is True:
            for filename in os.listdir(folder):
                # Get file path and age
                file_path = os.path.join(folder, filename)
                file_age = int(os.path.getmtime(file_path))

                # Delete if file age is exceeded
                if cli_args.max_age == 0:
                    desired_age = 1
                else:
                    desired_age = cli_args.max_age
                if now - file_age > abs(desired_age) * 86400:
                    if os.path.isfile(file_path) and file_path.endswith(
                        ".tgz"
                    ):
                        os.unlink(file_path)

            # Log success
            log_message = "Local directory backup file purge completed."
            log2die("BK-0027", log_message, die=False)
        else:
            # Log success
            log_message = f"{folder} does not exist or is not a directory."
            log2die("BK-0028", log_message, die=True)


def backup_files(config=None, file_list=None, suffix=""):
    """Backup file_list files using 'tar'. Place resulting file in backup_dir.

    Args:
        backup_filename: Name of file to backup
        file_list: List of files and directories to backup
        suffix: Suffix to append to tar file

    Returns:
        backup_command: String used to complete backup.

    """
    # Initialize key variables
    backup_dir = config["backup_dir"].rstrip("/")
    backup_user = pwd.getpwnam(config["backup_user"]).pw_uid

    # Make sure backup directory exists
    if os.path.isdir(backup_dir) is False:
        log_message = f"""Backup directory "{backup_dir}" does not exist!"""
        log2die("BK-0005", log_message)

    # Create backup filename
    backup_filename = f"""\
{backup_dir}/{socket.getfqdn(socket.gethostname())}{suffix}.tgz"""

    # Make sure each file exists
    for filename in file_list:
        if os.path.exists(filename) is False:
            log_message = f"""\
File or Directory named "{filename}" does not exist!"""
            log2die("BK-0001", log_message)

    # Create string of files to backup. Remove leading slashes
    for idx, val in enumerate(file_list):
        file_list[idx] = val.lstrip("/")
    all_files = " ".join(file_list)

    # Create an exclude file and do the rest
    if "exclude" in config:
        if isinstance(config.get("exclude"), list) is True:
            exclude_list = config.get("exclude")
        else:
            exclude_list = []
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, prefix="daily_backup_", suffix=".exclude"
        ) as f_handle:

            # Get the exclude filename
            exclude_file = f_handle.name

            # Write to the temp file. Add any exludes if necessary
            f_handle.write("#\n")
            for exclude in exclude_list:
                exclude = exclude.strip("'")
                exclude = exclude.strip('"')
                exclude = exclude.lstrip("/")
                f_handle.write(f"{exclude}\n")

        # Create string to execute backup
        backup_command = f"""\
/bin/nice /bin/tar --ignore-failed-read --create --gzip  --directory / \
--exclude-vcs \
--warning=no-file-changed --exclude-from {exclude_file}\
--file {backup_filename} {all_files}"""

    else:
        # Create string to execute backup
        backup_command = f"""\
/bin/nice /bin/tar --ignore-failed-read --create --gzip --directory / \
--warning=no-file-changed --file {backup_filename} {all_files}"""

    # Create backups
    run_script(backup_command)

    # Change ownership of tar ball
    os.chown(backup_filename, backup_user, -1)

    # Change permissions of tar ball
    os.chmod(backup_filename, 0o640)

    # Delete exclude file after running the backup.
    if "exclude" in config:
        os.remove(exclude_file)

    # Return
    return backup_command


def backup_mysql(config=None, suffix="", backup_dir=None):
    """Backup MySQL / MariaDB databases.

    Args:
        config: Configuration dict
        suffix: Suffix to append to tar file

    Returns:
        db_backup_files: List of database backup files created

    """
    # Initialize key variables
    db_backup_files = []
    db_name_dict = {}

    # Assign variables for ease of readability
    db_user = config.get("mysql_user")
    db_pass = config.get("mysql_pass")
    db_sock = config.get("mysql_sock")

    # Make sure backup directory exists
    if os.path.isdir(backup_dir) is False:
        log_message = f"""\
Backup directory {backup_dir} does not exist! DB backup abandoned."""
        log2die("BK-0020", log_message)

    # Do nothing if there is no mysql configuration
    if (db_user is None) or (db_pass is None) or (db_sock is None):
        log_message = """\
Parameters mysql_user, mysql_pass, \
mysql_sock not in configuration. DB backup abandoned."""
        log2die("BK-0029", log_message, die=False)
        return db_backup_files

    # Do nothing if socket not available
    if os.path.exists(db_sock) is False:
        log_message = f"""\
Socket file {db_sock} not found. DB backup abandoned."""
        log2die("BK-0031", log_message, die=False)
        return db_backup_files

    # Assign an object for the pid
    db_daemon = config.get("mysql_daemon_name", "mysqld")
    mysql_pidof = f"pidof {db_daemon}"
    mysql_process = subprocess.Popen(
        mysql_pidof, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    mysql_process.communicate()
    mysql_returncode = mysql_process.returncode

    # Do nothing if the mysql pid does not exist
    if mysql_returncode != 0:
        log_message = f"""\
Database daemon {db_daemon} not found. DB backup abandoned."""
        log2die("BK-0032", log_message, die=False)
        return False

    # Create a database object
    database = pymysql.connect(
        host="localhost",
        user=db_user,
        passwd=db_pass,
        unix_socket=db_sock,
        db="mysql",
    )

    # Create cursor
    cursor = database.cursor()

    try:
        # Execute the SQL command
        sql_statement = "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA"
        cursor.execute(sql_statement)
        query_results = cursor.fetchall()

    except Exception as exception_error:
        log_message = f'''\
Unable to fetch data from database. SQL statement: \
"{sql_statement}" Error: "{exception_error}"'''
        log2die("BK-0010", log_message)
    except:
        log_message = f'Unexpected exception. SQL statement: "{sql_statement}"'
        log2die("BK-0009", log_message)

    # Disconnect from server
    database.close()

    if query_results:
        # Create backup filenames and not the database names
        for row in query_results:
            if not re.search("#mysql50#", row[0]):
                backup_filename = f"{backup_dir}/{row[0]}{suffix}.sql.gz"
                db_backup_files.append(backup_filename)
                db_name_dict[row[0]] = backup_filename
    else:
        db_backup_files = []

    # Backup using mysql dump
    for db_name, _ in sorted(db_name_dict.items()):
        log_message = f"""\
Backing up {db_name} database to temporary directory {backup_dir}."""
        log2die("BK-0030", log_message, die=False)

        db_backup_command = f"""\
/usr/bin/mysqldump --single-transaction --lock-tables \
-u {db_user} -p"{db_pass}" {db_name} | /bin/gzip -9 > \
{db_name_dict.get(db_name)}"""

        # Create backups
        run_script(db_backup_command)

    # Return
    return db_backup_files


def run_script(cli_string):
    """Run the cli_string UNIX CLI command and record output.

    Args:
        None

    Returns:
        None

    """
    # Initialize key variables
    encoding = locale.getlocale()[1]
    header_returncode = "[Return Code]"
    header_stdout = "[Output]"
    header_stderr = "[Error Message]"
    header_bad_cmd = "[ERROR: Bad Command]"

    # Create the subprocess object
    process = subprocess.Popen(
        cli_string, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdoutdata, stderrdata = process.communicate()
    returncode = process.returncode

    # Don't crash if tar command gives warning errors
    if cli_string.startswith("/bin/nice /bin/tar") is True and returncode != 2:
        # Return
        return stdoutdata

    # Crash if the return code is not 0
    if returncode != 0:
        # Print the Return Code header, Return Code, STDOUT header
        string2print = f"""\
{header_bad_cmd}\n{cli_string}\n\n{header_returncode}\n{returncode}\n"""
        print(string2print)

        # Print the STDERR
        string2print = f"{header_stderr}"
        print(string2print)
        for line in stderrdata.decode(encoding).split("\n"):
            string2print = f"{line}"
            print(string2print)

        # Print the STDOUT
        string2print = f"{header_stdout}"
        print(string2print)
        for line in stdoutdata.decode(encoding).split("\n"):
            string2print = f"{line}"
            print(string2print)

        # Print the STDOUT
        string2print = (
            "#############################################################\n"
            "#############################################################\n"
            "# WARNING: ALL OTHER BACKUPS RUN BY THIS SCRIPT HAVE ABORTED.\n"
            "#############################################################\n"
            "#############################################################\n"
        )
        # print(string2print)

        # All done
        # sys.exit(2)
        return False

    # Return
    return stdoutdata


def get_cli(additional_help=None):
    """Return all the CLI options.

    Args:
        None

    Returns:
        args: Namespace() containing all of our CLI arguments as objects
            - filename: Path to the configuration file

    """
    # Initialize key variables
    width = 80

    # Header for the help menu of the application
    parser = argparse.ArgumentParser(
        description=additional_help,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # Add subparser
    subparsers = parser.add_subparsers(dest="mode")

    # Parse "local", return object used for parser
    cli_local(subparsers, width=width)

    # Parse "push", return object used for parser
    cli_push(subparsers, width=width)

    # Parse "pull", return object used for parser
    cli_pull(subparsers, width=width)

    # Return the CLI arguments
    args = parser.parse_args()

    # Print help if mode is None
    if args.mode is None:
        parser.print_help()
        sys.exit(2)

    # Return our parsed CLI arguments
    return args


def cli_local(subparsers, width=80):
    """Process local CLI commands.

    Args:
        subparsers: Subparsers object
        width: Width of the help text string to STDIO before wrapping

    Returns:
        None

    """
    # Initialize key variables
    parser = subparsers.add_parser(
        "local", help=textwrap.fill("Do local backup.", width=width)
    )

    # CLI argument for the config directory
    parser.add_argument(
        "--config_file",
        dest="config_file",
        required=True,
        default=None,
        type=str,
        help=textwrap.fill("Config file to use.", width=width),
    )

    # CLI argument for the config directory
    parser.add_argument(
        "--max_age",
        dest="max_age",
        required=False,
        default=None,
        type=int,
        help=textwrap.fill(
            "Purge .tgz files that exceed this maximum age in days.",
            width=width,
        ),
    )


def cli_push(subparsers, width=80):
    """Process push CLI commands.

    Args:
        subparsers: Subparsers object
        width: Width of the help text string to STDIO before wrapping

    Returns:
        None

    """
    # Initialize key variables
    parser = subparsers.add_parser(
        "push",
        help=textwrap.fill(
            "Recursively SCP copy files from this server to a remote one.",
            width=width,
        ),
    )

    # CLI argument for the config directory
    parser.add_argument(
        "--config_file",
        dest="config_file",
        required=True,
        default=None,
        type=str,
        help=textwrap.fill("Config file to use.", width=width),
    )


def cli_pull(subparsers, width=80):
    """Process pull CLI commands.

    Args:
        subparsers: Subparsers object
        width: Width of the help text string to STDIO before wrapping

    Returns:
        None

    """
    # Initialize key variables
    parser = subparsers.add_parser(
        "pull",
        help=textwrap.fill(
            "Recursively SCP copy files to this server from a remote one.",
            width=width,
        ),
    )

    # CLI argument for the config directory
    parser.add_argument(
        "--config_file",
        dest="config_file",
        required=True,
        default=None,
        type=str,
        help=textwrap.fill("Config file to use.", width=width),
    )


def rsync(host):
    """Determine whether this host requires rsync file copying.

    Args:
        host: Dict of host metadata

    Returns:
        do_rsync: True if rsync is required

    """
    # Initialize key variables
    do_rsync = True

    # Make determination
    if "scp" not in host:
        do_rsync = True
    else:
        if host["scp"] is True:
            do_rsync = False

    # Return
    return do_rsync


def kill_keys(hostname):
    """Remove keys from ~/.ssh/known_hosts for a specific host.

    Args:
        hostname: Hostname to process

    Returns:
        None

    """
    # Initialize key variables
    success = True

    # Remove any keys related to host. A Reinstallation can
    # cause completely new key pairs to be generated.
    # Delete any old ones as a precaution
    try:
        ip_address = socket.getfqdn(socket.gethostbyname(hostname))
    except:
        success = False

    if success is True:
        keygen_commands = [
            f"ssh-keygen -R {hostname}",
            f"ssh-keygen -R {ip_address}",
        ]
        for keygen_command in keygen_commands:
            success = run_script(keygen_command)

    # Return
    return success


def read_config(filename=None):
    """Read the configuration file.

    Args:
        None

    Returns:
        config_dict: dictionary of values found in file

    """
    # Initialize key variables
    yaml_from_file = ""

    # Check if config_directory exists
    if os.path.isfile(filename) is False:
        log_message = f'Configuration file "{filename}" does not exist!'
        log2die("BK-0004", log_message)

    # Verify YAML files found in directory
    if filename.endswith(".yaml") is False:
        log_message = f"""\
Configuration file "{filename}" does not end with ".yaml" extension."""
        log2die("BK-0002", log_message)

    # Read file and add to string
    with open(filename, "r", encoding="utf-8") as file_handle:
        yaml_from_file = file_handle.read()

    # Return
    config_dict = yaml.safe_load(yaml_from_file)
    return config_dict


def cluster_master(ipaddress):
    """Determine whether this host is a cluster master based on IP address.

    Args:
        ipaddress: IP address to test

    Returns:
        master: True if this is the master

    """
    # Initialize key variables
    master = False
    addresses = []

    # Get IP addresses of host
    for interface in netifaces.interfaces():
        if netifaces.AF_INET in netifaces.ifaddresses(interface):
            for link in netifaces.ifaddresses(interface)[netifaces.AF_INET]:
                addresses.append(link["addr"])

    # Determine if found
    if ipaddress in addresses:
        master = True

    # Return
    return master


def log2die(code=None, message=None, die=True):
    """Log message to screen and die.

    Args:
        code: Error code
        message: Error message

    Returns:
        None

    """
    # Initialize key variables
    app_name = "daily_backup"
    username = getpass.getuser()
    time_object = datetime.datetime.fromtimestamp(time.time())
    timestring = time_object.strftime("%Y-%m-%d %H:%M:%S,%f")

    # Format string for error message
    prefix = f"{timestring} - {app_name} - DEBUG"
    suffix = f"[{username}] ({code}): {message}"

    if die is True:
        # Print and die
        error = f"{prefix} - ERROR - {suffix}"
        print(error)
        log2file(message=error)
        sys.exit(2)
    else:
        # Append to backup file
        error = f"{prefix} - STATUS - {suffix}"
        log2file(message=error)


def log2file(message=None, filename="/var/log/backups/backups.log"):
    """Log message to file.

    Args:
        message: Error message
        filename:

    Returns:
        None

    """
    # Create log directory if necessary
    directory = os.path.dirname(filename)
    if os.path.isdir(directory) is False:
        os.makedirs(directory, 0o750)

    # Write to file
    if os.path.isfile(filename) is True:
        with open(filename, "a", encoding="utf-8") as f_handle:
            f_handle.write(f"\n{message}")
    else:
        with open(filename, "w", encoding="utf-8") as f_handle:
            f_handle.write(f"\n{message}")


if __name__ == "__main__":
    main()
