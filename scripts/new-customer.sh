#!/bin/bash
# Sets up a new customer instance directory on the VPS.
# Usage: ./scripts/new-customer.sh <customer_slug> <port>
# Example: ./scripts/new-customer.sh merkur 8001

set -e

CUSTOMER=$1
PORT=$2
BASE_DIR="/opt/enrichment"

if [ -z "$CUSTOMER" ] || [ -z "$PORT" ]; then
    echo "Usage: ./new-customer.sh <customer_slug> <port>"
    echo "Example: ./new-customer.sh merkur 8001"
    exit 1
fi

CUSTOMER_DIR="$BASE_DIR/$CUSTOMER"

if [ -d "$CUSTOMER_DIR" ]; then
    echo "Error: $CUSTOMER_DIR already exists"
    exit 1
fi

echo "Creating customer instance: $CUSTOMER (port $PORT)"

# Create directory structure
mkdir -p "$CUSTOMER_DIR/config"

# Copy docker-compose template
cp "$BASE_DIR/docker-compose.template.yml" "$CUSTOMER_DIR/docker-compose.yml"

# Create .env from template
cp "$BASE_DIR/backend/env.template" "$CUSTOMER_DIR/.env"

# Set the port
sed -i "s/API_PORT=8000/API_PORT=$PORT/" "$CUSTOMER_DIR/.env"

# Generate a unique JWT secret
JWT_SECRET=$(openssl rand -hex 32)
sed -i "s/JWT_SECRET=CHANGE-ME-generate-with-openssl-rand-hex-32/JWT_SECRET=$JWT_SECRET/" "$CUSTOMER_DIR/.env"

# Generate a unique DB password and update both docker-compose.yml and .env
DB_PASS=$(openssl rand -hex 16)
sed -i "s/DB_PASSWORD:-changeme/DB_PASSWORD:-$DB_PASS/g" "$CUSTOMER_DIR/docker-compose.yml"
sed -i "s/DB_PASSWORD=changeme/DB_PASSWORD=$DB_PASS/" "$CUSTOMER_DIR/.env"
sed -i "s/enrichment:changeme@db/enrichment:$DB_PASS@db/" "$CUSTOMER_DIR/.env"

echo ""
echo "Customer instance scaffolded at: $CUSTOMER_DIR"
echo ""
echo "Next steps:"
echo "  1. cp backend/config/$CUSTOMER.json $CUSTOMER_DIR/config/company.json"
echo "  2. cp service-account.json $CUSTOMER_DIR/"
echo "  3. nano $CUSTOMER_DIR/.env   # fill in VERTEX_PROJECT_ID, TAVILY_API_KEY, etc."
echo "  4. cd $CUSTOMER_DIR && docker compose up -d"
echo "  5. docker compose exec api python scripts/create_admin.py admin@$CUSTOMER.com <password> 'Admin Name'"
echo ""
echo "API will be available at http://localhost:$PORT"
