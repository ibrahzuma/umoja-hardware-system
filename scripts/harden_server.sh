#!/bin/bash
# Apply runtime security hardening on the live server:
#  - nginx: server_tokens off + strip stack headers
#  - .env: obscure ADMIN_URL
#  - collectstatic + restart services
set -e

NGINX_CONF=/etc/nginx/sites-available/django_app
ENV_FILE=/var/www/app/.env

echo "==> patch nginx"
if ! grep -q "server_tokens off;" "$NGINX_CONF"; then
    sed -i '/client_max_body_size 25M;/a\    server_tokens off;\n    proxy_hide_header X-Powered-By;\n    proxy_hide_header X-Runtime;\n    proxy_hide_header X-AspNet-Version;' "$NGINX_CONF"
    echo "   added server_tokens + proxy_hide_header"
else
    echo "   already patched"
fi
nginx -t

echo "==> set ADMIN_URL in .env"
if ! grep -q "^ADMIN_URL=" "$ENV_FILE"; then
    echo "ADMIN_URL=umoja-ops-k7x92m/" >> "$ENV_FILE"
    echo "   added"
else
    echo "   already set"
fi
grep "^ADMIN_URL=" "$ENV_FILE"

echo "==> collectstatic + restart"
cd /var/www/app
venv/bin/python manage.py collectstatic --noinput 2>&1 | tail -2
systemctl restart django_app
systemctl reload nginx
echo "DEPLOYED"
