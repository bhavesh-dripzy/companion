#!/bin/bash

# Deployment script for GCP VM
set -e

VM_IP="34.73.5.92"
VM_ZONE="us-east1-c"
VM_NAME="djangostack-interior-vm"
BACKEND_DIR="/home/nishkarsh/Desktop/designer/interior/backend"
REMOTE_USER="${USER}"

echo "🚀 Starting deployment to GCP VM at $VM_IP..."

# Generate SECRET_KEY
echo "🔑 Generating SECRET_KEY..."
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))" 2>/dev/null || openssl rand -hex 32)

# Create deployment package
echo "📦 Creating deployment package..."
cd /home/nishkarsh/Desktop/designer
tar -czf backend-deploy.tar.gz \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='db.sqlite3' \
    --exclude='media' \
    --exclude='staticfiles' \
    -C interior backend/

# Create .env file for production
echo "📝 Creating production .env file..."
cat > /tmp/prod.env << EOF
SECRET_KEY=$SECRET_KEY
DEBUG=False
ALLOWED_HOSTS=34.73.5.92,localhost,127.0.0.1

DB_ENGINE=mysql
DB_NAME=houzatt_db
DB_USER=root
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=3306
EOF

echo "📤 Transferring files to VM..."
# Try gcloud first, fallback to scp
if command -v gcloud &> /dev/null; then
    gcloud compute scp backend-deploy.tar.gz /tmp/prod.env ${VM_NAME}:~/ --zone=${VM_ZONE} 2>/dev/null || {
        echo "⚠️  gcloud transfer failed, trying direct SSH..."
        scp backend-deploy.tar.gz /tmp/prod.env ${REMOTE_USER}@${VM_IP}:~/
    }
else
    scp backend-deploy.tar.gz /tmp/prod.env ${REMOTE_USER}@${VM_IP}:~/
fi

echo "🔧 Creating remote setup script..."
cat > /tmp/remote_setup.sh << 'REMOTE_SCRIPT'
#!/bin/bash
set -e

echo "🔧 Setting up Django app on VM..."

# Extract backend
cd ~
tar -xzf backend-deploy.tar.gz
sudo mkdir -p /var/www/interior-app
sudo cp -r backend /var/www/interior-app/
cd /var/www/interior-app/backend

# Copy .env file
sudo cp ~/prod.env .env
sudo chmod 600 .env

# Create virtual environment
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Install dependencies
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
pip install gunicorn --quiet

# Install system packages if needed
if ! command -v nginx &> /dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y nginx python3-pip python3-venv -qq
fi

# Create database
sudo mysql -u root << 'MYSQL' || echo "Database may already exist"
CREATE DATABASE IF NOT EXISTS houzatt_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
MYSQL

# Set permissions
sudo chown -R www-data:www-data /var/www/interior-app
sudo chmod -R 755 /var/www/interior-app

# Run migrations
source venv/bin/activate
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Setup Gunicorn service
sudo tee /etc/systemd/system/gunicorn.service > /dev/null << 'GUNICORN'
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
GUNICORN

# Setup Nginx
sudo tee /etc/nginx/sites-available/interior-app > /dev/null << 'NGINX'
server {
    listen 80;
    server_name 34.73.5.92;

    client_max_body_size 100M;

    location /static/ {
        alias /var/www/interior-app/backend/staticfiles/;
    }

    location /media/ {
        alias /var/www/interior-app/backend/media/;
    }

    location /api/ {
        include proxy_params;
        proxy_pass http://unix:/var/www/interior-app/backend/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /admin/ {
        include proxy_params;
        proxy_pass http://unix:/var/www/interior-app/backend/gunicorn.sock;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/var/www/interior-app/backend/gunicorn.sock;
    }
}
NGINX

# Enable Nginx site
sudo ln -sf /etc/nginx/sites-available/interior-app /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t

# Start services
sudo systemctl daemon-reload
sudo systemctl enable gunicorn
sudo systemctl restart gunicorn
sudo systemctl restart nginx

echo "✅ Deployment complete!"
echo "🌐 Your app should be available at http://34.73.5.92/api/"
REMOTE_SCRIPT

# Transfer and execute setup script
echo "📤 Transferring setup script..."
if command -v gcloud &> /dev/null; then
    gcloud compute scp /tmp/remote_setup.sh ${VM_NAME}:~/ --zone=${VM_ZONE} 2>/dev/null || {
        scp /tmp/remote_setup.sh ${REMOTE_USER}@${VM_IP}:~/
    }
    gcloud compute ssh ${VM_NAME} --zone=${VM_ZONE} --command="chmod +x ~/remote_setup.sh && bash ~/remote_setup.sh" 2>/dev/null || {
        echo "⚠️  gcloud SSH failed, please run manually:"
        echo "ssh ${REMOTE_USER}@${VM_IP}"
        echo "Then run: bash ~/remote_setup.sh"
    }
else
    scp /tmp/remote_setup.sh ${REMOTE_USER}@${VM_IP}:~/
    echo "⚠️  Please SSH to the VM and run:"
    echo "ssh ${REMOTE_USER}@${VM_IP}"
    echo "bash ~/remote_setup.sh"
fi

# Cleanup
rm -f backend-deploy.tar.gz /tmp/prod.env /tmp/remote_setup.sh

echo "✅ Deployment files transferred!"
echo "🌐 Once setup completes, visit: http://34.73.5.92/api/"

