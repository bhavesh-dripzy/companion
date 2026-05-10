# Deployment Guide for GCP VM

This guide will help you deploy your Django app to the GCP VM at `http://34.73.5.92/`.

## Prerequisites

1. SSH access to your GCP VM instance
2. Your Django project files ready for deployment
3. Database credentials (MySQL is already configured on the VM)

## Step 1: Connect to Your GCP VM

From your local machine, connect to the VM using SSH:

```bash
# Replace with your actual VM instance name and zone
gcloud compute ssh djangostack-interior-vm --zone=us-east1-c

# Or if you have SSH keys set up:
ssh -i ~/.ssh/gcp_key user@34.73.5.92
```

## Step 2: Prepare the VM Environment

Once connected to the VM, run these commands:

```bash
# Update system packages
sudo apt-get update
sudo apt-get upgrade -y

# Install Python and pip if not already installed
sudo apt-get install -y python3 python3-pip python3-venv

# Install nginx (web server)
sudo apt-get install -y nginx

# Install gunicorn (WSGI server for Django)
pip3 install gunicorn
```

## Step 3: Transfer Your Project Files

From your **local machine**, transfer your project to the VM:

```bash
# Navigate to your project directory
cd /home/nishkarsh/Desktop/designer

# Create a compressed archive
tar -czf interior-app.tar.gz interior/

# Transfer to VM (replace with your actual SSH details)
scp interior-app.tar.gz user@34.73.5.92:/home/user/

# Or use gcloud command:
gcloud compute scp interior-app.tar.gz djangostack-interior-vm:/home/user/ --zone=us-east1-c
```

## Step 4: Set Up Project on VM

SSH back into the VM and extract the files:

```bash
# Extract the project
cd /home/user
tar -xzf interior-app.tar.gz

# Move to a better location (optional)
sudo mv interior /var/www/interior-app
cd /var/www/interior-app
```

## Step 5: Set Up Python Virtual Environment

```bash
cd /var/www/interior-app/backend

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install gunicorn
pip install gunicorn
```

## Step 6: Configure Environment Variables

```bash
# Create .env file
cd /var/www/interior-app/backend
nano .env
```

Add the following content (adjust values as needed):

```env
# Django Settings
SECRET_KEY=your-secret-key-here-generate-a-new-one
DEBUG=False
ALLOWED_HOSTS=34.73.5.92,localhost,127.0.0.1

# Database (MySQL is already configured on the VM)
DB_ENGINE=mysql
DB_NAME=houzatt_db
DB_USER=root
DB_PASSWORD=your-mysql-password
DB_HOST=localhost
DB_PORT=3306
```

**Important:** Generate a new SECRET_KEY:
```bash
python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

## Step 7: Set Up Database

```bash
# Connect to MySQL (as shown in GCP console)
sudo mysql -u root

# In MySQL prompt:
CREATE DATABASE IF NOT EXISTS houzatt_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
EXIT;

# Run migrations
cd /var/www/interior-app/backend
source venv/bin/activate
python manage.py migrate

# Create superuser (optional)
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic --noinput
```

## Step 8: Configure Gunicorn

Create a Gunicorn service file:

```bash
sudo nano /etc/systemd/system/gunicorn.service
```

Add this content:

```ini
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

[Install]
WantedBy=multi-user.target
```

Start and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl start gunicorn
sudo systemctl enable gunicorn
sudo systemctl status gunicorn
```

## Step 9: Configure Nginx

Create Nginx configuration:

```bash
sudo nano /etc/nginx/sites-available/interior-app
```

Add this content:

```nginx
server {
    listen 80;
    server_name 34.73.5.92;

    # Serve static files
    location /static/ {
        alias /var/www/interior-app/backend/staticfiles/;
    }

    # Serve media files
    location /media/ {
        alias /var/www/interior-app/backend/media/;
    }

    # Proxy to Gunicorn
    location / {
        include proxy_params;
        proxy_pass http://unix:/var/www/interior-app/backend/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/interior-app /etc/nginx/sites-enabled/
sudo nginx -t  # Test configuration
sudo systemctl restart nginx
```

## Step 10: Set Permissions

```bash
# Set proper ownership
sudo chown -R www-data:www-data /var/www/interior-app
sudo chmod -R 755 /var/www/interior-app
```

## Step 11: Build and Serve Frontend (Optional)

If you want to serve your React frontend:

```bash
# On your local machine, build the frontend
cd /home/nishkarsh/Desktop/designer/interior
npm install
npm run build

# Transfer the dist folder to VM
scp -r dist user@34.73.5.92:/var/www/interior-app/frontend/

# Update Nginx to serve frontend
```

Update Nginx config to serve frontend:

```nginx
server {
    listen 80;
    server_name 34.73.5.92;

    # Serve frontend
    location / {
        root /var/www/interior-app/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # API endpoints
    location /api/ {
        include proxy_params;
        proxy_pass http://unix:/var/www/interior-app/backend/gunicorn.sock;
    }

    # Static files
    location /static/ {
        alias /var/www/interior-app/backend/staticfiles/;
    }

    # Media files
    location /media/ {
        alias /var/www/interior-app/backend/media/;
    }
}
```

## Troubleshooting

### Check Gunicorn logs:
```bash
sudo journalctl -u gunicorn -f
```

### Check Nginx logs:
```bash
sudo tail -f /var/log/nginx/error.log
```

### Restart services:
```bash
sudo systemctl restart gunicorn
sudo systemctl restart nginx
```

### Test Django:
```bash
cd /var/www/interior-app/backend
source venv/bin/activate
python manage.py runserver 0.0.0.0:8000
```

## Quick Deployment Script

A deployment script is available at `deploy.sh` - see that file for automated deployment.

