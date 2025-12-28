#!/usr/bin/env python3
"""
Script to delete a BigQuery table.

This utility script deletes the BigQuery table configured in the first endpoint.
Use with caution as this operation cannot be undone.

Usage:
    python delete_table.py
"""

import json
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.bigquery_loader import BigQueryLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def delete_table():
    """Delete the BigQuery table."""
    try:
        # Load config
        config_path = Path(__file__).parent / "config" / "TC_API_config.json"
        with open(config_path, "r") as f:
            config = json.load(f)
        
        # Get GCP config
        gcp_config = config.get("gcp", {})
        credentials_path = gcp_config.get("credentials_path", "config/credentials.json")
        project_id = gcp_config.get("project_id")
        dataset_id = gcp_config.get("dataset_id")
        
        # Resolve credentials path relative to project root
        project_root = Path(__file__).parent
        credentials_path = project_root / credentials_path
        
        # Get table name from endpoint config
        endpoints = config.get("endpoints", [])
        if not endpoints:
            logger.error("No endpoints found in config")
            return
        
        table_id = endpoints[0].get("table", "")
        if not table_id:
            logger.error("No table ID found in endpoint config")
            return
        
        logger.info(f"Deleting table: {table_id}")
        
        # Initialize BigQuery loader
        loader = BigQueryLoader(str(credentials_path), project_id, dataset_id)
        
        # Parse table ID
        project_id_parsed, dataset_id_parsed, table_name = loader._parse_table_id(table_id)
        
        # Delete table
        table_ref = loader.client.dataset(dataset_id_parsed).table(table_name)
        try:
            loader.client.delete_table(table_ref, not_found_ok=True)
            logger.info(f"✅ Successfully deleted table: {table_id}")
        except Exception as e:
            logger.error(f"❌ Error deleting table: {e}")
            raise
            
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise


if __name__ == "__main__":
    delete_table()

