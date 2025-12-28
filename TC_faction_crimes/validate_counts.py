#!/usr/bin/env python3
"""
Validate that BigQuery table contents match the API by comparing record counts.

This utility script compares the number of records in BigQuery with the number
of records available from the API to ensure data consistency. Useful for:
- Verifying data completeness
- Detecting missing records
- Identifying duplicate records

Usage:
    python validate_counts.py
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from google.cloud import bigquery
from google.oauth2 import service_account

from src.api_client import TornCityAPIClient
from src.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def count_bigquery_records(credentials_path: str, table_id: str) -> int:
    """Count records in a BigQuery table."""
    try:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        client = bigquery.Client(credentials=credentials)
        
        query = f"SELECT COUNT(*) as count FROM `{table_id}`"
        query_job = client.query(query)
        result = list(query_job.result())[0]
        return result.count
    except Exception as e:
        logger.error(f"Error counting BigQuery records in {table_id}: {e}")
        return -1


def count_api_records(api_client: TornCityAPIClient, endpoint: str, params: dict = None) -> int:
    """Count unique records from API."""
    try:
        records = list(api_client.fetch_all_pages(endpoint, params=params))
        return len(records)
    except Exception as e:
        logger.error(f"Error counting API records from {endpoint}: {e}")
        return -1


def main():
    """Main validation function."""
    config = Config("config/TC_API_config.json")
    
    # Get BigQuery credentials path
    credentials_path = config.get_gcp_credentials_path()
    if not Path(credentials_path).exists():
        logger.error(f"Credentials file not found: {credentials_path}")
        return
    
    # Get the crimes endpoint
    endpoint = config.get_endpoint("v2_faction_40832_crimes")
    if not endpoint:
        logger.error("Endpoint v2_faction_40832_crimes not found in config")
        return
    
    table_id = endpoint.get("table")
    endpoint_url = endpoint.get("url", "")
    base_url = config.get_api_base_url()
    endpoint_path = endpoint_url.replace(base_url, "")
    
    # Get API key
    api_key_name = endpoint.get("api_key")
    api_key = config.get_api_key(api_key_name)
    if not api_key:
        logger.error(f"API key '{api_key_name}' not found or empty")
        return
    
    # Create API client
    api_client = TornCityAPIClient(
        api_key=api_key,
        rate_limit=config.get_rate_limit(endpoint),
        timeout=config.get_timeout(endpoint),
        base_url=base_url,
    )
    
    # Handle time windows if configured
    params = {}
    use_time_windows = endpoint.get("use_time_windows", False)
    if use_time_windows:
        # For validation, we want ALL records, not just recent ones
        # So we'll skip the time window filter
        logger.info("Note: Time window is configured but disabled for full validation")
        params = {}
    
    print("\n" + "="*70)
    print("VALIDATION: Comparing API vs BigQuery Record Counts")
    print("="*70)
    print(f"\nEndpoint: {endpoint.get('name')}")
    print(f"API URL: {endpoint_url}")
    print(f"BigQuery Table: {table_id}")
    print("\n" + "-"*70)
    
    # Count API records
    print("\n📡 Fetching records from API...")
    api_count = count_api_records(api_client, endpoint_path, params=params)
    
    if api_count < 0:
        print("❌ Failed to count API records")
        return
    
    print(f"✅ API Records: {api_count:,}")
    
    # Count BigQuery records
    print("\n📊 Counting records in BigQuery...")
    bq_count = count_bigquery_records(credentials_path, table_id)
    
    if bq_count < 0:
        print("❌ Failed to count BigQuery records")
        return
    
    print(f"✅ BigQuery Records: {bq_count:,}")
    
    # Compare
    print("\n" + "-"*70)
    print("\n📊 COMPARISON RESULTS:")
    print(f"   API Records:      {api_count:,}")
    print(f"   BigQuery Records: {bq_count:,}")
    print(f"   Difference:       {abs(api_count - bq_count):,}")
    
    if api_count == bq_count:
        print("\n✅ SUCCESS: Record counts match!")
    elif bq_count < api_count:
        print(f"\n⚠️  WARNING: BigQuery has {api_count - bq_count:,} fewer records than API")
        print("   This may indicate missing data in BigQuery.")
    else:
        print(f"\n⚠️  WARNING: BigQuery has {bq_count - api_count:,} more records than API")
        print("   This may indicate duplicate data in BigQuery.")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    main()

