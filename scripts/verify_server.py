import paramiko
import os

HOSTNAME = "172.236.209.216"
USERNAME = "ibrahimu"
PASSWORD = "allahu(SW)1"

def verify():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(HOSTNAME, username=USERNAME, password=PASSWORD)
        print(f"--- Verification Report for {HOSTNAME} ---")

        # 1. Check PostgreSQL service
        print("1. Checking PostgreSQL service...")
        _, stdout, _ = ssh.exec_command("systemctl is-active postgresql")
        print(f"   Status: {stdout.read().decode().strip()}")

        # 2. Check if umoja_db exists
        print("2. Checking for umoja_db...")
        # Need sudo for psql -l usually or use -U postgres
        # We'll use the sudo echo pattern again but carefully
        stdin, stdout, stderr = ssh.exec_command(f"echo '{PASSWORD}' | sudo -S -u postgres psql -l")
        out = stdout.read().decode()
        if "umoja_db" in out:
            print("   Result: umoja_db exists.")
        else:
            print(f"   Result: umoja_db NOT FOUND.")
            # print(f"   Debug Out: {out}")
            # print(f"   Debug Err: {stderr.read().decode()}")

        # 3. Check Django app service
        print("3. Checking django_app service...")
        _, stdout, _ = ssh.exec_command("systemctl is-active django_app")
        print(f"   Status: {stdout.read().decode().strip()}")

        # 4. Check .env file
        print("4. Checking .env file for DB settings...")
        dir_exists, stdout, _ = ssh.exec_command("[ -d /var/www/app ] && echo 'app' || echo 'umoja'")
        dir_name = stdout.read().decode().strip()
        path = f"/var/www/{dir_name if dir_name == 'app' else 'umoja-hardware-system'}/.env"
        stdin, stdout, stderr = ssh.exec_command(f"echo '{PASSWORD}' | sudo -S grep 'DB_NAME' {path}")
        print(f"   .env check: {stdout.read().decode().strip()}")

        # 5. Check migrations
        print("5. Checking migrations status...")
        cmd = f"cd /var/www/{dir_name if dir_name == 'app' else 'umoja-hardware-system'} && source venv/bin/activate && python manage.py migrate --check"
        _, stdout, stderr = ssh.exec_command(cmd)
        err = stderr.read().decode()
        if "Applied" in err or not err.strip():
            print("   Status: Migrations are up to date.")
        else:
            print(f"   Status: Pending migrations or error: {err.strip()}")

        ssh.close()
    except Exception as e:
        print(f"Error during verification: {e}")

if __name__ == "__main__":
    verify()
