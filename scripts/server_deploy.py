import paramiko
import time
import os

# Server details
HOSTNAME = "172.236.209.216"
USERNAME = "ibrahimu"
PASSWORD = "allahu(SW)1"
DB_PASS = "allahu(SW)1"
SUPERUSER_PASS = "Umoja@2026"

def run_remote_command(ssh, command, sudo=False):
    if sudo:
        command = f"echo '{PASSWORD}' | sudo -S {command}"
    
    # print(f"Executing: {command}")
    stdin, stdout, stderr = ssh.exec_command(command)
    
    # Wait for command to complete
    exit_status = stdout.channel.recv_exit_status()
    output = stdout.read().decode().strip()
    error = stderr.read().decode().strip()
    
    return exit_status, output, error

def migrate():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        print(f"Connecting to {HOSTNAME}...")
        ssh.connect(HOSTNAME, username=USERNAME, password=PASSWORD)
        print("Connected.")

        # Determine directory
        dir_exists, _, _ = run_remote_command(ssh, "[ -d /var/www/app ] && echo 'exists'")
        dir_path = "/var/www/app" if 'exists' in dir_exists else "/var/www/umoja-hardware-system"
        print(f"Target directory: {dir_path}")

        # Create the migration shell script on the server
        print("Creating migration script on server...")
        shell_script = f"""#!/bin/bash
set -e

# Function to wait for apt lock
wait_for_apt_lock() {{
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
        echo "Waiting for apt lock..."
        sleep 5
    done
}}

echo "Starting migration process..."

# 1. Install PostgreSQL
wait_for_apt_lock
apt-get update
apt-get install postgresql postgresql-contrib libpq-dev -y

# 2. Setup Database
echo "Setting up Database..."
sudo -u postgres psql -c "CREATE DATABASE umoja_db;" || echo "Database might already exist"
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD '{DB_PASS}';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE umoja_db TO postgres;"

# 3. Pull latest code
echo "Pulling latest code..."
cd {dir_path}
git fetch --all
git reset --hard origin/main

# 4. Update .env
echo "Updating .env..."
cat <<EOF > .env
DEBUG=False
SECRET_KEY=97mo#$70gwckll(^rf%=o%ad4y*a(93en2ya5p%7(4-^6%el8j
ALLOWED_HOSTS=umoja.ehub.co.tz,172.236.209.216
CSRF_TRUSTED_ORIGINS=https://umoja.ehub.co.tz

DB_NAME=umoja_db
DB_USER=postgres
DB_PASSWORD='{DB_PASS}'
DB_HOST=localhost
DB_PORT=5432

SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
EOF

# 5. Migration & Superuser
echo "Running Django migrations..."
source venv/bin/activate
pip install psycopg2-binary
python manage.py migrate --noinput

echo "Creating superuser..."
python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(username='umoja').delete(); User.objects.create_superuser('umoja', 'admin@example.com', '{SUPERUSER_PASS}')"

# 6. Restart services
echo "Restarting services..."
systemctl restart django_app
systemctl restart nginx

echo "Migration completed successfully!"
"""
        # Save shell script to server
        ssh.exec_command(f"echo \"{shell_script}\" > /tmp/migrate_to_pg.sh")
        ssh.exec_command("chmod +x /tmp/migrate_to_pg.sh")

        # Execute shell script with sudo
        print("Executing migration script with sudo...")
        status, out, err = run_remote_command(ssh, "/tmp/migrate_to_pg.sh", sudo=True)
        
        print("Output:\n", out)
        if status != 0:
            print(f"Migration script failed with status {status}: {err}")
        else:
            print("Migration script completed successfully!")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        ssh.close()

if __name__ == "__main__":
    migrate()
