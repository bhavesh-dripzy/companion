#!/bin/bash

# Deployment script for GCP VM
# Usage: ./deploy.sh [VM_IP or VM_NAME]

set -e  # Exit on error

VM_IP="34.73.5.92"
VM_ZONE="us-east1-c"
VM_NAME="djangostack-interior-vm"
PROJECT_DIR="/home/nishkarsh/Desktop/designer"
REMOTE_USER="$(whoami)"  # Adjust if different

echo "🚀 Starting deployment to GCP VM..."

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ gcloud CLI not found. Please install it first."
    exit 1
fi

# Build frontend
echo "📦 Building frontend..."
cd "$PROJECT_DIR/interior"
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install
fi
npm run build

# Create deployment package
echo "📦 Creating deployment package..."
cd "$PROJECT_DIR"
tar -czf interior-deploy.tar.gz \
    --exclude='node_modules' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    interior/

# Transfer to VM
echo "📤 Transferring files to VM..."
gcloud compute scp interior-deploy.tar.gz ${VM_NAME}:~/ --zone=${VM_ZONE}

# Create deployment script on remote
echo "📝 Creating remote deployment script..."
cat > remote_deploy.sh << 'REMOTE_SCRIPT'
#!/bin/bash
set -e

echo "🔧 Setting up on remote VM..."

# Extract files
cd ~
tar -xzf interior-deploy.tar.gz

# Move to web directory
sudo mkdir -p /var/www/interior-app
sudo cp -r interior/* /var/www/interior-app/
cd /var/www/interior-app/backend

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Activate and install dependencies
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

# Set permissions
sudo chown -R www-data:www-data /var/www/interior-app
sudo chmod -R 755 /var/www/interior-app

# Run migrations (if .env is configured)
if [ -f .env ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
fi

# Restart services
sudo systemctl restart gunicorn || echo "Gunicorn service not found - you may need to set it up first"
sudo systemctl restart nginx || echo "Nginx not found - you may need to install it first"

echo "✅ Deployment complete!"
REMOTE_SCRIPT

# Transfer and run remote script
gcloud compute scp remote_deploy.sh ${VM_NAME}:~/ --zone=${VM_ZONE}
gcloud compute ssh ${VM_NAME} --zone=${VM_ZONE} --command="chmod +x ~/remote_deploy.sh && ~/remote_deploy.sh"

# Cleanup
rm -f interior-deploy.tar.gz remote_deploy.sh

echo "✅ Deployment complete! Visit http://${VM_IP}/"

