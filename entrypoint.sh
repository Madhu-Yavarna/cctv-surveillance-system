#!/bin/bash
set -e

# Target data directory configured for Railway persistent volume
DATA_DIR="${CCTV_DATA:-/app/data}"
mkdir -p "$DATA_DIR/storage/uploads" "$DATA_DIR/storage/evidence"

# If database doesn't exist on volume yet (first run on new volume), populate with initial genuine DB
if [ ! -f "$DATA_DIR/cctv.db" ] && [ -f "/app/seed_data/cctv.db" ]; then
    echo "[INIT] Volume is empty. Seeding initial genuine surveillance database..."
    cp "/app/seed_data/cctv.db" "$DATA_DIR/cctv.db"
    if [ -d "/app/seed_data/storage" ]; then
        cp -r /app/seed_data/storage/* "$DATA_DIR/storage/" 2>/dev/null || true
    fi
    echo "[INIT] Initial genuine records and evidence successfully seeded into persistent volume."
fi

# Ensure secret key exists
if [ -z "$CCTV_SECRET" ] && [ ! -f "$DATA_DIR/secret.key" ]; then
    python -c "import secrets; print(secrets.token_hex(32))" > "$DATA_DIR/secret.key"
fi

echo "[STARTUP] Starting Context-Aware CCTV Surveillance Server on port ${PORT:-8000}..."
exec python run.py
