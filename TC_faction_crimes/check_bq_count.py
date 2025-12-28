#!/usr/bin/env python3
"""
Quick script to check record count in BigQuery table.

This utility script queries the BigQuery table configured in the first endpoint
and displays:
- Total record count
- Unique ID count
- Oldest record timestamp
- Newest record timestamp

Usage:
    python check_bq_count.py
"""

import json
import sys
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

# Load config
config_path = Path(__file__).parent / "config" / "TC_API_config.json"
with open(config_path, "r") as f:
    config = json.load(f)

# Get GCP config
gcp_config = config.get("gcp", {})
credentials_path = gcp_config.get("credentials_path", "config/credentials.json")
project_id = gcp_config.get("project_id")
dataset_id = gcp_config.get("dataset_id")

# Get table from endpoint config
endpoints = config.get("endpoints", [])
if not endpoints:
    print("No endpoints configured")
    sys.exit(1)

table_id = endpoints[0].get("table", "")
if not table_id:
    print("No table configured in endpoint")
    sys.exit(1)

print(f"Querying BigQuery table: {table_id}")

# Load credentials and create BigQuery client
credentials = service_account.Credentials.from_service_account_file(
    str(Path(__file__).parent / credentials_path),
    scopes=["https://www.googleapis.com/auth/bigquery"],
)
client = bigquery.Client(credentials=credentials, project=project_id)

# Query for record count
query = f"SELECT COUNT(*) as record_count FROM `{table_id}`"

try:
    query_job = client.query(query)
    results = query_job.result()
    
    for row in results:
        count = row.record_count
        print(f"\n✅ Total records in BigQuery: {count:,}")
        
        # Also get some additional stats
        stats_query = f"""
        SELECT 
            COUNT(DISTINCT id) as unique_ids,
            MIN(fetched_at) as oldest_record,
            MAX(fetched_at) as newest_record
        FROM `{table_id}`
        """
        stats_job = client.query(stats_query)
        stats_results = stats_job.result()
        
        for stat_row in stats_results:
            print(f"   - Unique IDs: {stat_row.unique_ids:,}")
            if stat_row.oldest_record:
                print(f"   - Oldest record: {stat_row.oldest_record}")
            if stat_row.newest_record:
                print(f"   - Newest record: {stat_row.newest_record}")
        
except Exception as e:
    print(f"❌ Error querying BigQuery: {e}")
    sys.exit(1)

