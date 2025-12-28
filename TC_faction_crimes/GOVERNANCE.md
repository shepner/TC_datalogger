# Project Governance Rules

This document defines the rules and standards for the Torn City Faction Crimes to BigQuery project.

## 1. Security Rules

### 1.1 Credentials Management
- **NEVER** commit API keys, passwords, or service account credentials to version control
- Use environment variables for all sensitive configuration
- Provide `.env.example` file with placeholder values
- Document required environment variables in README
- Use Docker secrets or mounted volumes for credential files when possible

### 1.2 Access Control
- Limit service account permissions to minimum required (BigQuery Data Editor)
- Rotate API keys periodically
- Use separate API keys for development and production

### 1.3 Code Security
- Review dependencies for known vulnerabilities
- Keep dependencies up to date
- Use `pip-audit` or similar tools to check for security issues

## 2. Error Handling Rules

### 2.1 API Error Handling
- Implement exponential backoff for API rate limit errors (429)
- Retry transient errors (network timeouts, 5xx errors)
- Fail fast on authentication errors (401, 403)
- Log all API errors with context (endpoint, status code, response body)

### 2.2 BigQuery Error Handling
- Validate data schema before insertion
- Handle quota exceeded errors gracefully
- Implement retry logic for transient BigQuery errors
- Log failed insertions with record details

### 2.3 General Error Handling
- Never silently swallow errors
- Always log errors with sufficient context
- Exit with appropriate exit codes (0 = success, non-zero = failure)
- Consider implementing dead letter queue for permanently failed records

## 3. Logging Rules

### 3.1 Log Levels
- **DEBUG**: Detailed information for diagnosing problems
- **INFO**: General informational messages (start/end of runs, record counts)
- **WARNING**: Warning messages (rate limit approaching, retries)
- **ERROR**: Error messages that don't stop execution
- **CRITICAL**: Errors that cause execution to stop

### 3.2 Log Format
- Use structured logging (JSON format recommended for production)
- Include timestamp, log level, component name, and message
- Include relevant context (API endpoint, record IDs, error codes)

### 3.3 Log Output
- Write logs to stdout/stderr for Docker compatibility
- Avoid writing to files unless necessary (use log rotation if needed)
- Consider integration with Cloud Logging for GCP deployments

## 4. Data Integrity Rules

### 4.1 Deduplication
- Identify unique key for each record (typically record ID or composite key)
- Implement idempotent operations (safe to re-run)
- Track last processed offset/page to resume from failures

### 4.2 Data Validation
- Validate API response structure before processing
- Check for required fields before BigQuery insertion
- Handle missing or null values appropriately
- Log validation failures with record details
- Automatically detect new fields in API responses
- Add new fields to BigQuery schema automatically (for supported field types)

### 4.3 Data Consistency
- Use transactions or batch operations where possible
- Verify record counts match API responses
- Implement data quality checks (e.g., date ranges, value constraints)

## 5. Code Quality Rules

### 5.1 Code Style
- Follow PEP 8 Python style guide
- Use type hints for function parameters and return values
- Maximum line length: 100 characters
- Use meaningful variable and function names

### 5.2 Documentation
- Document all public functions and classes with docstrings
- Use Google-style docstrings
- Include parameter descriptions, return values, and exceptions
- Document complex logic with inline comments

### 5.3 Testing
- Write unit tests for critical functions (API client, data transformation)
- Aim for >80% code coverage for core logic
- Test error handling paths
- Use mocking for external dependencies (API, BigQuery)

## 6. Configuration Rules

### 6.1 Configuration Management
- Use environment variables for all configuration
- Provide sensible defaults where possible
- Validate configuration on application startup
- Fail fast on invalid configuration

### 6.2 Environment Variables
- Prefix environment variables with `TC_` (e.g., `TC_API_KEY`)
- Document all required and optional environment variables
- Use `.env.example` as template
- Never include actual secrets in example files

### 6.3 No Hardcoded Data
- **NEVER** hardcode data values directly into program code
- All data must be stored in and obtained from configuration files, environment variables, or external data sources
- This includes but is not limited to:
  - API endpoints and URLs
  - API keys and credentials
  - Faction IDs, table names, dataset names
  - Timeout values, retry counts, batch sizes
  - Default values, constants, and configuration parameters
- Use configuration files (e.g., `config/TC_API_config.json`) for structured data
- Use environment variables for sensitive or environment-specific values
- Code should only contain logic and references to configuration sources, never actual data values
- Exceptions: Only language-level constants (e.g., `None`, `True`, `False`) and mathematical constants (e.g., `math.pi`) are acceptable

## 7. Performance Rules

### 7.1 API Usage
- Respect rate limits (Torn City API limit: 100 requests/minute)
- Default configuration uses 60 requests/minute for safety margin
- Implement request throttling via RateLimiter class
- Use appropriate delays between requests
- Batch API calls when possible

### 7.2 BigQuery Operations
- Use batch inserts (not individual row inserts)
- Consider streaming inserts for real-time data
- Use appropriate batch sizes (1000-10000 rows)
- Monitor BigQuery quota usage

### 7.3 Resource Usage
- Monitor memory usage (especially for large datasets)
- Implement pagination for large result sets
- Clean up temporary resources
- Set appropriate timeouts for network operations

## 8. Monitoring Rules

### 8.1 Metrics to Track
- Number of records processed per run
- API request count and rate limit usage
- BigQuery insertion success/failure rates
- Execution time per run
- Error rates by type

### 8.2 Health Checks
- Implement health check mechanism (file or endpoint)
- Monitor container health
- Alert on consecutive failures
- Track uptime and availability

## 9. Deployment Rules

### 9.1 Docker
- Use multi-stage builds for smaller images
- Pin dependency versions in requirements.txt
- Use specific Python version (Python 3.13 - latest stable, not `latest` tag)
- Minimize image size (use slim base images)
- Set appropriate resource limits

### 9.2 Scheduling
- Use cron syntax for scheduling
- Document timezone assumptions
- Handle overlapping runs (prevent concurrent execution)
- Implement lock mechanism if needed

## 10. Change Management

### 10.1 Version Control
- Use meaningful commit messages
- Create feature branches for changes
- Review code before merging
- Tag releases with version numbers

### 10.2 Documentation Updates
- Update README when adding features
- Document breaking changes
- Keep configuration examples up to date
- Update API documentation if response format changes

### 10.3 Automatic Commits
- **Automatic commits should occur when:**
  - Significant changes have been made (new files, modified source code, configuration changes)
  - It has been more than 24 hours since the last commit (to prevent loss of work)
- **Significant changes include:**
  - New or modified files in `src/`, `tests/`, `config/`, or root directory
  - Changes to `requirements.txt`, `Dockerfile`, `docker-compose.yml`
  - Documentation updates (`.md` files)
  - Configuration file changes (excluding credentials)
- **Automatic commits should:**
  - Stage all changes (`git add -A`)
  - Generate meaningful commit messages based on changed files
  - Never commit sensitive files (credentials, API keys, `.env` files)
  - Skip commits if working directory is clean
  - Skip commits if only log files or temporary files have changed
- **Implementation:**
  - Use `auto_commit.py` script to check and commit changes
  - Can be run manually or scheduled via cron
  - Script checks time since last commit and significance of changes

## 11. Compliance

### 11.1 Data Privacy
- Follow Torn City API terms of service
- Respect data retention policies
- Implement data deletion if required
- Document data handling procedures

### 11.2 API Terms
- Comply with Torn City API rate limits
- Follow API usage guidelines
- Don't abuse or overload the API
- Monitor API usage patterns


