# Project Learnings & Notes

This document captures important decisions, patterns, mistakes, and context that should be remembered for future development and AI-assisted coding sessions.

## Purpose

This file helps:
- **AI assistants** understand project context and avoid repeating mistakes
- **Developers** remember why certain decisions were made
- **Future maintainers** understand patterns and gotchas

## How to Use This File

- **Add entries** when you discover something important, make a key decision, or encounter a mistake
- **Update entries** if patterns or decisions change
- **Reference this file** at the start of new development sessions
- **Keep it concise** - use bullet points and clear headings

---

## Architecture Decisions

### Data Storage Strategy
- **Deduplication**: Uses `id` field as unique key with BigQuery MERGE statements
- **Storage Mode**: 
  - Configurable per endpoint via `storage_mode` field
  - "replace" mode: Overwrites entire table (use with caution)
  - "append" mode: Uses MERGE statement for deduplication (recommended for incremental updates)
- **Schema**: Defined in `config/oc_records_schema.json` - automatically extended when new fields detected
- **Schema Evolution**: New fields from API responses are automatically detected and added to BigQuery schema

### API Client Patterns
- **Pagination**: Automatically handles pagination to fetch all records with duplicate detection
- **Loop Detection**: Tracks seen record IDs to detect when API loops back to beginning
- **Rate Limiting**: Configurable rate limit (default 60 req/min, API limit is 100 req/min)
- **Error Handling**: Exponential backoff for 429 errors and transient errors, fail fast on 401/403
- **Response Structure**: Handles various response formats (data, crimes, members, items keys)

### Configuration Management
- **Primary**: JSON config file (`config/TC_API_config.json`)
- **Override**: Environment variables can override config values
- **Credentials**: Never committed, mounted as Docker volumes or env vars
- **Timezone**: Defaults to America/Chicago (Central Time)

---

## Common Mistakes & Gotchas

### Schema Mismatches
- **Problem**: API may return new fields not in schema
- **Solution**: Code detects new fields and logs warnings, but includes them in data
- **Action**: Update `oc_records_schema.json` when new fields are detected
- **Note**: Schema validation happens before table creation, but not on existing tables

### BigQuery Table Operations
- **Replace Mode**: Completely overwrites table - use carefully
- **Append Mode**: Uses MERGE statement - safe to re-run (idempotent)
- **Table Creation**: Only creates tables if they don't exist
- **Schema Updates**: Automatically adds new fields to existing tables (except pre-existing protected tables)
- **Protected Tables**: Only specific pre-existing tables (v2_faction_40832_crimes-new, v2_faction_40832_crimes-raw) can be modified

### API Key Configuration
- **Format**: API keys stored in config with names, referenced by endpoint config
- **Environment Override**: Use `TC_API_KEY_<key_name>` format
- **Validation**: Empty API keys log warnings but don't fail immediately

### Docker & Scheduling
- **Cron**: Runs inside container, logs to stdout/stderr
- **Timezone**: Container timezone must match expected timezone (America/Chicago)
- **Volume Mounts**: Credentials must be mounted as read-only volumes

---

## Code Patterns

### Error Handling
- **Never silently swallow errors** - always log with context
- **Use appropriate log levels**: DEBUG for details, INFO for flow, WARNING for recoverable issues, ERROR for failures
- **Exit codes**: 0 = success, non-zero = failure

### Logging
- **Format**: Structured logging with timestamps, component names, and context
- **Output**: stdout/stderr for Docker compatibility
- **Context**: Include endpoint names, record IDs, error codes in log messages

### Data Processing
- **Validation**: Validate API response structure before processing
- **Error Collection**: Collect processing errors but continue processing other records
- **New Field Detection**: Automatically detects fields not in schema and warns

### Testing
- **Mocking**: Mock external dependencies (API, BigQuery)
- **Coverage**: Aim for >80% coverage on core logic
- **Test Error Paths**: Don't just test happy paths

---

## Project-Specific Context

