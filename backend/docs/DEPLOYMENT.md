# Deployment Guide — Product Enrichment Engine

**Live URL:** https://enrichment.webfast.si
**Repository:** https://github.com/nejcpetan/shoppster-enrichment
**Docker image:** `ghcr.io/nejcpetan/enrichment-engine:latest`
**VPS:** enrichment.webfast.si (Hetzner or similar, Ubuntu 22.04)

---

## Architecture

Each customer runs as an isolated Docker Compose stack:

```
/opt/enrichment/
├── docker-compose.template.yml   ← base template for new customers
├── shoppster/
│   ├── docker-compose.yml
│   ├── .env                      ← secrets + config path
│   ├── config/
│   │   └── company.json          ← Shoppster's pipeline config
│   └── service-account.json      ← GCP service account (Vertex AI)
└── merkur/                       ← future customer
    └── ...
```

Port assignments:
- **Shoppster:** port 8000 → https://enrichment.webfast.si (via nginx reverse proxy)
- **Merkur (future):** port 8001

---

## Part 1: First-Time VPS Setup

Run these once when setting up a new server.

### 1.1 Install Docker

```bash
# Connect to VPS
ssh root@enrichment.webfast.si

# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Verify
docker --version
docker compose version
```

### 1.2 Create directory structure

```bash
mkdir -p /opt/enrichment
cd /opt/enrichment

# Clone the repo to get the template files
git clone https://github.com/nejcpetan/shoppster-enrichment /opt/enrichment-repo
```

### 1.3 Copy the docker-compose template

```bash
cp /opt/enrichment-repo/docker-compose.yml /opt/enrichment/docker-compose.template.yml
```

### 1.4 Install nginx (reverse proxy)

```bash
apt install -y nginx certbot python3-certbot-nginx

# Create nginx config for Shoppster
cat > /etc/nginx/sites-available/enrichment.webfast.si << 'EOF'
server {
    listen 80;
    server_name enrichment.webfast.si;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_cache_bypass $http_upgrade;

        # Required for SSE (Server-Sent Events)
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
EOF

ln -s /etc/nginx/sites-available/enrichment.webfast.si /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Get SSL certificate
certbot --nginx -d enrichment.webfast.si
```

---

## Part 2: Deploy Shoppster (First Customer)

### 2.1 Set up the instance directory

```bash
cd /opt/enrichment
mkdir -p shoppster/config

# Copy the config file
cp /opt/enrichment-repo/backend/config/shoppster.json shoppster/config/company.json

# Copy the docker-compose template
cp docker-compose.template.yml shoppster/docker-compose.yml

# Copy the .env template
cp /opt/enrichment-repo/backend/env.template shoppster/.env
```

### 2.2 Fill in the .env file

```bash
nano shoppster/.env
```

Set these values:

```env
COMPANY_CONFIG_PATH=config/company.json
DATABASE_URL=postgresql://enrichment:CHANGE_DB_PASS@db:5432/enrichment
DB_USER=enrichment
DB_PASSWORD=CHANGE_DB_PASS          # generate: openssl rand -hex 16
DB_NAME=enrichment

VERTEX_PROJECT_ID=your-gcp-project-id
VERTEX_LOCATION=europe-west1
GOOGLE_APPLICATION_CREDENTIALS=service-account.json

TAVILY_API_KEY=tvly-your-key-here
FIRECRAWL_API_KEY=fc-your-key-here   # only needed if search_provider = firecrawl

JWT_SECRET=CHANGE_THIS               # generate: openssl rand -hex 32

API_PORT=8000
```

### 2.3 Copy the GCP service account

```bash
# Upload from your local machine:
scp service-account.json root@enrichment.webfast.si:/opt/enrichment/shoppster/service-account.json

# Set permissions
chmod 600 /opt/enrichment/shoppster/service-account.json
```

### 2.4 Update docker-compose.yml DB password

The DB password must match between `.env` and `docker-compose.yml`. Edit `shoppster/docker-compose.yml` and replace `changeme` with the same password you set in `.env`:

```bash
nano shoppster/docker-compose.yml
# Find: POSTGRES_PASSWORD: ${DB_PASSWORD:-changeme}
# The ${DB_PASSWORD} variable is loaded from .env automatically — no change needed
# if you set DB_PASSWORD in .env correctly.
```

### 2.5 Start the stack

```bash
cd /opt/enrichment/shoppster
docker compose up -d

# Check both containers are running
docker compose ps

# Check API logs
docker compose logs -f api
```

Expected output: `INFO: Application startup complete.`

### 2.6 Create the admin user

```bash
docker compose exec api python scripts/create_admin.py admin@shoppster.com "YourPassword123" "Shoppster Admin"
```

### 2.7 Verify

```bash
# Test the API
curl https://enrichment.webfast.si/api/auth/login \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@shoppster.com", "password": "YourPassword123"}'

# Should return: {"access_token": "...", "token_type": "bearer", "user": {...}}
```

### 2.8 Migrate existing SQLite data (if applicable)

If there is an existing `products.db` with data to preserve:

```bash
# Upload the SQLite DB
scp products.db root@enrichment.webfast.si:/opt/enrichment/shoppster/products.db

# Copy it into the container
docker cp /opt/enrichment/shoppster/products.db shoppster-api-1:/app/products.db

# Run the migration
docker compose exec api python scripts/migrate_sqlite_to_postgres.py

# Remove the SQLite file from the container after migration
docker compose exec api rm /app/products.db
```

