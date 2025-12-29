# SQL Queries for Torn City Data Logger

This directory contains SQL queries for analyzing data stored in BigQuery tables from the Torn City API Data Logger project.

## Tables Available

All queries use tables in the `torncity-402423.torn_data` dataset:

- **`v2_faction_40832_crimes-raw`** - Organized crime records (append mode)
- **`v2_faction_40832_members-raw`** - Faction member data (replace mode)
- **`v2_torn_items-raw`** - Items catalog (replace mode)
- **`v2_torn_user_events-raw`** - User events (append mode)

## Usage

### Testing Queries Locally

You can test SQL queries locally using the provided test script:

1. **Copy your GCP service account credentials** to:
   ```
   sql_queries/config/credentials.json
   ```

2. **Install required Python packages** (if not already installed):
   ```bash
   pip install google-cloud-bigquery
   ```

3. **Run the test script**:
   ```bash
   python sql_queries/test_query.py items_id_name_market_price.sql
   ```

The script will execute the query and display the first 20 rows of results.

### Running Queries in BigQuery Console

1. Open [BigQuery Console](https://console.cloud.google.com/bigquery)
2. Select project: `torncity-402423`
3. Open any `.sql` file from this directory
4. Copy and paste the query into BigQuery SQL editor
5. Execute the query

## Notes

- All queries use standard SQL (not legacy SQL)
- Table names use full path: `torncity-402423.torn_data.table_name`
- Queries handle nested/repeated fields appropriately
- Each query is standalone and executable
- Queries include comments explaining purpose and usage

## Adding New Queries

1. Create a new `.sql` file with a descriptive filename
2. Include comments explaining the query purpose
3. Add the query to `TASK_LIST.md` and mark as completed
4. Test the query in BigQuery console before committing
