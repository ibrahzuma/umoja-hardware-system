#!/bin/bash
# First-time provisioning for Umoja Hardware System on a fresh Ubuntu 24.04 host.
# Idempotent where reasonable: re-running should not break a working install.
set -euo pipefail

DOMAIN="${UMOJA_DOMAIN:-umoja.ehub.co.tz}"
SERVER_IP="${UMOJA_SERVER_IP:-}"
APP_DIR="/var/www/app"
REPO_URL="${UMOJA_REPO_URL:-https://github.com/ibrahzuma/umoja-hardware-system.git}"
DB_NAME="${UMOJA_DB_NAME:-umoja_db}"
DB_USER="${UMOJA_DB_USER:-postgres}"
CERT_EMAIL="${UMOJA_CERT_EMAIL:-}"

# Required secrets — must be passed in via env. Refuse to run without them.
: "${UMOJA_DB_PASSWORD:?Set UMOJA_DB_PASSWORD env before running (postgres user password)}"
: "${UMOJA_SECRET_KEY:?Set UMOJA_SECRET_KEY env before running (Django SECRET_KEY)}"
DB_PASSWORD="$UMOJA_DB_PASSWORD"
SECRET_KEY="$UMOJA_SECRET_KEY"

step() { echo; echo "==> $*"; }

export DEBIAN_FRONTEND=noninteractive

step "apt update + install system packages"
apt-get update -y
apt-get install -y \
    python3-pip python3-venv python3-dev \
    libpq-dev pkg-config libcairo2-dev libpango-1.0-0 libpangoft2-1.0-0 \
    nginx git curl redis-server postgresql postgresql-contrib

step "Enable redis + postgres"
systemctl enable --now redis-server
systemctl enable --now postgresql

step "Create Postgres database (idempotent)"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME};"
sudo -u postgres psql -c "ALTER USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
sudo -u postgres psql -c "ALTER ROLE ${DB_USER} SET client_encoding TO 'utf8';"
sudo -u postgres psql -c "ALTER ROLE ${DB_USER} SET default_transaction_isolation TO 'read committed';"
sudo -u postgres psql -c "ALTER ROLE ${DB_USER} SET timezone TO 'UTC';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};"

step "Clone or update repo at ${APP_DIR}"
mkdir -p /var/www
if [ -d "${APP_DIR}/.git" ]; then
    cd "${APP_DIR}"
    git fetch origin
    git reset --hard origin/main
else
    git clone "${REPO_URL}" "${APP_DIR}"
    cd "${APP_DIR}"
fi

step "Create virtualenv + install dependencies"
cd "${APP_DIR}"
if [ ! -d venv ]; then
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
pip install daphne

step "Write .env"
cat > "${APP_DIR}/.env" <<EOF
DEBUG=False
SECRET_KEY=${SECRET_KEY}
ALLOWED_HOSTS=${DOMAIN},${SERVER_IP}
CSRF_TRUSTED_ORIGINS=https://${DOMAIN}

DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=localhost
DB_PORT=5432

SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
EOF

step "Migrate, collectstatic, seed roles"
mkdir -p "${APP_DIR}/media"
cd "${APP_DIR}"
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py create_roles || true

step "Systemd unit for daphne"
cat > /etc/systemd/system/django_app.service <<'EOF'
[Unit]
Description=Django ASGI Application (daphne)
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/var/www/app
EnvironmentFile=/var/www/app/.env
Environment="PATH=/var/www/app/venv/bin"
ExecStart=/var/www/app/venv/bin/daphne -b 127.0.0.1 -p 8000 sms_project.asgi:application
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now django_app
systemctl restart django_app

step "Nginx site for ${DOMAIN}"
# server_tokens off hides the nginx version from the Server header + error
# pages. proxy_hide_header drops any stack-revealing headers the backend
# emits. We don't expose an OS/version anywhere.
cat > /etc/nginx/sites-available/django_app <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    server_tokens off;
    client_max_body_size 25M;

    # Strip fingerprinting headers coming from the upstream
    proxy_hide_header X-Powered-By;
    proxy_hide_header X-Runtime;
    proxy_hide_header X-AspNet-Version;

    location = /favicon.ico { access_log off; log_not_found off; }

    location /static/ {
        alias ${APP_DIR}/staticfiles/;
    }

    location /media/ {
        alias ${APP_DIR}/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/django_app /etc/nginx/sites-enabled/django_app
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

step "Fix ownership/permissions"
chown -R www-data:www-data "${APP_DIR}/media" "${APP_DIR}/staticfiles" || true
chmod -R 755 "${APP_DIR}/staticfiles" "${APP_DIR}/media" || true

step "Certbot (Let's Encrypt) for ${DOMAIN}"
if ! command -v certbot >/dev/null 2>&1; then
    apt-get install -y snapd
    snap install core
    snap refresh core
    snap install --classic certbot
    ln -sf /snap/bin/certbot /usr/bin/certbot
fi
certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${CERT_EMAIL}" --redirect || {
    echo "certbot failed; continuing with HTTP only — re-run after fixing DNS/A record."
}

step "Tighten cookie/SSL settings now that HTTPS is live"
if grep -q "umoja.ehub.co.tz" /etc/nginx/sites-available/django_app && certbot certificates 2>/dev/null | grep -q "${DOMAIN}"; then
    sed -i 's/^SECURE_SSL_REDIRECT=False/SECURE_SSL_REDIRECT=True/' "${APP_DIR}/.env"
    sed -i 's/^SESSION_COOKIE_SECURE=False/SESSION_COOKIE_SECURE=True/' "${APP_DIR}/.env"
    sed -i 's/^CSRF_COOKIE_SECURE=False/CSRF_COOKIE_SECURE=True/' "${APP_DIR}/.env"
    systemctl restart django_app
fi

step "Make deploy.sh executable for future updates"
chmod +x "${APP_DIR}/deploy.sh" || true

step "Service status"
systemctl --no-pager status django_app | head -12
systemctl --no-pager status nginx | head -8

echo
echo "Deployment finished. App should be live at: https://${DOMAIN}"