### Torn City API
- **Endpoint Format**: `/v2/faction/{faction_id}/crimes`
- **Pagination**: Uses `offset` parameter
- **Rate Limits**: 100 requests per minute
- **Authentication**: API key in query parameter or header

### BigQuery Setup
- **Service Account**: Requires BigQuery Data Editor role (minimum)
- **Project/Dataset**: Configured in config file or environment variables
- **Table Naming**: Based on endpoint name from config

### Docker Configuration
- **Base Image**: Python 3.13+
- **Cron Schedule**: Every 15 minutes (PT15M)
- **Entrypoint**: `docker/entrypoint.sh` sets up cron and logging

---

## Development Workflow

### Making Changes
1. Test locally first: `python -m src.main`
2. Test specific endpoint: `python -m src.main --endpoint <name>`
3. Run tests: `pytest tests/`
4. Check logs: `docker logs tc-pipeline`

### Auto-Commit Behavior
- Commits when significant changes made (source, config, docs)
- Commits if >24 hours since last commit
- Never commits sensitive files (credentials, API keys, .env)
- Run manually: `python3 auto_commit.py`

### Configuration Changes
- Update `config/TC_API_config.json` for endpoint changes
- Update `config/oc_records_schema.json` for schema changes
- Use environment variables for runtime overrides

---

## Known Issues & Limitations

### Current Limitations
- Schema validation only checks critical fields (id, date) on existing tables
- Automatic schema updates only work for top-level fields (nested RECORD structures require manual updates)
- Replace mode completely overwrites data (no incremental updates)
- No dead letter queue for permanently failed records
- Time window filtering requires API support for timestamp parameters

### Future Improvements
- Consider partitioning tables by date for large datasets
- Enhanced nested RECORD structure inference for automatic schema updates
- Add dead letter queue for failed records
- Consider Cloud Logging integration for GCP deployments
- Add support for more complex time window filtering strategies

---

## AI Assistant Notes

### When Working on This Project
- **Always check** `GOVERNANCE.md` for rules and standards
- **Reference** `INFORMATION_NEEDED.md` for configuration details
- **Follow** existing patterns in codebase
- **Never** commit credentials or API keys
- **Always** log errors with sufficient context
- **Validate** schema before making BigQuery changes

### Common AI Mistakes to Avoid
- Don't modify existing table schemas without explicit request
- Don't change storage mode without understanding impact
- Don't remove error handling or logging
- Don't hardcode credentials or API keys
- Don't skip schema validation

### Preferred Patterns
- Use type hints for all functions
- Follow PEP 8 style guide
- Use Google-style docstrings
- Implement retry logic with exponential backoff
- Use structured logging with context

---

## Change Log

### 2025-01-XX
- Initial learnings document created
- Documented architecture decisions and common patterns

---

## Quick Reference

### Key Files
- `src/main.py` - Entry point and pipeline orchestration
- `src/api_client.py` - Torn City API client with pagination and duplicate detection
- `src/bigquery_loader.py` - BigQuery operations, schema management, and MERGE-based deduplication
- `src/data_processor.py` - Data transformation and new field detection
- `src/config.py` - Configuration management with environment variable overrides
- `src/scheduler.py` - Scheduling logic with timezone support
- `config/TC_API_config.json` - API and endpoint configuration
- `config/oc_records_schema.json` - BigQuery schema definition

### Utility Scripts
- `check_bq_count.py` - Check BigQuery record counts and statistics
- `load_all_historical.py` - Load all historical records (bypasses time windows)
- `validate_counts.py` - Validate API vs BigQuery record counts
- `delete_table.py` - Delete BigQuery table (use with caution)
- `auto_commit.py` - Automatic git commit script

### Key Commands
```bash
# Run locally
python -m src.main

# Run with scheduling
python -m src.main --schedule

# Run specific endpoint
python -m src.main --endpoint v2_faction_40832_crimes

# Run tests
pytest tests/

# Docker
docker-compose up -d
docker logs -f tc-pipeline
```

---

*Last Updated: [Update this when adding new entries]*

