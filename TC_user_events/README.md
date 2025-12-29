# Torn City User Events to BigQuery

A data pipeline that automatically fetches user events from the Torn City API and stores them in Google BigQuery. The pipeline runs every 15 minutes within a Docker container.

## Overview

This project implements a robust ETL pipeline that:
- Fetches user events from Torn City API v2 (`/v2/user/events`)
- Handles pagination to retrieve all available records
- Stores records in Google BigQuery using **append mode** (incremental updates with deduplication)
- Runs automatically every 15 minutes
- Executes within a Docker container for portability
- Auto-generates BigQuery schema from first API response

## Project Status

**✅ Implementation Complete** - Code has been generated and is ready for testing and deployment.

## Quick Start

### Prerequisites

1. **Service Account Credentials**: Place your GCP service account JSON file at `config/credentials.json`
   - **Note**: This service uses GCP project `torncity-2764614` (different from other services)
2. **API Keys**: Configure in `config/TC_API_config.json` or via environment variables
3. **Docker**: Ensure Docker is installed and running

### Running with Docker

1. **Build the Docker image**:
   ```bash
   docker build -t tc-user-events .
   ```

2. **Run the container**:
   ```bash
   docker run -d \
     --name tc-user-events-pipeline \
     -v $(pwd)/config/credentials.json:/app/config/credentials.json:ro \
     -v $(pwd)/logs:/app/logs \
     tc-user-events
   ```

   Or use docker-compose:
   ```bash
   docker-compose up -d
   ```

3. **View logs**:
   ```bash
   docker logs -f tc-user-events-pipeline
   ```

### Running Locally (for testing)

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run once**:
   ```bash
   python -m src.main
   ```

3. **Run with scheduling**:
   ```bash
   python -m src.main --schedule
   ```

4. **Run specific endpoint**:
   ```bash
   python -m src.main --endpoint v2_torn_user_events
   ```

## Configuration

### API Endpoint

- **URL**: `https://api.torn.com/v2/user/events?striptags=true&limit=100`
- **Table**: `torncity-402423.torn_data.v2_torn_user_events-raw`
- **Storage Mode**: `append` (incremental updates with deduplication)
- **Frequency**: `PT15M` (15 minutes)
- **GCP Project**: `torncity-2764614` (different from other services)

### Storage Mode: Append

This service uses **append mode**, which means:
- Each run adds new records to the existing BigQuery table
- Uses MERGE statement for deduplication based on `id` field
- Updates existing records if they already exist (upsert behavior)
- Preserves historical data while keeping it current
- Useful for event data that accumulates over time

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌─────────────┐
│ Torn City   │────▶│ API Client   │────▶│ Data        │────▶│ BigQuery    │
│ API         │     │ (Pagination) │     │ Processor   │     │ Loader      │
└─────────────┘     └──────────────┘     └─────────────┘     └─────────────┘
                                                                    │
                                                                    ▼
                                                            ┌─────────────┐
                                                            │ BigQuery    │
                                                            │ Table       │
                                                            │ (APPEND)    │
                                                            │ MERGE       │
                                                            └─────────────┘
```

## Key Features

- ✅ **Pagination Handling**: Automatically fetches all pages from the API with duplicate detection
- ✅ **Rate Limiting**: Respects Torn City API rate limits (configurable, default 60 req/min)
- ✅ **Append Mode with Deduplication**: Uses MERGE statement for efficient upserts
- ✅ **Schema Auto-Generation**: Automatically detects and creates BigQuery schema from first API response
- ✅ **Schema Evolution**: Automatically adds new fields from API responses to BigQuery schema
- ✅ **Error Handling**: Robust retry logic with exponential backoff for transient errors
- ✅ **Logging**: Comprehensive structured logging with context
- ✅ **Dockerized**: Runs in a containerized environment with cron scheduling
- ✅ **Scheduled**: Automatic execution every 15 minutes (configurable via ISO 8601 duration)

## Requirements

### Prerequisites
- Python 3.13+
- Docker
- Google Cloud Project with BigQuery enabled (project: `torncity-402423`)
- Torn City API key with user events access
- Service account credentials for BigQuery

### Dependencies

See `requirements.txt` for full list. Main dependencies:
- `requests` - API client
- `google-cloud-bigquery` - BigQuery integration
- `pytz` - Timezone handling

## Configuration

Configuration is managed through:
1. **JSON Config File**: `config/TC_API_config.json` - Main configuration
2. **Environment Variables**: Can override config file values
   - `TC_API_KEY_<key_name>` - Override API keys
   - `TC_GCP_CREDENTIALS_PATH` - Override credentials path
   - `TZ` - Timezone (defaults to America/Chicago)

## Project Structure

```
TC_user_events/
├── README.md                 # This file
├── docker/
│   └── entrypoint.sh        # Container entrypoint script
├── Dockerfile               # Docker container definition
├── src/
│   ├── main.py              # Entry point and pipeline orchestration
│   ├── api_client.py        # Torn City API client with pagination
│   ├── bigquery_loader.py   # BigQuery operations and schema management
│   ├── data_processor.py    # Data transformation
│   ├── scheduler.py         # Scheduling logic
│   └── config.py            # Configuration management
├── config/
│   ├── TC_API_config.json   # API and endpoint configuration
│   └── credentials.json      # GCP service account (not in git)
├── logs/                     # Log files directory
├── requirements.txt         # Python dependencies
└── docker-compose.yml       # Docker Compose configuration
```

## Security

- API keys and credentials are never committed to version control
- All sensitive data is managed through environment variables
- Service account credentials are mounted as Docker volumes or passed as environment variables

## How It Works

1. **Scheduling**: Pipeline runs every 15 minutes inside the container via cron
2. **API Fetching**: Fetches all pages of data from Torn City API with automatic pagination
3. **Data Processing**: Transforms API records into BigQuery-compatible format, adds `fetched_at` timestamp
4. **Data Loading**: 
   - **Append mode**: Uses MERGE statement for deduplication based on `id` field
   - Updates existing records, inserts new ones
5. **Schema Management**: Automatically detects new fields in API responses and adds them to BigQuery schema
6. **Error Handling**: Retries with exponential backoff, logs all errors with context

## Troubleshooting

### View Container Logs
```bash
docker logs tc-user-events-pipeline
```

### Check Cron Logs
```bash
docker exec tc-user-events-pipeline cat /app/logs/cron.log
```

### Run Manually Inside Container
```bash
docker exec -it tc-user-events-pipeline python -m src.main
```

### Common Issues

- **Credentials not found**: Ensure `config/credentials.json` exists and is mounted
- **API key errors**: Check API keys in config file or environment variables
- **BigQuery permission errors**: Verify service account has BigQuery Data Editor role
- **No data fetched**: Check API endpoint URLs and API key permissions

## Support

For questions or issues, refer to the parent project documentation or the template service `TC_faction_crimes` for examples.

## License

[To be determined]

