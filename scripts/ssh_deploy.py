"""Run a shell command or upload a file to the production server over SSH.

Usage:
    python scripts/ssh_deploy.py exec "<command>"
    python scripts/ssh_deploy.py put <local_path> <remote_path>

Credentials come from the environment, or from `.env` when the environment
does not set them — `.env` is gitignored, so that keeps the password out of
shell history and off the command line:

    SSH_HOST=umoja.ehub.co.tz
    SSH_USER=root
    SSH_PASS=...            # or SSH_KEY=/path/to/private_key
    SSH_PORT=22             # optional
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


def _from_env_file() -> dict:
    """SSH settings out of `.env`, for the keys the environment has not set.

    `.env` is gitignored and is already where the database password lives, so
    it is the natural home for these too — and it keeps the password off the
    command line, where it would otherwise end up in a shell history.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    found: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key in ("SSH_HOST", "SSH_USER", "SSH_PASS", "SSH_KEY", "SSH_PORT"):
                    found[key] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return found


def _setting(name: str, settings: dict, required: bool = True) -> str | None:
    value = os.environ.get(name) or settings.get(name)
    if not value and required:
        raise SystemExit(
            f"{name} is not set. Put SSH_HOST, SSH_USER and either SSH_PASS or SSH_KEY "
            f"in .env (which is gitignored), or export them."
        )
    return value or None


def _connect() -> paramiko.SSHClient:
    settings = _from_env_file()
    host = _setting("SSH_HOST", settings)
    user = _setting("SSH_USER", settings)
    password = _setting("SSH_PASS", settings, required=False)
    key_path = _setting("SSH_KEY", settings, required=False)
    port = int(_setting("SSH_PORT", settings, required=False) or 22)
    if not password and not key_path:
        raise SystemExit(
            "Neither SSH_PASS nor SSH_KEY is set. Put one of them in .env "
            "(which is gitignored), or export it."
        )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        # A key is used when one is named; otherwise this stays password-only,
        # so a stray agent key cannot silently be tried instead.
        key_filename=key_path,
        look_for_keys=bool(key_path),
        allow_agent=bool(key_path),
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
