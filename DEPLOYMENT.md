# Deployment Guide: GitHub & Linode

This guide covers deploying your Django (Channels/ASGI) application to production on a Linode server.

## Prerequisites
- [ ] GitHub Account
- [ ] Linode Account (Cloud Manager)
- [ ] SSH Client (Terminal/PowerShell)

---

## Part 1: Push to GitHub

1.  **Create a New Repository** on GitHub.
    *   Name it `umoja-hardware-system` (or similar).
    *   **Do not** initialize with README/gitignore (we already have them).

2.  **Push your code**:
    Run these commands in your local terminal (VS Code):
    ```powershell
    git remote add origin https://github.com/<YOUR_USERNAME>/<YOUR_REPO_NAME>.git
    git branch -M main
    git push -u origin main
    ```

---

## Part 2: Linode Server Setup

1.  **Create a Linode**:
    *   **Image**: Ubuntu 24.04 LTS
    *   **Region**: Choose one close to your users (e.g., Nairobi, London, Mumbai).
    *   **Plan**: Shared CPU -> Nanode 1GB (good for starting) or Linode 2GB.
    *   **Root Password**: Set a strong password.

2.  **SSH into the Server**:
    Open your terminal:
    ```bash
    ssh root@<YOUR_LINODE_IP_ADDRESS>
    ```

3.  **Update and Install Dependencies**:
    Run these commands on the server:
    ```bash
    sudo apt update && sudo apt upgrade -y
    sudo apt install python3-pip python3-venv python3-dev libpq-dev nginx git pkg-config libcairo2-dev libpango-1.0-0 libpangoft2-1.0-0 redis-server postgresql postgresql-contrib -y
    ```

4.  **Enable Redis and PostgreSQL**:
    ```bash
    sudo systemctl enable redis-server
    sudo systemctl start redis-server
    sudo systemctl enable postgresql
    sudo systemctl start postgresql
    ```

5.  **Setup PostgreSQL Database**:
    Run these commands to create the database and user:
    ```bash
    sudo -u postgres psql -c "CREATE DATABASE umoja_db;"
    sudo -u postgres psql -c "CREATE USER postgres WITH PASSWORD 'allahu(SW)1';"
    sudo -u postgres psql -c "ALTER ROLE postgres SET client_encoding TO 'utf8';"
    sudo -u postgres psql -c "ALTER ROLE postgres SET default_transaction_isolation TO 'read committed';"
    sudo -u postgres psql -c "ALTER ROLE postgres SET timezone TO 'UTC';"
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE umoja_db TO postgres;"
    ```

---

## Part 3: Deploying the Code

1.  **Clone Repository**:
    ```bash
    cd /var/www
    sudo git clone https://github.com/<YOUR_USERNAME>/<YOUR_REPO_NAME>.git app
    # Change ownership to your user so you don't need sudo for everything inside
    sudo chown -R $USER:$USER app
    cd app
    ```

2.  **Setup Virtual Environment**:
    ```bash
    cd /var/www/app  # Ensure you are in the application directory
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    pip install daphne  # Ensure daphne is installed for ASGI
    ```

3.  **Environment Variables**:
    Create `.env` file:
    ```bash
    nano .env
    ```
    Paste your production settings:
    ```
    DEBUG=False
    SECRET_KEY=97mo#$70gwckll(^rf%=o%ad4y*a(93en2ya5p%7(4-^6%el8j
    ALLOWED_HOSTS=umoja.ehub.co.tz,<YOUR_LINODE_IP>
    CSRF_TRUSTED_ORIGINS=https://umoja.ehub.co.tz

    # Database Settings
    DB_NAME=umoja_db
    DB_USER=postgres
    DB_PASSWORD=allahu(SW)1
    DB_HOST=localhost
    DB_PORT=5432
    
    # After setting up SSL (Part 6), set these to True:
    SECURE_SSL_REDIRECT=True
    SESSION_COOKIE_SECURE=True
    CSRF_COOKIE_SECURE=True
    ```
    (Replace `<YOUR_LINODE_IP>` with your actual server IP).
    (Press `Ctrl+X`, `Y`, `Enter` to save).

4.  **Database & Static Files**:
    ```bash
    mkdir -p media  # Create media folder if it doesn't exist
    python manage.py migrate
    python manage.py collectstatic --noinput
    ```

---

## Part 4: Running the App (Daphne + Systemd)

Since you are using Django Channels, you need an ASGI server (Daphne).

1.  **Create a Systemd Service**:
    ```bash
    sudo nano /etc/systemd/system/django_app.service
    ```
    Paste the following (replace `User=root` with `User=<YOUR_USERNAME>` if not using root):

    ```ini
    [Unit]
    Description=Django ASGI Application
    After=network.target

    [Service]
    User=root
    # CHANGE 'User=root' to your username (e.g., 'ibrahimu') if not running as root
    Group=www-data
    WorkingDirectory=/var/www/app
    Environment="PATH=/var/www/app/venv/bin"
    ExecStart=/var/www/app/venv/bin/daphne -b 127.0.0.1 -p 8000 sms_project.asgi:application

    [Install]
    WantedBy=multi-user.target
    ```

2.  **Start and Enable Service**:
    ```bash
    sudo systemctl start django_app
    sudo systemctl enable django_app
    ```

---

## Part 5: Nginx Configuration (Reverse Proxy)

1.  **Configure Nginx**:
    ```bash
    sudo nano /etc/nginx/sites-available/django_app
    ```
    Paste:
    ```nginx
    server {
        listen 80;
        server_name umoja.ehub.co.tz;

        location = /favicon.ico { access_log off; log_not_found off; }
        
        # Serve Static Files
        location /static/ {
            alias /var/www/app/staticfiles/;
        }

        # Serve Media Files (Uploads)
        location /media/ {
            alias /var/www/app/media/;
        }

        # Proxy to Daphne
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
    ```

2.  **Enable Configuration**:
    ```bash
    sudo ln -s /etc/nginx/sites-available/django_app /etc/nginx/sites-enabled/
    sudo rm /etc/nginx/sites-enabled/default
    sudo nginx -t  # Test config
    sudo systemctl restart nginx
    ```

---

## Part 6: SSL Configuration (HTTPS)

Secure your site with a free Let's Encrypt certificate.

1.  **Install Certbot**:
    ```bash
    sudo snap install core; sudo snap refresh core
    sudo snap install --classic certbot
    sudo ln -s /snap/bin/certbot /usr/bin/certbot
    ```

2.  **Obtain and Install Certificate**:
    ```bash
    sudo certbot --nginx -d umoja.ehub.co.tz
    ```
    *   Enter your email address when asked.
    *   Agree to the terms (Type `Y`).
    *   If asked to redirect HTTP traffic to HTTPS, choose **2** (Redirect) to ensure all traffic is secure.

### Deployment Automation

To simplify future updates, I've created a `deploy.sh` script.

1. **Make the script executable** (run once):
   ```bash
   chmod +x deploy.sh
   ```

2. **Run the update script** (whenever you push new code to GitHub):
   ```bash
   ./deploy.sh
   ```

The script will automatically:
- Pull latest changes from GitHub.
- Install any new dependencies.
- Run database migrations.
- Collect static files.
- Fix folder permissions.
- Restart Daphne and Nginx.

---

## Final Verification
Once your server is up and running, your app should be accessible at:
- **https://umoja.ehub.co.tz**
- Static files should load correctly.
- Admin dashboard should be accessible.

## Done!
Visit **https://umoja.ehub.co.tz**. Your app should be live and secure.
