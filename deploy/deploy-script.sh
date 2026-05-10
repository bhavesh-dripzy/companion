#!/bin/bash
# Deployment script to run on VM after files are transferred

set -e

echo "🚀 Starting deployment on VM..."

# Deploy Backend
if [ -f ~/backend-deploy.tar.gz ]; then
    echo "📦 Deploying backend..."
    cd ~
    tar -xzf backend-deploy.tar.gz
    sudo cp -r * /var/www/interior-app/backend/
    cd /var/www/interior-app/backend
    source venv/bin/activate
    pip install -r requirements.txt --quiet
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    sudo chown -R www-data:www-data /var/www/interior-app/backend
    sudo systemctl restart gunicorn
    echo "✅ Backend deployed"
fi

# Deploy Frontend
if [ -f ~/frontend-dist.tar.gz ]; then
    echo "📦 Deploying frontend..."
    cd ~
    tar -xzf frontend-dist.tar.gz
    sudo rm -rf /var/www/interior-app/frontend/*
    sudo cp -r * /var/www/interior-app/frontend/
    sudo chown -R www-data:www-data /var/www/interior-app/frontend
    sudo systemctl restart nginx
    echo "✅ Frontend deployed"
fi

echo "✅ Deployment complete!"

