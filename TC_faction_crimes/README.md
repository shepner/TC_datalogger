# Torn City Faction Crimes to BigQuery

A data pipeline that automatically fetches Organized Crime (OC) records from the Torn City API and stores them in Google BigQuery. The pipeline runs every 15 minutes within a Docker container.

## Overview

This project implements a robust ETL pipeline that:
- Fetches OC records from Torn City API v2 (`/v2/faction/crimes`)
- Handles pagination to retrieve all available records
- Stores records in Google BigQuery with deduplication
- Runs automatically every 15 minutes
- Executes within a Docker container for portability

## Project Status

**✅ Implementation Complete** - Code has been generated and is ready for testing and deployment.

## Documentation

- **[GOVERNANCE.md](GOVERNANCE.md)**: Rules and standards for development, security, error handling, and operations
- **[INFORMATION_NEEDED.md](INFORMATION_NEEDED.md)**: Configuration requirements and setup information
- **[LEARNINGS.md](LEARNINGS.md)**: Project learnings, decisions, patterns, and context for future reference

## Quick Start

### Prerequisites

1. **Service Account Credentials**: Place your GCP service account JSON file at `config/credentials.json`
2. **API Keys**: Configure in `config/TC_API_config.json` or via environment variables
3. **Docker**: Ensure Docker is installed and running

### Running with Docker

1. **Build the Docker image**:
   ```bash
   docker build -t tc-faction-crimes .
   ```

2. **Run the container**:
   ```bash
   docker run -d \
     --name tc-pipeline \
     -v $(pwd)/config/credentials.json:/app/config/credentials.json:ro \
     -v $(pwd)/logs:/app/logs \
     tc-faction-crimes
   ```

   Or use docker-compose:
   ```bash
   docker-compose up -d
   ```

3. **View logs**:
   ```bash
   docker logs -f tc-pipeline
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
   python -m src.main --endpoint v2_faction_40832_crimes
   ```

### Testing

Run the test suite:
```bash
pytest tests/
```

With coverage:
```bash
pytest tests/ --cov=src --cov-report=html
```

## Automatic Commits

This project includes an automatic commit system that commits changes when:
- Significant changes have been made (source code, config, docs)
- More than 24 hours have passed since the last commit

### Usage

Run the auto-commit script manually:
```bash
python3 auto_commit.py
```

The script will:
- Check for significant changes (source files, config, docs)
- Check time since last commit
- Automatically stage and commit changes if conditions are met
- Generate meaningful commit messages based on changed files
- Skip commits if only log files or temporary files changed
- Never commit sensitive files (credentials, API keys, `.env` files)

### Configuration

The auto-commit behavior is configured in `auto_commit.py`:
- `MAX_HOURS_SIN_COMMIT`: Maximum hours since last commit (default: 24)
- `SIGNIFICANT_PATTERNS`: File patterns that indicate significant changes
- `IGNORE_PATTERNS`: Files/directories to ignore

See `GOVERNANCE.md` section 10.3 for detailed rules about automatic commits.

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
                                                            └─────────────┘
