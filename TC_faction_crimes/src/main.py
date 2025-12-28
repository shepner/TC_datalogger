"""Main entry point for Torn City API to BigQuery pipeline."""

import argparse
import logging
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from src.api_client import TornCityAPIClient
from src.bigquery_loader import BigQueryLoader
from src.config import Config
from src.data_processor import DataProcessor
from src.scheduler import Scheduler, parse_iso8601_duration

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class Pipeline:
    """Main pipeline class that orchestrates the ETL process."""

    @staticmethod
    def _extract_schema_field_names(field: Any, prefix: str = "") -> set:
        """
        Recursively extract all field names from a BigQuery schema field.
        
        Args:
            field: BigQuery SchemaField object
            prefix: Prefix for nested field names
            
        Returns:
            Set of field names
        """
        from google.cloud import bigquery
        
        field_names = set()
        field_name = f"{prefix}.{field.name}" if prefix else field.name
        field_names.add(field_name)
        
        # Handle nested RECORD fields
        if hasattr(field, 'fields') and field.fields:
            for nested_field in field.fields:
                nested_names = Pipeline._extract_schema_field_names(nested_field, field_name)
                field_names.update(nested_names)
        
        return field_names

    def __init__(self, config: Config, endpoint_name: Optional[str] = None):
        """
        Initialize pipeline.

        Args:
            config: Configuration object
            endpoint_name: Optional endpoint name to process. If None, processes all endpoints.
        """
        self.config = config
        self.endpoint_name = endpoint_name

        # Initialize BigQuery loader
        credentials_path = config.get_gcp_credentials_path()
        project_id = config.get_gcp_project_id()
        dataset_id = config.get_gcp_dataset_id()
        allowed_tables = config.get_gcp_allowed_pre_existing_tables()
        self.bigquery_loader = BigQueryLoader(
            credentials_path, project_id, dataset_id, allowed_pre_existing_tables=allowed_tables
        )

        # Load schema
        project_root = Path(__file__).parent.parent
        schema_path = project_root / "config" / "oc_records_schema.json"
        self.schema = self.bigquery_loader.load_schema(str(schema_path))

    def process_endpoint(self, endpoint: dict) -> None:
        """
        Process a single endpoint.

        Args:
            endpoint: Endpoint configuration dictionary
        """
        endpoint_name = endpoint.get("name", "unknown")
        logger.info(f"Processing endpoint: {endpoint_name}")

        try:
            # Get API key
            api_key_name = endpoint.get("api_key", "")
            if not api_key_name:
                logger.error(f"Endpoint {endpoint_name} missing api_key")
                return

            api_key = self.config.get_api_key(api_key_name)
            if not api_key:
                logger.warning(
                    f"API key '{api_key_name}' is empty for endpoint {endpoint_name}"
                )
                return

            # Initialize API client
            rate_limit = self.config.get_rate_limit(endpoint)
            timeout = self.config.get_timeout(endpoint)
            max_retries = self.config.get_max_retries(endpoint)
            retry_delay = self.config.get_retry_delay(endpoint)
            base_url = self.config.get_api_base_url()

            api_client = TornCityAPIClient(
                api_key=api_key,
                rate_limit=rate_limit,
                timeout=timeout,
                max_retries=max_retries,
                retry_delay=retry_delay,
                base_url=base_url,
            )

            # Fetch data
            endpoint_url = endpoint.get("url", "")
            logger.info(f"Fetching data from {endpoint_url}")

            # Parse URL to get endpoint path
            # URL format: https://api.torn.com/v2/faction/crimes?params
            base_url = self.config.get_api_base_url()
            if "?" in endpoint_url:
                endpoint_path = endpoint_url.split("?")[0].replace(base_url, "")
                # Extract query params if any
                params_str = endpoint_url.split("?")[1]
                params = dict(urllib.parse.parse_qsl(params_str))
            else:
                endpoint_path = endpoint_url.replace(base_url, "")
                params = {}

            # Handle time windows if configured
            # This is useful for incremental fetching to avoid re-fetching all historical data
            use_time_windows = endpoint.get("use_time_windows", False)
            if use_time_windows:
                # Calculate time window based on frequency
                # For 15-minute frequency, fetch records from last 20 minutes (with buffer)
                frequency_str = endpoint.get("frequency", "PT15M")
                interval_seconds = parse_iso8601_duration(frequency_str)
                # Add 50% buffer to ensure we don't miss records
                window_seconds = int(interval_seconds * 1.5)
                window_start = int(time.time()) - window_seconds
                
                # Add timestamp parameters if API supports them
                # Note: Torn City API may use different parameter names
                # Common names: from, from_timestamp, start, start_time
                # For now, we'll add 'from' parameter as a common convention
                params["from"] = window_start
                logger.info(
                    f"Using time window: fetching records from last {window_seconds} seconds "
                    f"(from timestamp: {window_start})"
                )

            records = api_client.fetch_all(endpoint_path, params=params)
            logger.info(f"Fetched {len(records)} records from API")

            # Get table info before processing
            table_id = endpoint.get("table", "")
            storage_mode = endpoint.get("storage_mode", "replace")

            # Ensure table exists even if there are no records
            # This allows the table to be created in BigQuery for future data
            if not records:
                logger.warning(f"No records fetched for {endpoint_name}")
                # Still ensure table exists so it's ready for when data arrives
                try:
                    self.bigquery_loader.ensure_table_exists(table_id, self.schema)
                    logger.info(f"Table {table_id} ensured (ready for data)")
                except ValueError as e:
                    # Table exists but has incompatible schema - skip
                    logger.warning(f"Skipping: {e}")
                except Exception as e:
                    logger.error(f"Error ensuring table exists: {e}")
                return

            # Process data
            processor = DataProcessor()
            
            # Extract schema field names for new field detection
            schema_field_names = set()
            for field in self.schema:
                field_names = self._extract_schema_field_names(field)
                schema_field_names.update(field_names)
            
            processed_records = processor.process_records(
                records, 
                known_schema_fields=schema_field_names
            )
            logger.info(
                f"Processed {len(processed_records)} records for BigQuery"
            )

            if not processed_records:
                logger.warning(f"No processed records for {endpoint_name}")
                # Still ensure table exists
                try:
                    self.bigquery_loader.ensure_table_exists(table_id, self.schema)
                    logger.info(f"Table {table_id} ensured (ready for data)")
                except ValueError as e:
                    logger.warning(f"Skipping: {e}")
                except Exception as e:
                    logger.error(f"Error ensuring table exists: {e}")
                return

            # Load to BigQuery
            logger.info(
                f"Loading {len(processed_records)} records to {table_id} "
                f"(mode: {storage_mode})"
            )

            result = self.bigquery_loader.load_data(
                table_id=table_id,
                records=processed_records,
                schema=self.schema,
                storage_mode=storage_mode,
                deduplication_key="id",
            )

            if result:
                # Log load statistics
                if 'inserted' in result or 'updated' in result:
                    logger.info(
                        f"Load completed: {result.get('inserted', 0)} inserted, "
                        f"{result.get('updated', 0)} updated "
                        f"(total: {result.get('total', 0)} processed)"
                    )
                
                # Log new field capture verification
                if result.get('new_fields'):
                    logger.info("=" * 80)
                    logger.info("📋 NEW FIELD CAPTURE REPORT")
                    logger.info("=" * 80)
                    logger.info(f"Endpoint: {endpoint_name}")
                    logger.info(f"Table: {table_id}")
                    logger.info(f"New fields detected: {result.get('new_fields', [])}")
                    logger.info(f"Records with new fields: {result.get('records_with_new_fields', 0)}")
                    
                    if result.get('fields_added'):
                        logger.info(f"✅ Fields added to schema: {result.get('fields_added', [])}")
                    
                    if result.get('verification'):
                        verification = result.get('verification', {})
                        verified_fields = [f for f, exists in verification.items() if exists]
                        if verified_fields:
                            logger.info(f"✅ Verified in database: {verified_fields}")
                        missing_fields = [f for f, exists in verification.items() if not exists]
                        if missing_fields:
                            logger.warning(f"⚠️  Not found in database: {missing_fields}")
                    
                    if result.get('all_fields_verified'):
                        logger.info("✅ SUCCESS: All new fields are present in the database!")
                    else:
                        logger.warning("⚠️  WARNING: Some new fields may not be present in the database")
                    
                    logger.info("=" * 80)
            else:
                logger.info("Load completed (replace mode)")
            
            # Get and log current BigQuery table statistics
            logger.info("-" * 80)
            logger.info("📊 BIGQUERY TABLE STATISTICS")
            logger.info("-" * 80)
            table_stats = self.bigquery_loader.get_table_record_count(table_id)
            if table_stats:
                logger.info(f"Table: {table_id}")
                logger.info(f"   Total records: {table_stats.get('total_records', 0):,}")
                logger.info(f"   Unique IDs: {table_stats.get('unique_ids', 0):,}")
                if table_stats.get('oldest_record'):
                    logger.info(f"   Oldest record: {table_stats.get('oldest_record')}")
                if table_stats.get('newest_record'):
                    logger.info(f"   Newest record: {table_stats.get('newest_record')}")
            else:
                logger.warning(f"Could not retrieve statistics for table {table_id}")
            logger.info("-" * 80)

            logger.info(f"Successfully processed endpoint: {endpoint_name}")

        except ValueError as e:
            # Schema mismatch - skip this endpoint, don't modify pre-existing tables
            logger.warning(
                f"Skipping endpoint {endpoint_name}: {e}"
            )
            return
        except Exception as e:
            logger.error(
                f"Error processing endpoint {endpoint_name}: {e}", exc_info=True
            )
            raise

    def run(self) -> None:
        """Run the pipeline for configured endpoints."""
        endpoints = self.config.get_endpoints()

        if self.endpoint_name:
            # Process specific endpoint
            endpoint = self.config.get_endpoint(self.endpoint_name)
            if not endpoint:
                logger.error(f"Endpoint '{self.endpoint_name}' not found")
                sys.exit(1)
            self.process_endpoint(endpoint)
        else:
            # Process all endpoints
            logger.info(f"Processing {len(endpoints)} endpoints")
            for endpoint in endpoints:
                try:
                    self.process_endpoint(endpoint)
                except Exception as e:
                    logger.error(
                        f"Failed to process endpoint {endpoint.get('name', 'unknown')}: {e}",
                        exc_info=True,
                    )
                    # Continue with other endpoints
                    continue


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Torn City API to BigQuery pipeline"
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        help="Process specific endpoint by name",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run in scheduled mode (continuous)",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to configuration file",
    )

    args = parser.parse_args()

    try:
        # Load configuration
        config = Config(args.config)

        # Validate configuration
        try:
            # Check that credentials file exists
            credentials_path = config.get_gcp_credentials_path()
            if not Path(credentials_path).exists():
                logger.error(
                    f"GCP credentials file not found: {credentials_path}\n"
                    "Please ensure credentials.json exists or set TC_GCP_CREDENTIALS_PATH"
                )
                sys.exit(1)

            # Check that project and dataset are configured
            project_id = config.get_gcp_project_id()
            dataset_id = config.get_gcp_dataset_id()
            logger.debug(f"Using GCP project: {project_id}, dataset: {dataset_id}")

            # Check that at least one endpoint is configured
            endpoints = config.get_endpoints()
            if not endpoints:
                logger.error("No endpoints configured in config file")
                sys.exit(1)
            logger.info(f"Found {len(endpoints)} configured endpoints")

        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            sys.exit(1)

        # Create pipeline
        pipeline = Pipeline(config, endpoint_name=args.endpoint)

        if args.schedule:
            # Run in scheduled mode
            # Get frequency from first endpoint (assuming all have same frequency)
            endpoints = config.get_endpoints()
            if not endpoints:
                logger.error("No endpoints configured")
                sys.exit(1)

            frequency_str = endpoints[0].get("frequency", "PT15M")
            interval_seconds = parse_iso8601_duration(frequency_str)
            timezone = config.get_timezone()

            scheduler = Scheduler(
                interval_seconds=interval_seconds,
                timezone=timezone,
                function=pipeline.run,
            )

            logger.info(
                f"Starting scheduler: interval={interval_seconds}s, "
                f"timezone={timezone}"
            )
            scheduler.run_forever()
        else:
            # Run once
            pipeline.run()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

