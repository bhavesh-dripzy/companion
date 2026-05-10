# Quick Start: Deploy to GCP VM

This is a simplified guide to get your Django app running on `http://34.73.5.92/` quickly.

## Option 1: Automated Deployment (Recommended)

1. **Make sure you have gcloud CLI installed and configured:**
   ```bash
   gcloud auth login
   gcloud config set project interior-482510
   ```

2. **Run the deployment script:**
   ```bash
   cd /home/nishkarsh/Desktop/designer
   ./deploy.sh
   ```

3. **SSH into the VM and complete setup:**
   ```bash
   gcloud compute ssh djangostack-interior-vm --zone=us-east1-c
   ```

4. **On the VM, configure environment:**
   ```bash
   cd /var/www/interior-app/backend
   nano .env
   ```
   
   Add this content:
   ```env
   SECRET_KEY=<generate-new-secret-key>
   DEBUG=False
   ALLOWED_HOSTS=34.73.5.92,localhost,127.0.0.1
   DB_ENGINE=mysql
   DB_NAME=houzatt_db
   DB_USER=root
   DB_PASSWORD=
   DB_HOST=localhost
   DB_PORT=3306
   ```

5. **Set up database and services:**
   ```bash
   source venv/bin/activate
   
   # Create database
   sudo mysql -u root
   # In MySQL: CREATE DATABASE houzatt_db;
   # Exit MySQL: EXIT;
   
   # Run migrations
   python manage.py migrate
   python manage.py collectstatic --noinput
   
   # Restart services
   sudo systemctl restart gunicorn
   sudo systemctl restart nginx
   ```

## Option 2: Manual Step-by-Step

Follow the detailed guide in `DEPLOYMENT.md`.

## Important Notes

1. **Generate SECRET_KEY:**
   ```bash
   python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
   ```

2. **MySQL Password:**
   - The GCP Django Stack uses socket authentication by default
   - Connect with: `sudo mysql -u root`
   - If you set a password, update it in `.env`

3. **Frontend API URL:**
   - Create `.env` file in `interior/` directory:
     ```env
     VITE_API_BASE_URL=http://34.73.5.92/api
     ```
   - Rebuild frontend: `npm run build`

4. **Check if services are running:**
   ```bash
   sudo systemctl status gunicorn
   sudo systemctl status nginx
   ```

5. **View logs if something goes wrong:**
   ```bash
   sudo journalctl -u gunicorn -f
   sudo tail -f /var/log/nginx/error.log
   ```

## Testing Your Deployment

1. **Test Django API:**
   ```bash
   curl http://34.73.5.92/api/
   ```

2. **Test in browser:**
   - Visit: `http://34.73.5.92/`
   - API endpoint: `http://34.73.5.92/api/`

## Common Issues

### 502 Bad Gateway
- Check if Gunicorn is running: `sudo systemctl status gunicorn`
- Check Gunicorn socket permissions
- Restart: `sudo systemctl restart gunicorn`

### Static files not loading
- Run: `python manage.py collectstatic --noinput`
- Check Nginx static file configuration
- Check file permissions: `sudo chown -R www-data:www-data /var/www/interior-app`

### Database connection error
- Verify MySQL is running: `sudo systemctl status mysql`
- Check database credentials in `.env`
- Test connection: `sudo mysql -u root`

