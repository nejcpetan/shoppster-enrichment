#!/bin/bash
# Updates all customer instances to the latest Docker image.
# Run from VPS as root or docker-capable user:
#   ./scripts/update-all.sh
#
# Assumes all customer directories live under /opt/enrichment/
# and each has a docker-compose.yml.

set -e

BASE_DIR="/opt/enrichment"
IMAGE="ghcr.io/YOUR_GITHUB_USER/enrichment-engine:latest"

echo "Pulling latest image: $IMAGE"
docker pull "$IMAGE"

echo ""
echo "Updating customer instances..."

UPDATED=0
FAILED=0

for CUSTOMER_DIR in "$BASE_DIR"/*/; do
    if [ -f "$CUSTOMER_DIR/docker-compose.yml" ]; then
        CUSTOMER=$(basename "$CUSTOMER_DIR")
        echo ""
        echo "── Updating: $CUSTOMER ──"
        cd "$CUSTOMER_DIR"
        if docker compose up -d --pull always; then
            echo "  OK: $CUSTOMER updated"
            UPDATED=$((UPDATED + 1))
        else
            echo "  FAILED: $CUSTOMER — check logs with: docker compose -f $CUSTOMER_DIR/docker-compose.yml logs api"
            FAILED=$((FAILED + 1))
        fi
    fi
done

echo ""
echo "Done. Updated: $UPDATED, Failed: $FAILED"

if [ $FAILED -gt 0 ]; then
    echo "WARNING: $FAILED instance(s) failed to update. Check logs above."
    exit 1
fi
