#!/bin/bash
set -e

# Print environment info
echo "Starting Torn City API to BigQuery Pipeline"
echo "Timezone: ${TZ:-America/Chicago}"
echo "Python: $(python --version)"

# Run pipeline immediately on startup for faster troubleshooting
echo "Running initial pipeline execution..."
cd /app
python -m src.main >> /app/logs/cron.log 2>&1
echo "Initial pipeline execution completed. Check /app/logs/cron.log for details."

# Start cron in foreground for scheduled runs
echo "Starting cron daemon for scheduled runs (every 15 minutes)..."
cron -f