---

## Part 3: Adding a New Customer

This uses the `new-customer.sh` script to scaffold the instance directory.

### 3.1 Run the scaffold script

```bash
cd /opt/enrichment
bash /opt/enrichment-repo/scripts/new-customer.sh merkur 8001
```

This creates `/opt/enrichment/merkur/` with a generated JWT secret and DB password already filled in.

### 3.2 Add the customer config

```bash
cp /opt/enrichment-repo/backend/config/merkur.json /opt/enrichment/merkur/config/company.json
```

### 3.3 Fill in remaining .env values

```bash
nano /opt/enrichment/merkur/.env
# Fill in: VERTEX_PROJECT_ID, TAVILY_API_KEY, FIRECRAWL_API_KEY
```

### 3.4 Copy service account

```bash
cp /opt/enrichment/shoppster/service-account.json /opt/enrichment/merkur/service-account.json
```

### 3.5 Start and create admin

```bash
cd /opt/enrichment/merkur
docker compose up -d
docker compose exec api python scripts/create_admin.py admin@merkur.com "Password123" "Merkur Admin"
```

### 3.6 Add nginx vhost (optional — if they get their own subdomain)

```bash
# Or add a /merkur location to the existing nginx config for path-based routing
```

---

## Part 4: Updating All Instances

When you push to `master`, GitHub Actions automatically builds and pushes a new Docker image to `ghcr.io/nejcpetan/enrichment-engine:latest`.

To deploy the update to all running instances on the VPS:

```bash
bash /opt/enrichment-repo/scripts/update-all.sh
```

This pulls the latest image and does a zero-downtime rolling restart of each stack.

To update a single instance:

```bash
cd /opt/enrichment/shoppster
docker compose pull && docker compose up -d
```

---

## Part 5: Monitoring and Logs

### View live API logs

```bash
cd /opt/enrichment/shoppster
docker compose logs -f api
```

### View database logs

```bash
docker compose logs -f db
```

### Check container status

```bash
docker compose ps
```

### Check disk usage

```bash
docker system df
df -h /opt/enrichment
```

### Check API health

```bash
curl https://enrichment.webfast.si/api/dashboard/stats \
  -H "Authorization: Bearer <token>"
```

---

## Part 6: Backups

### Manual database backup

```bash
# Backup Shoppster's database to a local file
docker compose -f /opt/enrichment/shoppster/docker-compose.yml \
  exec db pg_dump -U enrichment enrichment > /opt/backups/shoppster_$(date +%Y%m%d_%H%M%S).sql
```

### Automated daily backups (cron)

```bash
mkdir -p /opt/backups

# Add to crontab
crontab -e

# Add this line (runs at 2am daily, keeps 30 days of backups):
0 2 * * * docker compose -f /opt/enrichment/shoppster/docker-compose.yml exec -T db pg_dump -U enrichment enrichment | gzip > /opt/backups/shoppster_$(date +\%Y\%m\%d).sql.gz && find /opt/backups -name "shoppster_*.sql.gz" -mtime +30 -delete
```

### Restore from backup

```bash
# Stop the API during restore
cd /opt/enrichment/shoppster
docker compose stop api

# Restore
gunzip -c /opt/backups/shoppster_20240115.sql.gz | \
  docker compose exec -T db psql -U enrichment enrichment

# Start API again
docker compose start api
```

---

## Part 7: Runtime Config Changes

Admins can change pipeline settings from the dashboard without redeploying.

### Via API

```bash
TOKEN="your-admin-jwt-token"

# View current config
curl https://enrichment.webfast.si/api/settings/ \
  -H "Authorization: Bearer $TOKEN"

# Change a setting (e.g. increase daily cost limit)
curl -X PUT https://enrichment.webfast.si/api/settings/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key": "cost.max_daily_cost_usd", "value": "100.0"}'

# Reset a setting to the company.json default
curl -X DELETE https://enrichment.webfast.si/api/settings/cost.max_daily_cost_usd \
  -H "Authorization: Bearer $TOKEN"
```

Settings that can be changed at runtime: any key in the config schema (see [API_REFERENCE.md](API_REFERENCE.md)).

---

## Troubleshooting

### Container won't start — "no such file: service-account.json"

```bash
ls -la /opt/enrichment/shoppster/service-account.json
# Must exist. If not: scp it from local machine.
```

### API returns 500 on startup — "Config not loaded"

```bash
# Check COMPANY_CONFIG_PATH in .env points to an existing file
docker compose exec api ls -la config/
# Should show company.json
```

### Database connection refused

```bash
# Check postgres is healthy
docker compose ps
# If db is not healthy, check its logs
docker compose logs db
```

### "Invalid email or password" on first login

```bash
# Re-run the create_admin script — it's idempotent for new users
docker compose exec api python scripts/create_admin.py admin@shoppster.com "NewPassword123"
```

### SSE events not streaming (nginx timeout)

The nginx config must have `proxy_buffering off` and `proxy_read_timeout 3600s` for SSE to work. Check the nginx config and reload:

```bash
nginx -t && systemctl reload nginx
```

### Out of disk space

```bash
# Clean up unused Docker images and volumes
docker system prune -f

# Check what's taking space
du -sh /opt/enrichment/*/
du -sh /opt/backups/
```
