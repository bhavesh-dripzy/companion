#!/bin/bash
# Run on the GCP VM with sudo after code is at /var/www/companion-app.
# Example:
#   sudo bash companion-setup-vm.sh

set -euo pipefail

EXTERNAL_IP="${EXTERNAL_IP:-34.11.93.251}"
APP_DIR="/var/www/companion-app"

if [ "$EUID" -ne 0 ]; then
  echo "Run with: sudo bash companion-setup-vm.sh"
  exit 1
fi

if [ ! -f "$APP_DIR/manage.py" ]; then
  echo "Error: $APP_DIR/manage.py not found. Extract the project to $APP_DIR first."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  nginx \
  mariadb-server \
  python3 \
  python3-pip \
  python3-venv \
  build-essential \
  pkg-config \
  default-libmysqlclient-dev \
  curl \
  ca-certificates \
  git

systemctl enable --now mariadb || systemctl enable --now mysql || true

# Node.js 20.x (for mcp-remote / Zomato + Swiggy MCP bridge)
if ! command -v node >/dev/null 2>&1 || [ "$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

cd "$APP_DIR"

if [ ! -d "venv" ]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

# MCP stdio bridge (uses node_modules/mcp-remote for Zomato + Swiggy Food)
if [ -f package.json ]; then
  npm install --omit=dev
fi

if [ ! -f "$APP_DIR/.env" ]; then
  if [ -f "$APP_DIR/.env.example" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  fi
  SECRET_KEY="$(python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())" 2>/dev/null || openssl rand -hex 32)"
  {
    echo ""
    echo "SECRET_KEY=$SECRET_KEY"
    echo "DEBUG=False"
    echo "ALLOWED_HOSTS=${EXTERNAL_IP},localhost,127.0.0.1"
  } >> "$APP_DIR/.env"
  echo "Created $APP_DIR/.env from template. Edit DB_* and GEMINI_API_KEY / ZOMATO_MCP_URL as needed."
fi

# MySQL: create DB + app user from .env (matches .env.example defaults)
DB_NAME="$(grep -E '^(export )?DB_NAME=' "$APP_DIR/.env" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '\r\"' || true)"
DB_USER="$(grep -E '^(export )?DB_USER=' "$APP_DIR/.env" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '\r\"' || true)"
DB_PASSWORD="$(grep -E '^(export )?DB_PASSWORD=' "$APP_DIR/.env" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '\r\"' || true)"
DB_NAME="${DB_NAME:-companion}"
DB_USER="${DB_USER:-companion_user}"
DB_PASSWORD="${DB_PASSWORD:-companion_pass}"

if command -v mysql >/dev/null 2>&1; then
  mysql -u root <<MYSQL_BOOTSTRAP || echo "Warning: MySQL bootstrap failed — fix DB manually."
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
MYSQL_BOOTSTRAP
else
  echo "Warning: mysql client not found."
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Gunicorn on loopback TCP (see nginx upstream). Avoids flaky nginx⇄unix CGI-style proxying.
GUNICORN_PORT="${GUNICORN_PORT:-8001}"

cat >/etc/systemd/system/gunicorn-companion.service <<EOF
[Unit]
Description=gunicorn daemon for companion Django app
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/gunicorn \\
          --access-logfile - \\
          --workers 3 \\
          --timeout 120 \\
          --bind 127.0.0.1:${GUNICORN_PORT} \\
          config.wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/nginx/sites-available/companion-app <<EOF
upstream companion_gunicorn {
    server 127.0.0.1:${GUNICORN_PORT} fail_timeout=0;
}

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${EXTERNAL_IP} localhost 127.0.0.1 _;

    client_max_body_size 20M;

    location /static/ {
        alias ${APP_DIR}/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location /api/ {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_pass http://companion_gunicorn;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    location /admin/ {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_pass http://companion_gunicorn;
    }

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_pass http://companion_gunicorn;
    }
}
EOF

ln -sf /etc/nginx/sites-available/companion-app /etc/nginx/sites-enabled/companion-app
rm -f /etc/nginx/sites-enabled/default

chown -R www-data:www-data "$APP_DIR"
chmod -R 755 "$APP_DIR"

# MCP (mcp-remote) OAuth token directory — Zomato + Swiggy share this path; copy ~/.mcp-auth here or run scripts/*_mcp_prod_oauth.sh as www-data.
mkdir -p "$APP_DIR/.mcp-auth"
chown www-data:www-data "$APP_DIR/.mcp-auth"
chmod 700 "$APP_DIR/.mcp-auth"

nginx -t
systemctl daemon-reload
systemctl enable gunicorn-companion
systemctl restart gunicorn-companion
systemctl restart nginx

echo ""
echo "Done. Test: curl http://${EXTERNAL_IP}/api/"
echo "Logs: journalctl -u gunicorn-companion -f"
echo ""
echo "MCP OAuth (optional): tokens live in ${APP_DIR}/.mcp-auth (readable by www-data)."
echo "  Zomato:  sudo -u www-data -H bash ${APP_DIR}/scripts/zomato_mcp_prod_oauth.sh"
echo "  Swiggy:  sudo -u www-data -H bash ${APP_DIR}/scripts/swiggy_food_mcp_prod_oauth.sh"
