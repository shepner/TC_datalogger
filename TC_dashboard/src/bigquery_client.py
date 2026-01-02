"""BigQuery client for executing SQL queries from the dashboard."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.cloud import bigquery
from google.oauth2 import service_account
from datetime import datetime

logger = logging.getLogger(__name__)


class BigQueryClient:
    """Client for executing SQL queries against BigQuery."""

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
    ):
        """
        Initialize BigQuery client.

        Args:
            credentials_path: Path to GCP service account credentials JSON file.
                            If None, will try to find credentials in common locations.
            project_id: GCP project ID. If None, will use environment variable or default.
            dataset_id: BigQuery dataset ID. If None, will use environment variable or default.
        """
        # Get credentials path
        if credentials_path is None:
            # Try environment variable first
            credentials_path = os.getenv("BIGQUERY_CREDENTIALS_PATH")
            if not credentials_path:
                # Try common locations relative to dashboard
                base_path = Path(__file__).parent.parent.parent
                # Try mounted credentials path first (Docker container)
                possible_paths = [
                    Path("/app/config/credentials.json"),  # Docker mounted path
                    base_path / "config" / "credentials.json",  # Local dashboard config
                    base_path / "TC_faction_crimes" / "config" / "credentials.json",
                    base_path / "TC_faction_members" / "config" / "credentials.json",
                    base_path / "TC_items" / "config" / "credentials.json",
                    base_path / "TC_faction_chains" / "config" / "credentials.json",
                ]
                for path in possible_paths:
                    if path.exists():
                        credentials_path = str(path)
                        logger.info(f"Found credentials at: {credentials_path}")
                        break

        if not credentials_path or not Path(credentials_path).exists():
            raise FileNotFoundError(
                f"BigQuery credentials not found. Please set BIGQUERY_CREDENTIALS_PATH "
                f"or place credentials.json in one of the microservice config directories."
            )

        # Get project ID
        if project_id is None:
            project_id = os.getenv("BIGQUERY_PROJECT_ID", "torncity-402423")

        # Get dataset ID
        if dataset_id is None:
            dataset_id = os.getenv("BIGQUERY_DATASET_ID", "torn_data")

        self.project_id = project_id
        self.dataset_id = dataset_id

        # Load credentials and create BigQuery client
        logger.info(f"Initializing BigQuery client: project={project_id}, dataset={dataset_id}")
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        self.client = bigquery.Client(credentials=credentials, project=project_id)

    def execute_query(
        self, query: str, max_results: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute a SQL query and return results as a list of dictionaries.

        Args:
            query: SQL query string
            max_results: Maximum number of results to return (None for all)

        Returns:
            List of dictionaries, where each dictionary represents a row
        """
        try:
            logger.debug(f"Executing query: {query[:200]}...")
            query_job = self.client.query(query)
            results = query_job.result(max_results=max_results)

            # Convert to list of dictionaries
            rows = []
            for row in results:
                row_dict = {}
                for key, value in row.items():
                    row_dict[key] = value
                rows.append(row_dict)

            logger.info(f"Query returned {len(rows)} rows")
            return rows

        except Exception as e:
            logger.error(f"Error executing query: {e}", exc_info=True)
            raise

    def execute_query_file(
        self, query_file_path: str, max_results: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute a SQL query from a file.

        Args:
            query_file_path: Path to SQL query file
            max_results: Maximum number of results to return (None for all)

        Returns:
            List of dictionaries, where each dictionary represents a row
        """
        query_path = Path(query_file_path)
        if not query_path.exists():
            raise FileNotFoundError(f"Query file not found: {query_file_path}")

        with open(query_path, "r") as f:
            query = f.read()

        return self.execute_query(query, max_results=max_results)

    def get_table_info(self, table_name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a BigQuery table.

        Args:
            table_name: Table name (can be just table name or full project.dataset.table)

        Returns:
            Dictionary with table information or None if table doesn't exist
        """
        try:
            # Parse table name
            if "." in table_name:
                # Full table ID provided
                table_id = table_name
            else:
                # Just table name, construct full ID
                table_id = f"{self.project_id}.{self.dataset_id}.{table_name}"

            table = self.client.get_table(table_id)
            return {
                "table_id": table_id,
                "num_rows": table.num_rows,
                "num_bytes": table.num_bytes,
                "created": table.created.isoformat() if table.created else None,
                "modified": table.modified.isoformat() if table.modified else None,
                "num_fields": len(table.schema),
            }
        except Exception as e:
            logger.warning(f"Could not get table info for {table_name}: {e}")
            return None

    def ensure_table_exists(
        self, table_name: str, schema: List[bigquery.SchemaField]
    ) -> None:
        """
        Ensure a BigQuery table exists, creating it if necessary.

        Args:
            table_name: Table name (can be just table name or full project.dataset.table)
            schema: List of SchemaField objects defining the table schema
        """
        try:
            # Parse table name
            if "." in table_name:
                # Full table ID provided
                table_id = table_name
            else:
                # Just table name, construct full ID
                table_id = f"{self.project_id}.{self.dataset_id}.{table_name}"

            # Check if table exists
            try:
                self.client.get_table(table_id)
                logger.info(f"Table {table_id} already exists")
            except Exception:
                # Table doesn't exist, create it
                logger.info(f"Creating table {table_id}")
                table = bigquery.Table(table_id, schema=schema)
                table = self.client.create_table(table)
                logger.info(f"Created table {table_id}")

        except Exception as e:
            logger.error(f"Error ensuring table exists {table_name}: {e}", exc_info=True)
            raise

    def insert_row(self, table_name: str, row: Dict[str, Any]) -> None:
        """
        Insert a single row into a BigQuery table.

        Args:
            table_name: Table name (can be just table name or full project.dataset.table)
            row: Dictionary of column names to values
        """
        try:
            # Parse table name
            if "." in table_name:
                # Full table ID provided
                table_id = table_name
            else:
                # Just table name, construct full ID
                table_id = f"{self.project_id}.{self.dataset_id}.{table_name}"

            # Insert row
            errors = self.client.insert_rows_json(table_id, [row])
            if errors:
                raise Exception(f"Error inserting row: {errors}")
            logger.debug(f"Inserted row into {table_id}")

        except Exception as e:
            logger.error(f"Error inserting row into {table_name}: {e}", exc_info=True)
            raise

    def delete_rows(self, table_name: str, where_clause: str) -> None:
        """
        Delete rows from a BigQuery table.

        Args:
            table_name: Table name (can be just table name or full project.dataset.table)
            where_clause: WHERE clause (without the WHERE keyword)
        """
        try:
            # Parse table name
            if "." in table_name:
                # Full table ID provided
                table_id = table_name
            else:
                # Just table name, construct full ID
                table_id = f"{self.project_id}.{self.dataset_id}.{table_name}"

            # Execute delete query
            query = f"DELETE FROM `{table_id}` WHERE {where_clause}"
            logger.debug(f"Executing delete query: {query}")
            query_job = self.client.query(query)
            query_job.result()  # Wait for completion
            logger.info(f"Deleted rows from {table_id}")

        except Exception as e:
            logger.error(f"Error deleting rows from {table_name}: {e}", exc_info=True)
            raise

    def merge_row(self, table_name: str, row: Dict[str, Any], match_key: str) -> None:
        """
        Insert or update a row in a BigQuery table using MERGE for atomic operations.
        This prevents race conditions by performing check-and-insert atomically.
        
        The MERGE statement ensures that even if multiple requests try to insert
        the same row simultaneously, only one will succeed, preventing duplicates.

        Args:
            table_name: Table name (can be just table name or full project.dataset.table)
            row: Dictionary of column names to values
            match_key: Column name to use for matching (typically the primary key)
        """
        try:
            # Parse table name
            if "." in table_name:
                # Full table ID provided
                table_id = table_name
            else:
                # Just table name, construct full ID
                table_id = f"{self.project_id}.{self.dataset_id}.{table_name}"

            # Verify match_key exists in row
            if match_key not in row:
                raise ValueError(f"Match key '{match_key}' not found in row data")

            # Build column names for MERGE
            columns = list(row.keys())
            
            # Build parameterized query for safety
            query_params = []
            param_placeholders = []
            
            for col in columns:
                val = row[col]
                param_name = f"param_{col}"
                
                # Determine BigQuery type and create parameter
                if val is None:
                    # For NULL values, use NULL directly (no parameter needed)
                    param_placeholders.append(f"NULL AS {col}")
                elif isinstance(val, str):
                    query_params.append(bigquery.ScalarQueryParameter(param_name, "STRING", val))
                    param_placeholders.append(f"@{param_name} AS {col}")
                elif isinstance(val, int):
                    query_params.append(bigquery.ScalarQueryParameter(param_name, "INT64", val))
                    param_placeholders.append(f"@{param_name} AS {col}")
                elif isinstance(val, float):
                    query_params.append(bigquery.ScalarQueryParameter(param_name, "FLOAT64", val))
                    param_placeholders.append(f"@{param_name} AS {col}")
                else:
                    # For other types (like datetime strings), convert to string
                    query_params.append(bigquery.ScalarQueryParameter(param_name, "STRING", str(val)))
                    # Try to detect if it's a timestamp string
                    if col == "paid_at" or "timestamp" in col.lower() or "at" in col.lower():
                        param_placeholders.append(f"PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S.%fZ', @{param_name}) AS {col}")
                    else:
                        param_placeholders.append(f"@{param_name} AS {col}")

            # Construct MERGE statement with parameterized values
            # MERGE will only insert if the match_key doesn't exist (WHEN NOT MATCHED)
            # This is atomic and prevents race conditions
            merge_query = f"""
            MERGE `{table_id}` AS target
            USING (
                SELECT {', '.join(param_placeholders)}
            ) AS source
            ON target.{match_key} = source.{match_key}
            WHEN NOT MATCHED THEN
                INSERT ({', '.join(columns)})
                VALUES ({', '.join([f'source.{col}' for col in columns])})
            """

            logger.debug(f"Executing MERGE query for {table_id}")
            job_config = bigquery.QueryJobConfig(query_parameters=query_params)
            query_job = self.client.query(merge_query, job_config=job_config)
            query_job.result()  # Wait for completion
            logger.debug(f"Merged row into {table_id} (inserted if not exists)")

        except Exception as e:
            logger.error(f"Error merging row into {table_name}: {e}", exc_info=True)
            raise

