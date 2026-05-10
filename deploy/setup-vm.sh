#!/bin/bash

# Setup script to run on GCP VM
# This script sets up Gunicorn and Nginx for the Django app

set -e

echo "🔧 Setting up Django app on GCP VM..."

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then 
    echo "Please run with sudo: sudo bash setup-vm.sh"
    exit 1
fi

APP_DIR="/var/www/interior-app"
BACKEND_DIR="$APP_DIR/backend"

# Check if app directory exists
if [ ! -d "$BACKEND_DIR" ]; then
    echo "❌ Error: $BACKEND_DIR not found!"
    echo "Please extract your project files to $APP_DIR first."
    exit 1
fi

cd "$BACKEND_DIR"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install dependencies
echo "📦 Installing Python dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

# Install system dependencies
echo "📦 Installing system packages..."
apt-get update
apt-get install -y nginx python3-pip python3-venv

# Set up Gunicorn service
echo "🔧 Setting up Gunicorn service..."
cat > /etc/systemd/system/gunicorn.service << 'GUNICORN_SERVICE'
[Unit]
Description=gunicorn daemon for interior app
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/interior-app/backend
ExecStart=/var/www/interior-app/backend/venv/bin/gunicorn \
          --access-logfile - \
          --workers 3 \
          --bind unix:/var/www/interior-app/backend/gunicorn.sock \
          config.wsgi:application

Restart=always

[Install]
WantedBy=multi-user.target
GUNICORN_SERVICE

# Set up Nginx configuration
echo "🔧 Setting up Nginx configuration..."
cat > /etc/nginx/sites-available/interior-app << 'NGINX_CONFIG'
server {
    listen 80;
    server_name 34.73.5.92;

    client_max_body_size 100M;

    # Serve static files
    location /static/ {
        alias /var/www/interior-app/backend/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Serve media files
    location /media/ {
        alias /var/www/interior-app/backend/media/;
        expires 7d;
        add_header Cache-Control "public";
    }

    # Backend API
    location /api/ {
        include proxy_params;
        proxy_pass http://unix:/var/www/interior-app/backend/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Django admin
    location /admin/ {
        include proxy_params;
        proxy_pass http://unix:/var/www/interior-app/backend/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Serve frontend or proxy to backend
    location / {
        root /var/www/interior-app/frontend/dist;
        try_files $uri $uri/ @backend;
    }

    location @backend {
        include proxy_params;
        proxy_pass http://unix:/var/www/interior-app/backend/gunicorn.sock;
    }
}
NGINX_CONFIG

# Enable Nginx site
ln -sf /etc/nginx/sites-available/interior-app /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
nginx -t

# Set permissions
echo "🔧 Setting permissions..."
chown -R www-data:www-data "$APP_DIR"
chmod -R 755 "$APP_DIR"

# Create .env file if it doesn't exist
if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo "📝 Creating .env file template..."
    cat > "$BACKEND_DIR/.env" << 'ENV_TEMPLATE'
# Django Settings
SECRET_KEY=CHANGE-THIS-GENERATE-A-NEW-SECRET-KEY
DEBUG=False
ALLOWED_HOSTS=34.73.5.92,localhost,127.0.0.1

# Database Configuration
DB_ENGINE=mysql
DB_NAME=houzatt_db
DB_USER=root
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=3306
ENV_TEMPLATE
    echo "⚠️  Please edit $BACKEND_DIR/.env and set your SECRET_KEY and database credentials!"
fi

# Create database if it doesn't exist
echo "🔧 Setting up database..."
mysql -u root << 'MYSQL_SCRIPT' || echo "⚠️  Could not create database. You may need to run this manually."
CREATE DATABASE IF NOT EXISTS houzatt_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
MYSQL_SCRIPT

# Run migrations if .env is configured
if [ -f "$BACKEND_DIR/.env" ] && grep -q "SECRET_KEY=CHANGE-THIS" "$BACKEND_DIR/.env"; then
    echo "⚠️  Skipping migrations - please configure .env file first"
else
    echo "🔄 Running database migrations..."
    cd "$BACKEND_DIR"
    source venv/bin/activate
    python manage.py migrate --noinput || echo "⚠️  Migrations failed. Check your database configuration."
    python manage.py collectstatic --noinput || echo "⚠️  Static files collection failed."
fi

# Enable and start services
echo "🚀 Starting services..."
systemctl daemon-reload
systemctl enable gunicorn
systemctl start gunicorn
systemctl restart nginx

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit $BACKEND_DIR/.env and configure your settings"
echo "2. Run: cd $BACKEND_DIR && source venv/bin/activate && python manage.py migrate"
echo "3. Run: python manage.py collectstatic --noinput"
echo "4. Restart services: sudo systemctl restart gunicorn nginx"
echo ""
echo "Check status:"
echo "  sudo systemctl status gunicorn"
echo "  sudo systemctl status nginx"
echo ""
echo "View logs:"
echo "  sudo journalctl -u gunicorn -f"
echo "  sudo tail -f /var/log/nginx/error.log"

