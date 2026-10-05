#!/bin/bash

# Configuration
APP_DIR="/var/www/app"
VENV_DIR="$APP_DIR/venv"
SERVICE_NAME="django_app"

echo "🚀 Starting Deployment Process..."

# Navigate to project directory
cd $APP_DIR || { echo "❌ Directory $APP_DIR not found"; exit 1; }

# Check Redis
echo "🔍 Checking Redis service..."
if ! systemctl is-active --quiet redis-server; then
    echo "⚠️ Redis is not running. Attempting to start..."
    sudo systemctl start redis-server
fi

# Pull latest code
echo "📥 Pulling latest code from GitHub..."
git pull origin main

# Activate virtual environment
source $VENV_DIR/bin/activate

# Install dependencies
echo "📦 Installing requirements..."
pip install -r requirements.txt

# Run migrations
echo "⚙️ Running database migrations..."
python manage.py migrate --noinput

# Open the books: the base currency, a financial year, the six voucher types
# and the chart of accounts. Idempotent — on a system already running it
# checks and does nothing, so it is safe on every deploy.
echo "📒 Checking the accounting books are open..."
python manage.py open_books

# Collect static files
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput

# Fix permissions
echo "🔒 Setting permissions for static and media files..."
sudo chown -R $USER:$USER $APP_DIR
sudo chmod -R 755 $APP_DIR/static $APP_DIR/media

# Restart Systemd Service (Daphne)
echo "🔄 Restarting Daphne service ($SERVICE_NAME)..."
sudo systemctl restart $SERVICE_NAME

# Restart Nginx
echo "🌐 Restarting Nginx..."
sudo systemctl restart nginx

# Verify status
echo "📊 Checking service status..."
sudo systemctl status $SERVICE_NAME --no-pager

echo "✅ Deployment Finished Successfully!"
