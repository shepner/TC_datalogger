#!/usr/bin/env python3
"""
Load all historical records from API into BigQuery (bypassing time window filter).

This utility script fetches ALL records from the configured endpoint without
applying any time window filters. Useful for:
- Initial data loading
- Backfilling historical data
- Re-syncing after data loss

The script uses the storage mode configured in the endpoint (append or replace).

Usage:
    python load_all_historical.py
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.api_client import TornCityAPIClient
from src.bigquery_loader import BigQueryLoader
from src.config import Config
from src.data_processor import DataProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Load all historical records."""
    config = Config("config/TC_API_config.json")
    
    # Get the crimes endpoint
    endpoint = config.get_endpoint("v2_faction_40832_crimes")
    if not endpoint:
        logger.error("Endpoint v2_faction_40832_crimes not found in config")
        return
    
    table_id = endpoint.get("table")
    endpoint_url = endpoint.get("url", "")
    base_url = config.get_api_base_url()
    endpoint_path = endpoint_url.replace(base_url, "")
    storage_mode = endpoint.get("storage_mode", "append")
    
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
    
    # Create BigQuery loader
    credentials_path = config.get_gcp_credentials_path()
    project_id = config.get_gcp_project_id()
    dataset_id = config.get_gcp_dataset_id()
    allowed_tables = config.get_gcp_allowed_pre_existing_tables()
    bigquery_loader = BigQueryLoader(
        credentials_path, project_id, dataset_id, allowed_pre_existing_tables=allowed_tables
    )
    
    # Load schema
    schema_path = "config/oc_records_schema.json"
    schema = bigquery_loader.load_schema(schema_path)
    
    print("\n" + "="*70)
    print("LOADING ALL HISTORICAL RECORDS (bypassing time window)")
    print("="*70)
    print(f"\nEndpoint: {endpoint.get('name')}")
    print(f"API URL: {endpoint_url}")
    print(f"BigQuery Table: {table_id}")
    print(f"Storage Mode: {storage_mode}")
    print("\n" + "-"*70)
    
    # Fetch ALL records (no time window filter)
    print("\n📡 Fetching ALL records from API (no time window)...")
    records = api_client.fetch_all(endpoint_path, params={})  # Empty params = no time filter
    print(f"✅ Fetched {len(records):,} records from API")
    
    if not records:
        print("⚠️  No records to load")
        return
    
    # Process records
    print("\n🔄 Processing records...")
    processor = DataProcessor()
    processed_records = processor.process_records(records)
    print(f"✅ Processed {len(processed_records):,} records")
    
    # Load to BigQuery
    print(f"\n📊 Loading {len(processed_records):,} records to BigQuery...")
    try:
        if storage_mode == "replace":
            bigquery_loader.load_data_replace(table_id, processed_records, schema)
            print("✅ Data loaded (replace mode)")
        else:
            result = bigquery_loader.load_data_append_merge(
                table_id, processed_records, schema, deduplication_key="id"
            )
            print(f"✅ Data loaded: {result.get('inserted', 0):,} inserted, "
                  f"{result.get('updated', 0):,} updated")
    except Exception as e:
        logger.error(f"Error loading to BigQuery: {e}", exc_info=True)
        return
    
    print("\n" + "="*70)
    print("✅ COMPLETE: All historical records loaded!")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

