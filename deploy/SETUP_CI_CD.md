# CI/CD Setup Instructions

This guide will help you set up automatic deployment from GitHub to your GCP VM.

## Prerequisites

1. GitHub repository: https://github.com/bhavesh-dripzy/interior.git
2. GCP VM access
3. GitHub Actions enabled (free for public repos)

## Step 1: Generate SSH Key for GitHub Actions

Run this on your local machine or VM:

```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/github_actions_key -N ""
```

This creates:
- `~/.ssh/github_actions_key` (private key - add to GitHub Secrets)
- `~/.ssh/github_actions_key.pub` (public key - add to VM)

## Step 2: Add SSH Public Key to VM

```bash
# Copy the public key
cat ~/.ssh/github_actions_key.pub

# Add it to the VM
gcloud compute ssh djangostack-interior-vm --zone=us-east1-c
# Then run:
echo "YOUR_PUBLIC_KEY_HERE" >> ~/.ssh/authorized_keys
```

Or use gcloud:
```bash
gcloud compute instances add-metadata djangostack-interior-vm \
  --zone=us-east1-c \
  --metadata-from-file ssh-keys=<(echo "github-actions:$(cat ~/.ssh/github_actions_key.pub)")
```

## Step 3: Create GCP Service Account (Optional - for gcloud CLI)

If you want to use gcloud in GitHub Actions:

1. Go to GCP Console → IAM & Admin → Service Accounts
2. Create a new service account
3. Grant "Compute Instance Admin" role
4. Create a JSON key
5. Add the JSON content to GitHub Secrets as `GCP_SA_KEY`

## Step 4: Add GitHub Secrets

Go to your GitHub repo: https://github.com/bhavesh-dripzy/interior/settings/secrets/actions

Add these secrets:

1. **VM_SSH_PRIVATE_KEY**: Content of `~/.ssh/github_actions_key` (private key)
2. **VM_USER**: Your VM username (usually your GCP username)
3. **GCP_SA_KEY**: (Optional) GCP Service Account JSON key

## Step 5: Push the Workflow

The workflow file is already created at `.github/workflows/deploy.yml`. 

Commit and push:

```bash
cd /home/nishkarsh/Desktop/designer
git add .github/workflows/deploy.yml
git commit -m "Add CI/CD deployment workflow"
git push origin main
```

## Step 6: Test the Deployment

1. Make a small change to your code
2. Commit and push to `main` branch
3. Go to GitHub → Actions tab
4. Watch the deployment run

## Manual Deployment Trigger

You can also trigger deployment manually:
1. Go to GitHub → Actions
2. Select "Deploy to Production VM"
3. Click "Run workflow"

## Troubleshooting

### SSH Connection Issues
- Verify the public key is in `~/.ssh/authorized_keys` on the VM
- Check VM firewall rules allow SSH (port 22)

### Permission Issues
- Ensure the VM user has sudo permissions
- Check file ownership: `sudo chown -R www-data:www-data /var/www/interior-app`

### Build Failures
- Check Node.js and Python versions match
- Verify all dependencies are in `package.json` and `requirements.txt`

## Alternative: Simpler SSH-only Setup

If you don't want to use GCP Service Account, you can modify the workflow to use only SSH:

```yaml
- name: Deploy
  uses: appleboy/ssh-action@master
  with:
    host: 34.73.5.92
    username: ${{ secrets.VM_USER }}
    key: ${{ secrets.VM_SSH_PRIVATE_KEY }}
    script: |
      cd /var/www/interior-app
      git pull origin main
      # ... rest of deployment commands
```

