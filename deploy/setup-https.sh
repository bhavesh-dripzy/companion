#!/bin/bash
# Script to set up HTTPS with Let's Encrypt on the VM
# Usage: Run this script on the VM (via SSH)

set -e

DOMAIN="${1:-}"
EMAIL="${2:-your-email@example.com}"

if [ -z "$DOMAIN" ]; then
    echo "❌ Error: Domain name is required for HTTPS"
    echo ""
    echo "Let's Encrypt (free SSL certificates) requires a domain name, not just an IP address."
    echo ""
    echo "To set up HTTPS, you need to:"
    echo "1. Purchase a domain name (e.g., from Namecheap, Google Domains, etc.)"
    echo "2. Point the domain's A record to your VM's IP: 34.73.5.92"
    echo "3. Wait for DNS propagation (usually 5-30 minutes)"
    echo "4. Run this script with your domain name"
    echo ""
    echo "Usage: bash setup-https.sh yourdomain.com your-email@example.com"
    echo ""
    echo "Alternative: If you just want to test HTTPS locally, you can use a self-signed certificate,"
    echo "but browsers will show a security warning."
    exit 1
fi

echo "🔒 Setting up HTTPS for domain: $DOMAIN"
echo "📧 Email for Let's Encrypt notifications: $EMAIL"
echo ""

# Update system
echo "📦 Updating system packages..."
sudo apt-get update -qq

# Install Certbot
echo "📦 Installing Certbot..."
sudo apt-get install -y certbot python3-certbot-nginx

# Check if domain resolves to this server
echo "🔍 Verifying domain DNS..."
DOMAIN_IP=$(dig +short $DOMAIN | tail -n1)
SERVER_IP=$(curl -s ifconfig.me || curl -s icanhazip.com)

if [ "$DOMAIN_IP" != "$SERVER_IP" ] && [ "$DOMAIN_IP" != "34.73.5.92" ]; then
    echo "⚠️  Warning: Domain $DOMAIN resolves to $DOMAIN_IP, but server IP is $SERVER_IP"
    echo "   Make sure your domain's A record points to 34.73.5.92"
    echo "   Continuing anyway..."
fi

# Backup current Nginx config
echo "💾 Backing up current Nginx configuration..."
sudo cp /etc/nginx/sites-available/interior-app /etc/nginx/sites-available/interior-app.backup.$(date +%Y%m%d_%H%M%S)

# Update Nginx config to use domain name
echo "📝 Updating Nginx configuration..."
sudo sed -i "s/server_name 34.73.5.92;/server_name $DOMAIN;/g" /etc/nginx/sites-available/interior-app

# Test Nginx configuration
echo "🧪 Testing Nginx configuration..."
sudo nginx -t

# Reload Nginx
echo "🔄 Reloading Nginx..."
sudo systemctl reload nginx

# Obtain SSL certificate
echo "🔐 Obtaining SSL certificate from Let's Encrypt..."
echo "   This will automatically configure Nginx for HTTPS"
sudo certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email $EMAIL --redirect

# Verify certificate
echo "✅ Verifying SSL certificate..."
sudo certbot certificates

# Set up automatic renewal
echo "🔄 Setting up automatic certificate renewal..."
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer

echo ""
echo "✅ HTTPS setup complete!"
echo ""
echo "🌐 Your site should now be available at:"
echo "   https://$DOMAIN"
echo ""
echo "📋 Certificate renewal is automatic. Certificates expire every 90 days"
echo "   and are automatically renewed 30 days before expiration."
echo ""
echo "🔍 To check certificate status: sudo certbot certificates"
echo "🔄 To manually renew: sudo certbot renew"