```

## Key Features

- ✅ **Pagination Handling**: Automatically fetches all pages from the API with duplicate detection
- ✅ **Rate Limiting**: Respects Torn City API rate limits (configurable, default 60 req/min)
- ✅ **Deduplication**: Prevents duplicate records in BigQuery using MERGE statements with `id` field
- ✅ **Schema Evolution**: Automatically detects and adds new fields from API responses to BigQuery schema
- ✅ **Error Handling**: Robust retry logic with exponential backoff for transient errors
- ✅ **Logging**: Comprehensive structured logging with context
- ✅ **Dockerized**: Runs in a containerized environment with cron scheduling
- ✅ **Scheduled**: Automatic execution every 15 minutes (configurable via ISO 8601 duration)
- ✅ **Flexible Storage Modes**: Supports both replace and append modes with automatic deduplication

## Requirements

### Prerequisites
- Python 3.13+
- Docker
- Google Cloud Project with BigQuery enabled
- Torn City API key with faction crimes access
- Service account credentials for BigQuery

### Dependencies

See `requirements.txt` for full list. Main dependencies:
- `requests` - API client
- `google-cloud-bigquery` - BigQuery integration
- `pytz` - Timezone handling
- `pytest` - Testing framework

## Configuration

Configuration is managed through:
1. **JSON Config File**: `config/TC_API_config.json` - Main configuration
2. **Environment Variables**: Can override config file values
   - `TC_API_KEY_<key_name>` - Override API keys
   - `TC_GCP_CREDENTIALS_PATH` - Override credentials path
   - `TZ` - Timezone (defaults to America/Chicago)

See `config/TC_API_config.json` for endpoint configuration and `INFORMATION_NEEDED.md` for details.

## Project Structure

```
TC_faction_crimes/
├── README.md                 # This file
├── GOVERNANCE.md            # Governance rules and standards
├── INFORMATION_NEEDED.md    # Configuration requirements
├── LEARNINGS.md             # Project learnings and patterns
├── docker/
│   └── entrypoint.sh        # Container entrypoint script
├── Dockerfile               # Docker container definition
├── src/
│   ├── main.py              # Entry point and pipeline orchestration
│   ├── api_client.py        # Torn City API client with pagination
│   ├── bigquery_loader.py   # BigQuery operations and schema management
│   ├── data_processor.py    # Data transformation
│   ├── scheduler.py        # Scheduling logic
│   └── config.py            # Configuration management
├── config/
│   ├── TC_API_config.json   # API and endpoint configuration
│   ├── oc_records_schema.json # BigQuery schema definition
│   └── credentials.json      # GCP service account (not in git)
├── tests/
│   └── test_api_client.py   # API client tests
├── logs/                     # Log files directory
├── requirements.txt         # Python dependencies
├── docker-compose.yml       # Docker Compose configuration
├── auto_commit.py           # Automatic commit script
├── check_bq_count.py        # Utility: Check BigQuery record counts
├── delete_table.py          # Utility: Delete BigQuery table
├── load_all_historical.py   # Utility: Load all historical records
└── validate_counts.py       # Utility: Validate API vs BigQuery counts
```

## Security

- API keys and credentials are never committed to version control
- All sensitive data is managed through environment variables
- Service account credentials are mounted as Docker volumes or passed as environment variables
- See `GOVERNANCE.md` for detailed security rules

## How It Works

1. **Scheduling**: Pipeline runs every 15 minutes inside the container via cron (or use `--schedule` flag for local runs)
2. **API Fetching**: Fetches all pages of data from Torn City API with automatic pagination and duplicate detection
3. **Data Processing**: Transforms API records into BigQuery-compatible format, adds `fetched_at` timestamp
4. **Data Loading**: 
   - **Replace mode**: Overwrites entire table (use with caution)
   - **Append mode**: Uses MERGE statement for deduplication based on `id` field (recommended for incremental updates)
5. **Schema Management**: Automatically detects new fields in API responses and adds them to BigQuery schema
6. **Error Handling**: Retries with exponential backoff, logs all errors with context

## Troubleshooting

### View Container Logs
```bash
docker logs tc-pipeline
```

### Check Cron Logs
```bash
docker exec tc-pipeline cat /app/logs/cron.log
```

### Run Manually Inside Container
```bash
docker exec -it tc-pipeline python -m src.main
```

### Common Issues

- **Credentials not found**: Ensure `config/credentials.json` exists and is mounted
- **API key errors**: Check API keys in config file or environment variables
- **BigQuery permission errors**: Verify service account has BigQuery Data Editor role
- **No data fetched**: Check API endpoint URLs and API key permissions

## Utility Scripts

The project includes several utility scripts for maintenance and validation:

### Check BigQuery Record Count
```bash
python check_bq_count.py
```
Displays total record count, unique IDs, and oldest/newest record timestamps for the configured table.

### Load All Historical Records
```bash
python load_all_historical.py
```
Fetches and loads all historical records from the API (bypasses time window filters). Useful for initial data loading.

### Validate Counts
```bash
python validate_counts.py
```
Compares record counts between the API and BigQuery to ensure data consistency.

### Delete Table
```bash
python delete_table.py
```
Deletes the BigQuery table configured in the first endpoint (use with caution).

## Support

For questions or issues, refer to:
- `GOVERNANCE.md` for development standards and rules
- `INFORMATION_NEEDED.md` for configuration help
- `LEARNINGS.md` for project context and patterns

## License

[To be determined]
