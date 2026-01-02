#!/bin/bash
# Helper script to rebuild the dashboard container with cache busting
# This ensures templates and static files are always fresh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Rebuilding tc-dashboard with cache busting..."
cd "$PROJECT_ROOT"

# Use timestamp to bust cache
CACHE_BUST=$(date +%s)
export CACHE_BUST

echo "Cache bust value: $CACHE_BUST"
docker-compose build --build-arg CACHE_BUST="$CACHE_BUST" tc-dashboard
docker-compose up -d tc-dashboard

echo "Dashboard rebuilt and restarted!"

