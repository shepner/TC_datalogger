# Torn City API Data Logger

A collection of microservices that automatically fetch data from the Torn City API and store it in Google BigQuery. Each microservice operates independently and can be run separately or together via Docker Compose orchestration.

## Overview

This project contains multiple independent microservices, each responsible for fetching specific data from the Torn City API and storing it in BigQuery:

- **TC_faction_crimes**: Fetches organized crime records (append mode)
- **TC_faction_members**: Fetches faction member data (replace mode)
- **TC_items**: Fetches Torn City items catalog (replace mode)
- **TC_user_events**: Fetches user events (append mode)

All services:
- Run every 15 minutes
- Use the same API key (configurable per service)
- Have separate GCP credentials files
- Auto-generate schemas from first API response
- Operate as independent microservices

## Architecture

```
TC_datalogger/
├── TC_faction_crimes/     # Organized crime records (append mode)
├── TC_faction_members/    # Faction members (replace mode)
├── TC_items/              # Items catalog (replace mode)
├── TC_user_events/        # User events (append mode)
└── docker-compose.yml     # Root-level orchestration
```

## Quick Start

### Prerequisites

1. **Docker**: Ensure Docker and Docker Compose are installed
2. **GCP Credentials**: Each service needs its own service account credentials:
   - `TC_faction_crimes/config/credentials.json` (project: torncity-402423)
   - `TC_faction_members/config/credentials.json` (project: torncity-402423)
   - `TC_items/config/credentials.json` (project: torncity-402423)
   - `TC_user_events/config/credentials.json` (project: torncity-2764614)
3. **API Keys**: Configure in each service's `config/TC_API_config.json`

### Running All Services Together

**Start all services:**
```bash
cd TC_datalogger
docker-compose up -d
```

**Stop all services:**
```bash
docker-compose down
```

**View logs for all services:**
```bash
docker-compose logs -f
```

**View logs for specific service:**
```bash
docker-compose logs -f tc-faction-members
```

**Rebuild and restart all services:**
```bash
docker-compose up -d --build
```

**Start/stop specific services:**
```bash
docker-compose up -d tc-faction-members tc-items
docker-compose stop tc-user-events
```

### Running Individual Services

Each service can be run independently:

```bash
cd TC_faction_members
docker-compose up -d
```

Or run locally:
```bash
cd TC_faction_members
pip install -r requirements.txt
python -m src.main
```

## Services Overview

### TC_faction_crimes
- **Endpoint**: `/v2/faction/crimes`
- **Table**: `torncity-402423.torn_data.v2_faction_40832_crimes-raw`
- **Storage Mode**: `append` (incremental updates with deduplication)
- **GCP Project**: `torncity-402423`
- **Documentation**: [TC_faction_crimes/README.md](TC_faction_crimes/README.md)

### TC_faction_members
- **Endpoint**: `/v2/faction/members?striptags=true`
- **Table**: `torncity-402423.torn_data.v2_faction_40832_members-raw`
- **Storage Mode**: `replace` (full table replacement)
- **GCP Project**: `torncity-402423`
- **Documentation**: [TC_faction_members/README.md](TC_faction_members/README.md)

### TC_items
- **Endpoint**: `/v2/torn/items`
- **Table**: `torncity-402423.torn_data.v2_torn_items-raw`
- **Storage Mode**: `replace` (full table replacement)
- **GCP Project**: `torncity-402423`
- **Documentation**: [TC_items/README.md](TC_items/README.md)

### TC_user_events
- **Endpoint**: `/v2/user/events?striptags=true&limit=100`
- **Table**: `torncity-402423.torn_data.v2_torn_user_events-raw`
- **Storage Mode**: `append` (incremental updates with deduplication)
- **GCP Project**: `torncity-402423`
- **Documentation**: [TC_user_events/README.md](TC_user_events/README.md)

## Storage Modes

### Replace Mode
Used by: `TC_faction_members`, `TC_items`

- Each run completely replaces the entire BigQuery table
- No deduplication needed (table is wiped and reloaded)
- Useful for reference data that changes over time
- Ensures the table always reflects the current state from the API

### Append Mode
Used by: `TC_faction_crimes`, `TC_user_events`

- Each run adds new records to the existing table
- Uses MERGE statement for deduplication based on `id` field
- Updates existing records if they already exist (upsert behavior)
- Preserves historical data while keeping it current
- Useful for event data that accumulates over time

## Configuration

### API Keys
All services share the same API key by default. Configure in each service's `config/TC_API_config.json`:

```json
{
  "api_keys": {
    "faction_40832": "YOUR_API_KEY_HERE"
  }
}
```

Or use environment variables:
```bash
export TC_API_KEY_FACTION_40832=your_key_here
```

### GCP Credentials
Each service requires its own GCP service account credentials file:
- Place credentials JSON file at `{service}/config/credentials.json`
- Ensure service account has BigQuery Data Editor role
- **Note**: All services use the same GCP project (`torncity-402423`)

## Schema Auto-Generation

**No initial schema files are needed!** Each service:
1. Fetches data from API on first run
2. Infers schema from the first API response
3. Creates the BigQuery table with auto-detected schema
4. Automatically adds new fields as they appear in future API responses

## Benefits of Root-Level Orchestration

1. **Single Command Management**: Start/stop all services with one command
2. **Unified Logging**: View logs from all services together
3. **Consistent Environment**: All services share the same Docker Compose environment
4. **Easy Scaling**: Can easily add/remove services
5. **Resource Management**: Docker Compose manages resource allocation

## Individual Service Control

Each service can still be run independently:
- Individual `docker-compose.yml` files in each service directory remain functional
- Services can be started individually: `cd TC_faction_members && docker-compose up -d`
- Root-level orchestration is optional but recommended for production

## Troubleshooting

### View All Service Logs
```bash
docker-compose logs -f
```

### View Specific Service Logs
```bash
docker-compose logs -f tc-faction-members
```

### Check Service Status
```bash
docker-compose ps
```

### Restart a Specific Service
```bash
docker-compose restart tc-faction-members
```

### Common Issues

- **Credentials not found**: Ensure each service has `config/credentials.json`
- **API key errors**: Check API keys in each service's config file
- **BigQuery permission errors**: Verify service accounts have BigQuery Data Editor role
- **Wrong GCP project**: Ensure all services use project `torncity-402423`

## Project Structure

```
TC_datalogger/
├── README.md                    # This file
├── docker-compose.yml           # Root-level orchestration
├── TC_faction_crimes/           # Organized crime records service
│   ├── README.md
│   ├── src/
│   ├── config/
│   ├── docker/
│   └── ...
├── TC_faction_members/          # Faction members service
│   ├── README.md
│   ├── src/
│   ├── config/
│   ├── docker/
│   └── ...
├── TC_items/                    # Items catalog service
│   ├── README.md
│   ├── src/
│   ├── config/
│   ├── docker/
│   └── ...
└── TC_user_events/              # User events service
    ├── README.md
    ├── src/
    ├── config/
    ├── docker/
    └── ...
```

## Security

- API keys and credentials are never committed to version control
- All sensitive data is managed through environment variables
- Service account credentials are mounted as Docker volumes
- Each service operates independently with its own credentials

## Support

For service-specific questions, refer to individual service README files:
- [TC_faction_crimes/README.md](TC_faction_crimes/README.md)
- [TC_faction_members/README.md](TC_faction_members/README.md)
- [TC_items/README.md](TC_items/README.md)
- [TC_user_events/README.md](TC_user_events/README.md)

## License

[To be determined]

