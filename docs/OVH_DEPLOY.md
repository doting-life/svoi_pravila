# OVH Production Deployment
## 1) Fresh Ubuntu 24.04 update
sudo apt update && sudo apt -y upgrade
sudo apt -y install ca-certificates curl gnupg git
## 2) Docker Engine + Docker Compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
## 3) Clone repository
git clone https://github.com/doting-life/svoi-pravila.git
cd svoi-pravila
## 4) Checkout production branch/tag
git fetch --all --tags
git checkout telegram-consent
## 5) Create server-side .env
cp .env.example .env
nano .env
Set SITE_DOMAIN, POSTGRES_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_WEBHOOK_URL.
## 6) DNS A record
Point SITE_DOMAIN A record to VPS IPv4.
## 7) Optional UFW
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
sudo ufw status
## 8) Validate production Compose
docker compose -f docker-compose.yml -f docker-compose.prod.yml config
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --profiles
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --services
## 9) Start postgres and redis first
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres redis
## 10) Run db-init explicitly once
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm db-init
## 11) Start app and caddy
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d app caddy
## 12) Verify health
curl -fsS https://your-domain/health
## 13) Telegram webhook setup
python scripts/set_telegram_webhook.py
Verify webhook is https://your-domain/v1/telegram/webhook
## 14) Verify Mini App
Open https://your-domain/miniapp/
## 15) View logs
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f app caddy postgres redis
## 16) Safe update/rebuild/restart
git fetch --all --tags
git checkout target-branch-or-tag
git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.prod.yml build app
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d app caddy
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
## 17) PostgreSQL backup
One-time directory preparation for non-root backup runs:
sudo mkdir -p /var/backups/svoi-pravila
sudo chown -R $USER:$USER /var/backups/svoi-pravila
sudo chmod 700 /var/backups/svoi-pravila
Run backup: ./scripts/backup_postgres.sh
## 18) PostgreSQL restore
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop app caddy
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres pg_restore -U svoi_pravila -d svoi_pravila --clean --if-exists --no-owner --no-privileges < /var/backups/svoi-pravila/dump-file.dump
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d app caddy
## 19) Basic rollback
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=200 app caddy
git checkout previous-known-good-branch-or-tag
docker compose -f docker-compose.yml -f docker-compose.prod.yml build app
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d app caddy
## Production behavior notes
Normal production startup does NOT run db-init.
In production, postgres/redis/app are internal only; only caddy exposes 80/443.
