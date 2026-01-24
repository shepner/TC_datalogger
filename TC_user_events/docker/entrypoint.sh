#!/bin/bash
set -e

# Print environment info
echo "Starting Torn City API to BigQuery Pipeline"
echo "Timezone: ${TZ:-America/Chicago}"
echo "Python: $(python --version)"

# Run pipeline immediately on startup for faster troubleshooting
echo "Running initial pipeline execution..."
cd /app
set +e
python -m src.main >> /app/logs/cron.log 2>&1
INITIAL_RC=$?
set -e
if [ $INITIAL_RC -ne 0 ]; then
  echo "Initial pipeline execution failed (exit code: $INITIAL_RC). Check /app/logs/cron.log for details."
else
  echo "Initial pipeline execution completed. Check /app/logs/cron.log for details."
fi

# Start scheduler for continuous runs (uses --schedule flag)
echo "Starting scheduler for continuous runs..."
python -m src.main --schedule >> /app/logs/cron.log 2>&1

