# Torn City Faction Chains to BigQuery

A data pipeline that automatically fetches faction chain data from the Torn City API and stores it in Google BigQuery. The pipeline runs every hour within a Docker container.

## Overview

This project implements a robust ETL pipeline that:
- Fetches chain IDs from Torn City API (`/faction/chains`)
- For each chain ID, fetches detailed chain report (`/faction/{chainId}/chainreport`)
- Stores records in Google BigQuery using **append mode** (incremental updates with deduplication)
- Runs automatically every hour
- Executes within a Docker container for portability
- Auto-generates BigQuery schema from first API response

## Quick Start

### Prerequisites

1. **Service Account Credentials**: Place your GCP service account JSON file at `config/credentials.json`
2. **API Keys**: Configure in `config/TC_API_config.json` or via environment variables
3. **Docker**: Ensure Docker is installed and running

### Running with Docker

1. **Build the Docker image**:
   ```bash
   docker build -t tc-faction-chains .
   ```

2. **Run the container**:
   ```bash
   docker run -d \
     --name tc-faction-chains-pipeline \
     -v $(pwd)/config/credentials.json:/app/config/credentials.json:ro \
     -v $(pwd)/logs:/app/logs \
     tc-faction-chains
   ```

   Or use docker-compose:
   ```bash
   docker-compose up -d
   ```

3. **View logs**:
   ```bash
   docker logs -f tc-faction-chains-pipeline
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

## Configuration

### API Endpoint

- **Chain List URL**: `https://api.torn.com/faction/chains`
- **Chain Report URL**: `https://api.torn.com/faction/{chainId}/chainreport`
- **Table**: `torncity-402423.torn_data.v2_faction_40832_chains-raw`
- **Storage Mode**: `append` (incremental updates with deduplication)
- **Frequency**: `PT1H` (1 hour)
- **GCP Project**: `torncity-402423`

### Storage Mode: Append

This service uses **append mode**, which means:
- Each run adds new records to the existing BigQuery table
- Uses MERGE statement for deduplication based on `id` field (chain ID)
- Updates existing records if they already exist (upsert behavior)
- Preserves historical data while keeping it current
- Useful for chain data that accumulates over time

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌─────────────┐
│ Torn City   │────▶│ API Client   │────▶│ Data        │────▶│ BigQuery    │
│ API         │     │ (2-step:     │     │ Processor   │     │ Loader      │
│             │     │  chains list │     │             │     │             │
│             │     │  + reports)  │     │             │     │             │
└─────────────┘     └──────────────┘     └─────────────┘     └─────────────┘
```

## Data Collection Process

1. **Fetch Chain IDs**: Calls `/faction/chains` to get list of all chain IDs
2. **Fetch Chain Reports**: For each chain ID, calls `/faction/{chainId}/chainreport` to get detailed report
3. **Process & Store**: Processes all reports and stores in BigQuery with deduplication

## Notes

- Chain reports are fetched sequentially to respect API rate limits
- Each chain report includes the chain_id for tracking
- The `id` field is used for deduplication (typically the chain_id)
- Schema is auto-generated from the first API response

