"""Run a shell command or upload a file to the production server over SSH.

Usage:
    python scripts/ssh_deploy.py exec "<command>"
    python scripts/ssh_deploy.py put <local_path> <remote_path>

Reads SSH host/user/password from env: SSH_HOST, SSH_USER, SSH_PASS.
"""

from __future__ import annotations

import io
import os
import sys

import paramiko

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def _connect() -> paramiko.SSHClient:
    host = os.environ["SSH_HOST"]
    user = os.environ["SSH_USER"]
    password = os.environ["SSH_PASS"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=user,
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=60,
        banner_timeout=60,
        auth_timeout=60,
    )
    return client


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    mode = sys.argv[1]
    if mode == "put":
        if len(sys.argv) != 4:
            print("usage: ssh_deploy.py put <local> <remote>", file=sys.stderr)
            return 2
        local, remote = sys.argv[2], sys.argv[3]
        client = _connect()
        sftp = client.open_sftp()
        sftp.put(local, remote)
        sftp.close()
        client.close()
        print(f"uploaded {local} -> {remote}")
        return 0

    if mode != "exec":
        print(__doc__, file=sys.stderr)
        return 2

    command = sys.argv[2]
    client = _connect()

    transport = client.get_transport()
    assert transport is not None
    channel = transport.open_session()
    channel.get_pty()
    channel.exec_command(command)

    while True:
        if channel.recv_ready():
            sys.stdout.write(channel.recv(4096).decode(errors="replace"))
            sys.stdout.flush()
        if channel.recv_stderr_ready():
            sys.stderr.write(channel.recv_stderr(4096).decode(errors="replace"))
            sys.stderr.flush()
        if channel.exit_status_ready():
            while channel.recv_ready():
                sys.stdout.write(channel.recv(4096).decode(errors="replace"))
            while channel.recv_stderr_ready():
                sys.stderr.write(channel.recv_stderr(4096).decode(errors="replace"))
            break

    exit_code = channel.recv_exit_status()
    sys.stdout.flush()
    sys.stderr.flush()
    client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
