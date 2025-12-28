# Information Required Before Code Generation

Please provide the following information to proceed with implementation:

## 1. Torn City API Configuration

### API Key
- [x] **API Key**: **Provided in config** (stored securely, not in version control)
  - Stored in `config/TC_API_config.json` (will be moved to environment variables in code)
  - Note: This key will be stored as an environment variable, never in code

### Faction Information
- [x] **Faction ID**: **API key automatically associates with faction** (faction IDs: 40832, 17991)
- [ ] **Expected Data Volume**: 
  - Approximately how many records per page?
  - Approximately how many total records expected?

## 2. Google Cloud / BigQuery Configuration

### Project Details
- [x] **GCP Project ID**: **`torncity-402423`**
- [x] **BigQuery Dataset Name**: **`torn_data`**
- [x] **BigQuery Table Name**: **`v2_faction_40832_crimes-new`** and **`v2_faction_17991_crimes`** (as specified in config)

### Authentication
- [x] **Service Account Credentials**: 
  - Path: **`config/credentials.json`** (specified in config)
  - Preferred method: Mount as Docker volume, or pass as environment variable? (to be determined)

### Table Schema
- [x] **Schema Definition**: **Provided in `config/oc_records_schema.json`**
  - Schema includes: `id` (STRING, REQUIRED), `date` (DATE, REQUIRED), `record` (RECORD, REPEATED)
  - Schema is defined and ready to use

### Data Strategy
- [x] **Deduplication Key**: **`id` field** (e.g., `"id": "1149286"`) - This is the unique identifier for each record
- [x] **Upsert Strategy**: **MERGE statement** (most efficient method for BigQuery)
  - **Implementation Details**:
    - Use BigQuery MERGE statement with `id` field as the join key
    - Load new records into a temporary staging table
    - MERGE from staging table to target table:
      - When `id` matches: UPDATE existing record with new data
      - When `id` doesn't match: INSERT new record
    - This approach is more efficient than individual inserts or delete+insert operations
    - Atomic operation ensures data consistency
- [ ] **Partitioning**: Should the table be partitioned by date? (Recommended for large datasets)
  - Note: Currently using "replace" storage mode in config, but deduplication strategy supports "append" mode with MERGE

## 3. Docker & Deployment Configuration

### Docker Setup
- [ ] **Container Registry**: Where should the Docker image be published?
  - Docker Hub
  - Google Container Registry (GCR)
  - Other (specify)
- [x] **Orchestration Platform**: How will the container run?
  - **Standalone Docker** (with cron)
    - Initially: Local Docker instance for testing
    - Eventually: Remote host running Docker
  - Docker Compose
  - Kubernetes
  - Google Cloud Run
  - Other (specify)

### Scheduling
- [x] **Timezone**: **Central time (America/Chicago)** - Used for the 15-minute schedule
- [x] **Frequency**: **PT15M (15 minutes)** - Specified in config
- [ ] **Scheduling Method**: 
  - Cron job on host machine
  - Cron job inside container
  - External scheduler (Cloud Scheduler, etc.)

### Logging
- [x] **Logging Destination**: **stdout/stderr (Docker logs)**
  - Logs will be written to stdout/stderr for Docker to capture
  - Accessible via `docker logs <container_name>` for troubleshooting
  - No external logging services required
  - Structured logging format (JSON) for easy parsing if needed

## 4. Error Handling & Notifications

### Error Handling
- [x] **Retry Strategy**: 
  - Maximum retry attempts: **3** (specified in config)
  - Retry delay: **60 seconds** (specified in config)
  - Exponential backoff: Yes/No (to be implemented)
- [ ] **Failure Notification**: How should we notify on failures?
  - Email
  - Slack webhook
  - PagerDuty
  - Google Cloud Monitoring alerts
  - Log only (no notifications)
  - Other (specify)

### Data Processing
- [x] **Storage Mode**: **"replace"** (specified in config for crimes endpoints)
  - Note: Deduplication strategy document describes MERGE/upsert approach for "append" mode
  - Current config uses "replace" mode, which may need clarification
- [ ] **Track New vs Existing**: Should we track which records are new vs. already in the database?
- [ ] **Data Transformations**: Any transformations needed on the data before storing?
  - Date format conversions
  - Field renaming
  - Data enrichment
  - None (store raw data)

## 5. Development Preferences

### Code Style
- [x] **Python Version**: **Python 3.13** (latest stable version)
- [x] **API Configuration**: **Provided in `config/TC_API_config.json`**
  - Rate limit: 60 requests/minute (config) vs 100 requests/minute (API limit - may need adjustment)
  - Timeout: 30 seconds
  - Multiple endpoints configured (crimes, members, items, basic, currency)

### Testing
- [ ] **Testing Requirements**: 
  - Unit tests required?
  - Integration tests?
  - Test coverage target?

## 6. Additional Requirements

- [ ] **Any other specific requirements or constraints?**
  - Performance requirements
  - Resource limits
  - Compliance requirements
  - Integration with other systems

---

## Summary of Provided Information

Based on the configuration files and documentation, the following has been provided:

✅ **API Configuration**: API keys, endpoints, and settings in `config/TC_API_config.json`
✅ **GCP Configuration**: Project ID (`torncity-402423`), dataset (`torn_data`), table names
✅ **Schema**: BigQuery schema defined in `config/oc_records_schema.json`
✅ **Deduplication Strategy**: `id` field with MERGE statement approach (documented in `DEDUPLICATION_STRATEGY.md`)
✅ **Python Version**: 3.13
✅ **Timezone**: America/Chicago (Central time)
✅ **Retry Settings**: 3 retries, 60 second delay
✅ **Logging**: stdout/stderr (Docker logs)
✅ **Deployment**: Standalone Docker

## Remaining Questions

The following items still need clarification:

- **Expected Data Volume**: Records per page and total records expected
- **Scheduling Method**: Cron on host vs inside container vs external scheduler
- **Partitioning**: Whether to partition BigQuery tables by date
- **Storage Mode Clarification**: Config shows "replace" mode, but deduplication strategy describes MERGE/upsert for "append" mode
- **Failure Notifications**: How to notify on failures
- **Data Tracking**: Whether to track new vs existing records
- **Data Transformations**: Any needed transformations
- **Testing Requirements**: Unit tests, integration tests, coverage targets
- **Docker Registry**: Where to publish Docker images
- **Service Account Auth Method**: Mount as volume or environment variable


