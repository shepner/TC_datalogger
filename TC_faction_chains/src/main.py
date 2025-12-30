"""Main entry point for Torn City Faction Chains to BigQuery pipeline."""

import argparse
import logging
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    """Main pipeline class that orchestrates the ETL process for chains."""

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

        # For chains, we'll auto-generate schema from first response
        # No predefined schema file needed
        self.schema = []

    def _fetch_chain_ids(self, api_client: TornCityAPIClient) -> List[int]:
        """
        Fetch list of chain IDs from /v2/faction/chains endpoint with pagination.
        
        Uses timestamp-based pagination (backwards via 'to' parameter) since chains
        API uses time-based pagination rather than offset-based.
        
        Args:
            api_client: Torn City API client
            
        Returns:
            List of chain IDs
        """
        logger.info("Fetching chain IDs from /v2/faction/chains (with timestamp pagination)")
        endpoint_path = "/v2/faction/chains"
        
        chain_ids = []
        seen_ids = set()
        current_to_timestamp = None  # For timestamp-based pagination
        
        while True:
            params = {}
            if current_to_timestamp:
                params["to"] = current_to_timestamp
            
            try:
                logger.debug(f"Fetching chains page (to={current_to_timestamp})")
                response = api_client._make_request(
                    f"{api_client.base_url}{endpoint_path}",
                    params
                )
                
                if not isinstance(response, dict) or "chains" not in response:
                    logger.warning(f"Unexpected response format: {response}")
                    break
                
                chains_list = response.get("chains", [])
                if not chains_list:
                    logger.info("No more chains in this page")
                    break
                
                # Extract chain IDs from this page
                page_chain_ids = []
                oldest_timestamp = None
                
                for chain_obj in chains_list:
                    if isinstance(chain_obj, dict) and "id" in chain_obj:
                        chain_id = chain_obj["id"]
                        if chain_id not in seen_ids:
                            chain_ids.append(chain_id)
                            seen_ids.add(chain_id)
                            page_chain_ids.append(chain_id)
                        
                        # Track oldest timestamp for next page (use 'end' timestamp)
                        if "end" in chain_obj:
                            end_ts = chain_obj["end"]
                            if oldest_timestamp is None or end_ts < oldest_timestamp:
                                oldest_timestamp = end_ts
                
                logger.info(f"Page: {len(page_chain_ids)} new chains (total so far: {len(chain_ids)})")
                
                # Check metadata for pagination
                # Chains API paginates backwards - use oldest timestamp from current page
                metadata = response.get("_metadata", {})
                links = metadata.get("links", {})
                prev_url = links.get("prev")  # Check if prev link exists
                
                # Use the oldest timestamp from current page to fetch next (older) page
                if oldest_timestamp:
                    # Set timestamp for next page (go backwards in time)
                    new_to_timestamp = oldest_timestamp
                    
                    # Safety check: don't go backwards if we're already at or past this timestamp
                    if current_to_timestamp and new_to_timestamp >= current_to_timestamp:
                        logger.info("Timestamp not progressing backwards - stopping")
                        break
                    
                    current_to_timestamp = new_to_timestamp
                    logger.debug(f"Next page will use timestamp: {current_to_timestamp}")
                elif prev_url:
                    # Fallback: try to extract from prev URL
                    if "to=" in prev_url:
                        try:
                            to_param = prev_url.split("to=")[1].split("&")[0].split("?")[0]
                            current_to_timestamp = int(to_param)
                            logger.debug(f"Using timestamp from prev URL: {current_to_timestamp}")
                        except (ValueError, IndexError) as e:
                            logger.warning(f"Could not parse 'to' parameter from prev URL: {e}")
                            break
                    else:
                        logger.info("No timestamp available for next page - stopping")
                        break
                else:
                    # No more pages
                    logger.info("No more pages available (no prev link and no oldest timestamp)")
                    break
                
                if len(page_chain_ids) == 0:
                    logger.info("No new chains in this page - stopping")
                    break
                
            except Exception as e:
                logger.error(f"Error fetching chain IDs page: {e}", exc_info=True)
                break
        
        # Ensure all IDs are integers
        try:
            chain_ids = [int(cid) for cid in chain_ids if cid is not None]
        except (ValueError, TypeError) as e:
            logger.warning(f"Error converting chain IDs to integers: {e}")
            chain_ids = []
        
        logger.info(f"Found {len(chain_ids)} unique chain IDs (across all pages)")
        return chain_ids

    def _fetch_chain_report(
        self, api_client: TornCityAPIClient, chain_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch detailed chain report for a specific chain ID.
        
        Args:
            api_client: Torn City API client
            chain_id: Chain ID to fetch report for
            
        Returns:
            Chain report dictionary or None if fetch fails
        """
        # Use v2 endpoint: /v2/faction/{chainId}/chainreport
        endpoint_path = f"/v2/faction/{chain_id}/chainreport"
        logger.debug(f"Fetching chain report for chain ID {chain_id}")
        
        try:
            response = api_client._make_request(
                f"{api_client.base_url}{endpoint_path}",
                params={}
            )
            
            # Response format: {"chainreport": {...}}
            # Extract the chainreport data
            if isinstance(response, dict):
                if "chainreport" in response:
                    report = response["chainreport"]
                    # Ensure it's a dict
                    if not isinstance(report, dict):
                        report = {"data": report}
                else:
                    report = response
                
                # Add chain_id to the response for tracking
                report["chain_id"] = chain_id
                # Ensure there's an 'id' field for deduplication
                if "id" not in report:
                    report["id"] = chain_id
                
                return report
            else:
                # If response is not a dict, wrap it
                return {
                    "id": chain_id,
                    "chain_id": chain_id,
                    "data": response
                }
        except Exception as e:
            logger.warning(f"Failed to fetch chain report for chain ID {chain_id}: {e}")
            return None

    def process_endpoint(self, endpoint: dict) -> None:
        """
        Process a single endpoint (chains collection).

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

            # Step 1: Fetch chain IDs
            chain_ids = self._fetch_chain_ids(api_client)
            
            if not chain_ids:
                logger.warning("No chain IDs found")
                return

            # Step 2: Fetch chain reports for each chain ID
            logger.info(f"Fetching chain reports for {len(chain_ids)} chains")
            chain_reports = []
            
            for chain_id in chain_ids:
                report = self._fetch_chain_report(api_client, chain_id)
                if report:
                    chain_reports.append(report)
                # Small delay between requests to respect rate limits
                time.sleep(0.1)

            logger.info(f"Fetched {len(chain_reports)} chain reports from API")

            # Get table info
            table_id = endpoint.get("table", "")
            storage_mode = endpoint.get("storage_mode", "append")

            if not chain_reports:
                logger.warning(f"No chain reports fetched for {endpoint_name}")
                return

            # Process data - convert complex nested structures to JSON strings
            import json
            processed_reports = []
            for report in chain_reports:
                processed = {}
                for key, value in report.items():
                    if isinstance(value, (dict, list)):
                        # Convert complex nested structures to JSON strings
                        processed[key] = json.dumps(value)
                    else:
                        processed[key] = value
                processed_reports.append(processed)
            
            processor = DataProcessor()
            
            # If we don't have a schema yet, we'll let BigQuery auto-detect
            # Otherwise, extract schema field names for new field detection
            schema_field_names = set()
            if self.schema:
                for field in self.schema:
                    field_names = self._extract_schema_field_names(field)
                    schema_field_names.update(field_names)
            
            processed_records = processor.process_records(
                processed_reports, 
                known_schema_fields=schema_field_names if schema_field_names else None
            )
            logger.info(
                f"Processed {len(processed_records)} records for BigQuery"
            )

            if not processed_records:
                logger.warning(f"No processed records for {endpoint_name}")
                return

            # Auto-generate schema from first record if we don't have one
            if not self.schema:
                logger.info("Auto-generating schema from first record")
                from google.cloud import bigquery
                
                # Create a simple schema from the first record
                # BigQuery will handle type inference
                sample_record = processed_records[0]
                schema_fields = []
                
                for key, value in sample_record.items():
                    if key == "fetched_at":
                        field = bigquery.SchemaField(key, "TIMESTAMP", mode="NULLABLE")
                    elif isinstance(value, bool):
                        field = bigquery.SchemaField(key, "BOOLEAN", mode="NULLABLE")
                    elif isinstance(value, int):
                        field = bigquery.SchemaField(key, "INTEGER", mode="NULLABLE")
                    elif isinstance(value, float):
                        field = bigquery.SchemaField(key, "FLOAT", mode="NULLABLE")
                    elif isinstance(value, dict):
                        # For complex nested structures, store as JSON string
                        # BigQuery can parse JSON later if needed
                        field = bigquery.SchemaField(key, "STRING", mode="NULLABLE")
                    elif isinstance(value, list):
                        if value and isinstance(value[0], dict):
                            # For lists of dicts, store as JSON string
                            field = bigquery.SchemaField(key, "STRING", mode="NULLABLE")
                        else:
                            field = bigquery.SchemaField(key, "STRING", mode="REPEATED")
                    else:
                        field = bigquery.SchemaField(key, "STRING", mode="NULLABLE")
                    schema_fields.append(field)
                
                # Ensure 'id' field exists and is REQUIRED
                id_field_idx = None
                for i, field in enumerate(schema_fields):
                    if field.name == "id":
                        id_field_idx = i
                        break
                
                if id_field_idx is not None:
                    # Replace with REQUIRED version
                    old_field = schema_fields[id_field_idx]
                    schema_fields[id_field_idx] = bigquery.SchemaField(
                        old_field.name, old_field.field_type, mode="REQUIRED"
                    )
                else:
                    # Add id field if missing
                    schema_fields.insert(0, bigquery.SchemaField("id", "INTEGER", mode="REQUIRED"))
                
                self.schema = schema_fields
                logger.info(f"Generated schema with {len(schema_fields)} fields")

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
        description="Torn City Faction Chains to BigQuery pipeline"
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

            frequency_str = endpoints[0].get("frequency", "PT1H")
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

