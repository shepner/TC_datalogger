# Torn City Data Logger - Health Dashboard

A web-based health monitoring dashboard for all Torn City data logger microservices. The dashboard provides real-time visibility into container status, last successful runs, recent errors, and log statistics.

## Overview

The health dashboard is a Flask-based web application that monitors the health of all four data logger microservices:

- **TC Faction Crimes** (`tc-faction-crimes-pipeline`)
- **TC Faction Members** (`tc-faction-members-pipeline`)
- **TC Items** (`tc-items-pipeline`)
- **TC User Events** (`tc-user-events-pipeline`)

## Features

- **Container Status**: Real-time Docker container status (running/stopped)
- **Last Successful Run**: Timestamp of the last successful pipeline execution
- **Recent Errors**: Last 10 error entries from each service's logs
- **Log Statistics**: Basic log file statistics (line count, last modified)
- **Health Status**: Overall health indicator (healthy/degraded/unhealthy)
- **Auto-refresh**: Automatically refreshes every 5 minutes
- **Responsive Design**: Works on desktop and mobile devices

## Quick Start

### Running with Docker Compose

The dashboard is included in the root `docker-compose.yml` and starts automatically:

```bash
cd TC_datalogger
docker-compose up -d
```

The dashboard will be available at: **http://localhost:8080**

### Running Standalone

1. **Build the Docker image**:
   ```bash
   cd TC_dashboard
   docker build -t tc-dashboard .
   ```

2. **Run the container**:
   ```bash
   docker run -d \
     --name tc-dashboard \
     -p 8080:8080 \
     -v $(pwd)/../TC_faction_crimes/logs:/app/logs/faction_crimes:ro \
     -v $(pwd)/../TC_faction_members/logs:/app/logs/faction_members:ro \
     -v $(pwd)/../TC_items/logs:/app/logs/items:ro \
     -v $(pwd)/../TC_user_events/logs:/app/logs/user_events:ro \
     -v $(pwd)/..:/app/data:ro \
     -v /var/run/docker.sock:/var/run/docker.sock:ro \
     tc-dashboard
   ```

3. **Access the dashboard**: http://localhost:8080

### Running Locally (for development)

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Set environment variables**:
   ```bash
   export DASHBOARD_PORT=8080
   export DASHBOARD_BASE_PATH=/path/to/TC_datalogger
   ```

3. **Run the application**:
   ```bash
   python -m src.app
   ```

4. **Access the dashboard**: http://localhost:8080

## API Endpoints

### `GET /api/health`

Get health status for all services.

**Response**:
```json
{
  "services": {
    "tc-faction-crimes": {
      "service_key": "tc-faction-crimes",
      "service_name": "TC Faction Crimes",
      "container_status": {
        "running": true,
        "status": "running",
        "started_at": "2025-12-28T18:00:00Z"
      },
      "last_successful_run": "2025-12-28T18:30:00",
      "recent_errors": [],
      "log_stats": {
        "exists": true,
        "last_modified": "2025-12-28T18:30:00",
        "line_count": 1234
      },
      "health_status": "healthy"
    },
    ...
  },
  "timestamp": "2025-12-28T18:35:00",
  "docker_available": true
}
```

### `GET /api/health/<service_key>`

Get health status for a specific service.

**Parameters**:
- `service_key`: One of `tc-faction-crimes`, `tc-faction-members`, `tc-items`, `tc-user-events`

**Response**: Same format as individual service in `/api/health`

## Health Status Logic

The dashboard determines health status based on:

- **Healthy**: Container is running, last run was within 30 minutes, no recent errors
- **Degraded**: Container is running but last run was >30 minutes ago, or recent errors in last 15 minutes
- **Unhealthy**: Container is not running

## Architecture

### Components

- **`src/app.py`**: Flask application with API routes
- **`src/health_checker.py`**: Orchestrates health checks for all services
- **`src/docker_client.py`**: Docker SDK wrapper for container status
- **`src/log_parser.py`**: Parses log files for run times and errors
- **`templates/index.html`**: Dashboard HTML template
- **`static/dashboard.js`**: Frontend JavaScript
- **`static/dashboard.css`**: Styling

### Log Parsing

The dashboard parses log files using the standard Python logging format:
```
YYYY-MM-DD HH:MM:SS - logger_name - LEVEL - message
```

Success indicators:
- "Successfully processed endpoint"
- "Load completed"

Error detection:
- Log entries with `ERROR` level

## Configuration

### Environment Variables

- `DASHBOARD_PORT`: Port to run the dashboard on (default: 8080)
- `DASHBOARD_BASE_PATH`: Base path for resolving log files (default: parent directory)
- `DASHBOARD_DEBUG`: Enable Flask debug mode (default: false)

### Volume Mounts

The dashboard requires access to:
- Log directories from all services (read-only)
- Docker socket for container status checks (read-only)
- Project root directory for log path resolution (read-only)

## Security Considerations

- Dashboard is read-only (no write operations)
- Docker socket access requires appropriate permissions
- All volumes are mounted read-only
- No sensitive data is logged or displayed
- Consider adding authentication if exposed externally

## Troubleshooting

### Dashboard shows "docker_unavailable"

The dashboard cannot connect to Docker. Ensure:
- Docker socket is mounted: `-v /var/run/docker.sock:/var/run/docker.sock:ro`
- Container has permission to access Docker socket

### Log files not found

Check that log volumes are mounted correctly:
- Logs should be mounted at `/app/logs/{service_name}/cron.log`
- Verify log files exist in the host directories

### Services show as "not_found"

Container names must match exactly:
- `tc-faction-crimes-pipeline`
- `tc-faction-members-pipeline`
- `tc-items-pipeline`
- `tc-user-events-pipeline`

## Development

### Project Structure

```
TC_dashboard/
├── src/
│   ├── app.py              # Flask application
│   ├── health_checker.py   # Health check orchestration
│   ├── docker_client.py    # Docker API client
│   └── log_parser.py       # Log file parser
├── templates/
│   └── index.html          # Dashboard HTML
├── static/
│   ├── dashboard.js        # Frontend JavaScript
│   └── dashboard.css       # Styles
├── Dockerfile
├── requirements.txt
└── README.md
```

### Adding New Services

To add a new service to monitor:

1. Add service configuration to `src/health_checker.py`:
   ```python
   "tc-new-service": {
       "name": "TC New Service",
       "container": "tc-new-service-pipeline",
       "log_path": "/app/logs/new_service/cron.log",
       "log_path_host": "TC_new_service/logs/cron.log",
   }
   ```

2. Add log volume mount to `docker-compose.yml`

3. Restart the dashboard service

## License

[To be determined]




