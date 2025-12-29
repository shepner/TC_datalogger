#!/usr/bin/env python3
"""
Test script to execute SQL queries against BigQuery.

This script allows you to test SQL queries in the sql_queries directory
by executing them against the BigQuery tables.

Usage:
    python test_query.py items_id_name_market_price.sql
    python test_query.py <query_file.sql>
"""

import json
import sys
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

# Default configuration
DEFAULT_PROJECT_ID = "torncity-402423"
DEFAULT_DATASET_ID = "torn_data"
DEFAULT_CREDENTIALS_PATH = Path(__file__).parent / "config" / "credentials.json"


def load_credentials(credentials_path: Path):
    """Load GCP service account credentials."""
    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {credentials_path}\n"
            f"Please copy your GCP service account JSON file to: {credentials_path}"
        )
    
    return service_account.Credentials.from_service_account_file(
        str(credentials_path),
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )


def execute_query(query_file: Path, project_id: str, dataset_id: str, credentials_path: Path):
    """Execute a SQL query file against BigQuery."""
    if not query_file.exists():
        raise FileNotFoundError(f"Query file not found: {query_file}")
    
    # Read the SQL query
    with open(query_file, "r") as f:
        query = f.read()
    
    print(f"📄 Executing query from: {query_file.name}")
    print(f"📊 Project: {project_id}, Dataset: {dataset_id}\n")
    
    # Load credentials and create BigQuery client
    credentials = load_credentials(credentials_path)
    client = bigquery.Client(credentials=credentials, project=project_id)
    
    # Execute query
    try:
        query_job = client.query(query)
        results = query_job.result()
        
        # Get column names from schema
        if query_job.schema:
            columns = [field.name for field in query_job.schema]
        else:
            # If schema is not available, try to infer from first row
            first_row = next(iter(results), None)
            if first_row:
                columns = list(first_row.keys())
                # Reset results iterator
                results = query_job.result()
            else:
                print("⚠️  Query returned no results")
                return
        
        # Print header
        print("=" * 80)
        print("Results:")
        print("=" * 80)
        
        # Print column headers
        header = " | ".join(f"{col:20}" for col in columns)
        print(header)
        print("-" * len(header))
        
        # Print rows (limit to first 20 for display)
        row_count = 0
        for row in results:
            values = []
            for col in columns:
                value = getattr(row, col, None)
                if value is None:
                    values.append("NULL")
                elif isinstance(value, (int, float)):
                    values.append(str(value))
                else:
                    values.append(str(value)[:20])  # Truncate long strings
            print(" | ".join(f"{val:20}" for val in values))
            row_count += 1
            if row_count >= 20:
                print("\n... (showing first 20 rows)")
                break
        
        print("=" * 80)
        print(f"✅ Query executed successfully")
        
        # Get total count if we truncated
        if row_count >= 20:
            try:
                # Try to get count by wrapping the query
                count_query = f"SELECT COUNT(*) as total FROM ({query.rstrip().rstrip(';')})"
                count_job = client.query(count_query)
                count_results = count_job.result()
                for count_row in count_results:
                    print(f"📈 Total rows: {count_row.total:,}")
            except Exception as e:
                # If count query fails, just note that more rows exist
                print(f"📈 (showing first 20 rows, more available)")
        
    except Exception as e:
        print(f"❌ Error executing query: {e}")
        sys.exit(1)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python test_query.py <query_file.sql>")
        print("\nExample:")
        print("  python test_query.py items_id_name_market_price.sql")
        sys.exit(1)
    
    query_file = Path(sys.argv[1])
    if not query_file.is_absolute():
        query_file = Path(__file__).parent / query_file
    
    # Check for config file to override defaults
    config_path = Path(__file__).parent / "config" / "config.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            config = json.load(f)
        project_id = config.get("project_id", DEFAULT_PROJECT_ID)
        dataset_id = config.get("dataset_id", DEFAULT_DATASET_ID)
        credentials_path = Path(config.get("credentials_path", str(DEFAULT_CREDENTIALS_PATH)))
    else:
        project_id = DEFAULT_PROJECT_ID
        dataset_id = DEFAULT_DATASET_ID
        credentials_path = DEFAULT_CREDENTIALS_PATH
    
    execute_query(query_file, project_id, dataset_id, credentials_path)


if __name__ == "__main__":
    main()

