"""BigQuery loader with MERGE-based deduplication."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from google.cloud import bigquery
from google.oauth2 import service_account

logger = logging.getLogger(__name__)


class BigQueryLoader:
    """Handles BigQuery operations including schema management and data loading."""

    def __init__(
        self,
        credentials_path: str,
        project_id: str,
        dataset_id: str,
        allowed_pre_existing_tables: Optional[List[str]] = None,
    ):
        """
        Initialize BigQuery loader.

        Args:
            credentials_path: Path to service account JSON credentials
            project_id: GCP project ID
            dataset_id: BigQuery dataset ID
            allowed_pre_existing_tables: List of pre-existing table names that can be modified.
                                        If None, defaults to empty list (no pre-existing tables allowed).
        """
        self.credentials_path = Path(credentials_path)
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.allowed_pre_existing_tables = allowed_pre_existing_tables or []

        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {credentials_path}"
            )

        # Load credentials and create BigQuery client
        credentials = service_account.Credentials.from_service_account_file(
            str(self.credentials_path),
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        self.client = bigquery.Client(
            credentials=credentials, project=project_id
        )

    def _parse_table_id(self, table_id: str) -> Tuple[str, str, str]:
        """
        Parse full table ID into components.

        Args:
            table_id: Full table ID (project.dataset.table or dataset.table)

        Returns:
            Tuple of (project_id, dataset_id, table_id)
        """
        parts = table_id.split(".")
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            return self.project_id, parts[0], parts[1]
        else:
            return self.project_id, self.dataset_id, parts[0]

    def load_schema(self, schema_path: str) -> List[bigquery.SchemaField]:
        """
        Load BigQuery schema from JSON file.

        Args:
            schema_path: Path to schema JSON file

        Returns:
            List of SchemaField objects
        """
        schema_file = Path(schema_path)
        if not schema_file.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        with open(schema_file, "r") as f:
            schema_json = json.load(f)

        # Handle both array and single object formats
        if isinstance(schema_json, list):
            return [bigquery.SchemaField.from_api_repr(field) for field in schema_json]
        else:
            return [bigquery.SchemaField.from_api_repr(schema_json)]

    def _get_schema_field_names(self, schema: List[bigquery.SchemaField], prefix: str = "") -> Set[str]:
        """
        Recursively extract all field names from a BigQuery schema.
        
        Args:
            schema: List of SchemaField objects
            prefix: Prefix for nested field names
            
        Returns:
            Set of field names (using dot notation for nested fields)
        """
        field_names = set()
        
        for field in schema:
            field_name = f"{prefix}.{field.name}" if prefix else field.name
            field_names.add(field_name)
            
            # Handle nested RECORD fields
            if field.fields:
                nested_fields = self._get_schema_field_names(field.fields, field_name)
                field_names.update(nested_fields)
        
        return field_names

    def _infer_field_type(self, value: Any) -> str:
        """
        Infer BigQuery field type from a Python value.
        
        Args:
            value: Python value to infer type from
            
        Returns:
            BigQuery field type string
        """
        if value is None:
            return "STRING"  # Default for null values
        elif isinstance(value, bool):
            return "BOOLEAN"
        elif isinstance(value, int):
            return "INTEGER"
        elif isinstance(value, float):
            return "FLOAT"
        elif isinstance(value, str):
            # Check if it's a timestamp-like string
            if "T" in value and ("Z" in value or "+" in value or "-" in value[-6:]):
                return "TIMESTAMP"
            return "STRING"
        elif isinstance(value, dict):
            return "RECORD"
        elif isinstance(value, list):
            if value and isinstance(value[0], dict):
                return "RECORD"  # REPEATED RECORD
            elif value:
                return self._infer_field_type(value[0])  # REPEATED of inferred type
            return "STRING"  # Default for empty lists
        else:
            return "STRING"  # Default fallback

    def _create_schema_field_from_value(
        self, 
        field_name: str, 
        value: Any,
        mode: str = "NULLABLE"
    ) -> bigquery.SchemaField:
        """
        Create a BigQuery SchemaField from a field name and value.
        
        Args:
            field_name: Name of the field
            value: Sample value to infer type from
            mode: Field mode (NULLABLE, REQUIRED, REPEATED)
            
        Returns:
            SchemaField object
        """
        field_type = self._infer_field_type(value)
        
        # Handle REPEATED fields
        if isinstance(value, list) and value:
            if isinstance(value[0], dict):
                # REPEATED RECORD - for now, create a simple RECORD
                # This is a limitation - we'd need sample data to infer nested structure
                logger.warning(
                    f"Field '{field_name}' is a REPEATED RECORD. "
                    "Automatic schema update for nested structures is limited. "
                    "Manual schema update may be required."
                )
                return bigquery.SchemaField(
                    field_name, 
                    "STRING",  # Fallback to STRING for complex nested structures
                    mode="REPEATED"
                )
            else:
                # REPEATED simple type
                return bigquery.SchemaField(
                    field_name,
                    self._infer_field_type(value[0]),
                    mode="REPEATED"
                )
        elif isinstance(value, dict):
            # RECORD type - for now, we'll create a simple RECORD
            # Full nested structure inference would require more complex logic
            logger.warning(
                f"Field '{field_name}' is a RECORD type. "
                "Automatic schema update for nested structures is limited. "
                "Manual schema update may be required."
            )
            return bigquery.SchemaField(
                field_name,
                "STRING",  # Fallback - manual update needed for proper structure
                mode="NULLABLE"
            )
        else:
            return bigquery.SchemaField(field_name, field_type, mode=mode)

    def _update_table_schema(
        self,
        table: bigquery.Table,
        new_fields: List[bigquery.SchemaField],
    ) -> bigquery.Table:
        """
        Update an existing table's schema to include new fields.
        
        Args:
            table: Existing BigQuery table
            new_fields: List of new SchemaField objects to add
            
        Returns:
            Updated table object
        """
        existing_field_names = {field.name for field in table.schema}
        
        # Filter out fields that already exist
        fields_to_add = [
            field for field in new_fields 
            if field.name not in existing_field_names
        ]
        
        if not fields_to_add:
            return table
        
        logger.info(
            f"Adding {len(fields_to_add)} new field(s) to table {table.table_id}: "
            f"{[f.name for f in fields_to_add]}"
        )
        
        # Add new fields to the schema
        updated_schema = list(table.schema) + fields_to_add
        
        # Update the table
        table.schema = updated_schema
        table = self.client.update_table(table, ["schema"])
        
        logger.info(f"Successfully updated schema for table {table.table_id}")
        return table

    def _validate_table_schema(
        self,
        table: bigquery.Table,
        expected_schema: List[bigquery.SchemaField],
    ) -> bool:
        """
        Validate that an existing table has the expected schema.
        
        Args:
            table: Existing BigQuery table
            expected_schema: Expected schema fields
            
        Returns:
            True if schema matches, False otherwise
        """
        expected_field_names = {field.name for field in expected_schema}
        actual_field_names = {field.name for field in table.schema}
        
        # Check if all expected fields exist
        if not expected_field_names.issubset(actual_field_names):
            missing_fields = expected_field_names - actual_field_names
            logger.warning(
                f"Table {table.table_id} is missing required fields: {missing_fields}. "
                "This table may have an old schema format."
            )
            return False
        
        # Check that key fields have correct types
        for expected_field in expected_schema:
            actual_field = next(
                (f for f in table.schema if f.name == expected_field.name),
                None
            )
            if actual_field and actual_field.field_type != expected_field.field_type:
                logger.warning(
                    f"Table {table.table_id} field '{expected_field.name}' has type "
                    f"{actual_field.field_type}, expected {expected_field.field_type}"
                )
                # For critical fields like 'id' and 'date', this is a mismatch
                if expected_field.name in ['id', 'date']:
                    return False
        
        return True

    def ensure_table_exists(
        self,
        table_id: str,
        schema: List[bigquery.SchemaField],
    ) -> bigquery.Table:
        """
        Ensure BigQuery table exists, creating it if necessary.
        
        Only allows operations on pre-existing table "v2_faction_40832_crimes-new".
        All other pre-existing tables will be skipped.

        Args:
            table_id: Full table ID
            schema: Table schema

        Returns:
            BigQuery Table object
            
        Raises:
            ValueError: If table exists but is not the allowed pre-existing table
        """
        project_id, dataset_id, table_name = self._parse_table_id(table_id)

        # Ensure dataset exists
        dataset_ref = self.client.dataset(dataset_id)
        try:
            self.client.get_dataset(dataset_ref)
        except Exception:
            logger.info(f"Creating dataset {dataset_id}")
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = "US"  # Default location
            self.client.create_dataset(dataset, exists_ok=True)

        # Check if table exists
        table_ref = self.client.dataset(dataset_id).table(table_name)
        try:
            table = self.client.get_table(table_ref)
            logger.debug(f"Table {table_id} already exists")
            
            # Skip all pre-existing tables except the allowed ones
            if self.allowed_pre_existing_tables and table_name not in self.allowed_pre_existing_tables:
                raise ValueError(
                    f"Table {table_id} is a pre-existing table. "
                    f"Only {self.allowed_pre_existing_tables} are allowed. "
                    "Skipping - will not modify pre-existing tables."
                )
            
            # For the allowed table, check if we need to add new fields
            # First check if all expected fields exist
            expected_field_names = {field.name for field in schema}
            actual_field_names = {field.name for field in table.schema}
            
            # Find new fields that need to be added
            new_fields = [
                field for field in schema 
                if field.name not in actual_field_names
            ]
            
            if new_fields:
                # Update schema to include new fields
                logger.info(
                    f"Detected {len(new_fields)} new field(s) in schema that need to be added to table: "
                    f"{[f.name for f in new_fields]}"
                )
                table = self._update_table_schema(table, new_fields)
            
            # Validate that critical fields match
            if not self._validate_table_schema(table, schema):
                raise ValueError(
                    f"Table {table_id} exists but has incompatible schema for critical fields. "
                    "Skipping - will not modify pre-existing tables."
                )
            return table
        except ValueError:
            # Re-raise validation errors (these indicate we should skip)
            raise
        except Exception:
            # Table doesn't exist, create it with the correct schema
            logger.info(f"Creating table {table_id}")
            table = bigquery.Table(table_ref, schema=schema)
            table = self.client.create_table(table)
            logger.info(f"Created table {table_id}")
            return table

    def load_data_replace(
        self,
        table_id: str,
        records: List[Dict[str, Any]],
        schema: List[bigquery.SchemaField],
    ) -> None:
        """
        Load data using REPLACE mode (overwrites existing data).

        Args:
            table_id: Full table ID
            records: List of records to load
            schema: Table schema
        """
        if not records:
            logger.info("No records to load")
            return

        project_id, dataset_id, table_name = self._parse_table_id(table_id)

        # Ensure table exists
        self.ensure_table_exists(table_id, schema)

        # Load data with write_disposition=WRITE_TRUNCATE
        table_ref = self.client.dataset(dataset_id).table(table_name)
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )

        logger.info(f"Loading {len(records)} records to {table_id} (REPLACE mode)")
        job = self.client.load_table_from_json(
            records, table_ref, job_config=job_config
        )
        job.result()  # Wait for job to complete

        logger.info(
            f"Successfully loaded {job.output_rows} rows to {table_id}"
        )

    def load_data_append_merge(
        self,
        table_id: str,
        records: List[Dict[str, Any]],
        schema: List[bigquery.SchemaField],
        deduplication_key: str = "id",
    ) -> Dict[str, int]:
        """
        Load data using APPEND mode with MERGE-based deduplication.

        Args:
            table_id: Full table ID
            records: List of records to load
            schema: Table schema
            deduplication_key: Field name to use for deduplication

        Returns:
            Dictionary with counts of inserted, updated, and total records processed
        """
        if not records:
            logger.info("No records to load")
            return {"inserted": 0, "updated": 0, "total": 0}

        project_id, dataset_id, table_name = self._parse_table_id(table_id)

        # Ensure table exists
        self.ensure_table_exists(table_id, schema)

        # Create staging table
        staging_table_name = f"{table_name}_staging"
        staging_table_id = f"{project_id}.{dataset_id}.{staging_table_name}"
        staging_table_ref = self.client.dataset(dataset_id).table(
            staging_table_name
        )

        try:
            # Create staging table if it doesn't exist
            try:
                staging_table = self.client.get_table(staging_table_ref)
            except Exception:
                logger.debug(f"Creating staging table {staging_table_id}")
                staging_table = bigquery.Table(staging_table_ref, schema=schema)
                staging_table = self.client.create_table(staging_table)

            # Load data to staging table
            logger.info(
                f"Loading {len(records)} records to staging table {staging_table_id}"
            )
            job_config = bigquery.LoadJobConfig(
                schema=schema,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            )
            job = self.client.load_table_from_json(
                records, staging_table_ref, job_config=job_config
            )
            job.result()  # Wait for completion

            # Get statistics before MERGE to estimate updates vs inserts
            # Query target table to see how many records already exist
            total_processed = len(records)
            inserted = total_processed
            updated = 0
            
            try:
                # Check how many records in staging already exist in target
                check_query = f"""
                SELECT COUNT(DISTINCT s.{deduplication_key}) as existing_count
                FROM `{staging_table_id}` s
                INNER JOIN `{table_id}` t
                ON s.{deduplication_key} = t.{deduplication_key}
                """
                check_job = self.client.query(check_query)
                check_result = list(check_job.result())[0]
                existing_count = check_result.existing_count
                # Records that exist are "updates", new ones are "inserts"
                updated = existing_count
                inserted = max(0, total_processed - updated)
            except Exception as e:
                logger.debug(f"Could not get pre-merge statistics: {e}. "
                            "Will use approximation after MERGE.")
                # If table doesn't exist or query fails, assume all are inserts
                inserted = total_processed
                updated = 0

            # Build MERGE statement
            merge_sql = self._build_merge_statement(
                table_id, staging_table_id, deduplication_key, schema
            )

            # Execute MERGE
            logger.info(f"Executing MERGE statement for {table_id}")
            logger.info(f"MERGE SQL: {merge_sql}")
            query_job = self.client.query(merge_sql)
            query_job.result()  # Wait for completion

            logger.info(
                f"MERGE completed: {inserted} inserted, {updated} updated "
                f"(total: {total_processed} records processed)"
            )

            # Cleanup staging table
            logger.debug(f"Cleaning up staging table {staging_table_id}")
            self.client.delete_table(staging_table_ref, not_found_ok=True)

            return {"inserted": inserted, "updated": updated, "total": total_processed}

        except Exception as e:
            logger.error(
                f"Error during MERGE operation for {table_id}: {e}",
                exc_info=True
            )
            logger.warning(
                f"Staging table {staging_table_id} preserved for debugging. "
                "Please clean it up manually if needed."
            )
            # Don't delete staging table on error for debugging
            raise

    def _build_merge_statement(
        self,
        target_table: str,
        source_table: str,
        key_field: str,
        schema: List[bigquery.SchemaField],
    ) -> str:
        """
        Build MERGE SQL statement.

        Args:
            target_table: Target table ID
            source_table: Source (staging) table ID
            key_field: Field name for deduplication
            schema: Table schema

        Returns:
            MERGE SQL statement
        """
        # Build field list (excluding the key field for UPDATE SET)
        # Also exclude 'fetched_at' from updates to preserve original fetch timestamp
        field_names = [field.name for field in schema]
        update_fields = [f for f in field_names if f != key_field and f != "fetched_at"]
        insert_fields = field_names

        # Build UPDATE SET clause (use target. prefix for clarity)
        update_set = ", ".join([f"target.{f} = source.{f}" for f in update_fields])

        # Build INSERT VALUES clause
        insert_values = ", ".join([f"source.{f}" for f in insert_fields])

        # Use direct comparison for id field (now INTEGER type)
        key_compare = f"target.{key_field} = source.{key_field}"
        
        merge_sql = f"""
        MERGE `{target_table}` AS target
        USING `{source_table}` AS source
        ON {key_compare}
        WHEN MATCHED THEN
          UPDATE SET
            {update_set}
        WHEN NOT MATCHED THEN
          INSERT ({', '.join(insert_fields)})
          VALUES ({insert_values})
        """

        return merge_sql

    def get_table_record_count(
        self,
        table_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get record count and statistics for a BigQuery table.
        
        Args:
            table_id: Full table ID
            
        Returns:
            Dictionary with:
            - 'total_records': Total record count
            - 'unique_ids': Count of unique IDs
            - 'oldest_record': Timestamp of oldest record (fetched_at)
            - 'newest_record': Timestamp of newest record (fetched_at)
            Or None if table doesn't exist or query fails
        """
        try:
            # Query for record count and stats
            query = f"""
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT id) as unique_ids,
                MIN(fetched_at) as oldest_record,
                MAX(fetched_at) as newest_record
            FROM `{table_id}`
            """
            
            query_job = self.client.query(query)
            results = query_job.result()
            
            for row in results:
                return {
                    'total_records': row.total_records,
                    'unique_ids': row.unique_ids,
                    'oldest_record': row.oldest_record,
                    'newest_record': row.newest_record,
                }
            
            return None
        except Exception as e:
            logger.warning(f"Could not get record count for table {table_id}: {e}")
            return None

    def _detect_new_fields_in_records(
        self,
        records: List[Dict[str, Any]],
        schema: List[bigquery.SchemaField],
    ) -> Dict[str, Any]:
        """
        Detect new fields in records that aren't in the schema.
        
        Args:
            records: List of records to analyze
            schema: Current schema
            
        Returns:
            Dictionary with:
            - 'new_fields': List of new field names found
            - 'sample_values': Dict mapping field names to sample values
            - 'records_with_new_fields': Count of records containing new fields
        """
        if not records:
            return {
                'new_fields': [],
                'sample_values': {},
                'records_with_new_fields': 0
            }
        
        # Get existing field names from schema
        existing_field_names = {field.name for field in schema}
        
        # Track new fields across all records
        new_fields_set = set()
        sample_values = {}
        records_with_new_fields = 0
        
        for record in records:
            has_new_field = False
            for field_name, field_value in record.items():
                if field_name not in existing_field_names and field_name != "fetched_at":
                    new_fields_set.add(field_name)
                    if field_name not in sample_values:
                        sample_values[field_name] = field_value
                    has_new_field = True
            if has_new_field:
                records_with_new_fields += 1
        
        return {
            'new_fields': sorted(list(new_fields_set)),
            'sample_values': sample_values,
            'records_with_new_fields': records_with_new_fields
        }

    def _verify_fields_in_table(
        self,
        table_id: str,
        field_names: List[str],
    ) -> Dict[str, bool]:
        """
        Verify that specific fields exist in a BigQuery table.
        
        Args:
            table_id: Full table ID
            field_names: List of field names to verify
            
        Returns:
            Dictionary mapping field names to whether they exist in the table
        """
        project_id, dataset_id, table_name = self._parse_table_id(table_id)
        table_ref = self.client.dataset(dataset_id).table(table_name)
        
        try:
            table = self.client.get_table(table_ref)
            existing_field_names = {field.name for field in table.schema}
            
            return {
                field_name: field_name in existing_field_names
                for field_name in field_names
            }
        except Exception as e:
            logger.warning(f"Could not verify fields in table {table_id}: {e}")
            return {field_name: False for field_name in field_names}

    def _detect_and_add_new_fields(
        self,
        table_id: str,
        records: List[Dict[str, Any]],
        schema: List[bigquery.SchemaField],
    ) -> Tuple[List[bigquery.SchemaField], Dict[str, Any]]:
        """
        Detect new fields in records that aren't in the schema and add them.
        
        Args:
            table_id: Full table ID
            records: List of records to analyze
            schema: Current schema
            
        Returns:
            Tuple of (updated schema, detection info dict)
        """
        if not records:
            return schema, {
                'new_fields': [],
                'sample_values': {},
                'records_with_new_fields': 0,
                'fields_added': [],
                'fields_failed': []
            }
        
        # Detect new fields
        detection_info = self._detect_new_fields_in_records(records, schema)
        new_fields_list = detection_info['new_fields']
        
        if not new_fields_list:
            return schema, {**detection_info, 'fields_added': [], 'fields_failed': []}
        
        # Get existing field names from schema
        existing_field_names = {field.name for field in schema}
        
        # Analyze first record to create schema fields for new top-level fields
        sample_record = records[0]
        fields_to_add = []
        fields_failed = []
        
        for field_name in new_fields_list:
            if field_name in sample_record:
                field_value = sample_record[field_name]
                try:
                    new_field = self._create_schema_field_from_value(field_name, field_value)
                    fields_to_add.append(new_field)
                    logger.info(
                        f"Detected new field '{field_name}' with inferred type '{new_field.field_type}'. "
                        f"Will add to table schema."
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not create schema field for new field '{field_name}': {e}. "
                        "Manual schema update may be required."
                    )
                    fields_failed.append(field_name)
        
        if fields_to_add:
            # Update the table schema
            project_id, dataset_id, table_name = self._parse_table_id(table_id)
            table_ref = self.client.dataset(dataset_id).table(table_name)
            
            try:
                table = self.client.get_table(table_ref)
                table = self._update_table_schema(table, fields_to_add)
                # Verify fields were added
                added_field_names = [f.name for f in fields_to_add]
                verification = self._verify_fields_in_table(table_id, added_field_names)
                
                # Log verification results
                for field_name, exists in verification.items():
                    if exists:
                        logger.info(f"✓ Verified: Field '{field_name}' successfully added to table {table_id}")
                    else:
                        logger.warning(f"✗ Warning: Field '{field_name}' not found in table {table_id} after update")
                
                # Return updated schema
                return list(table.schema), {
                    **detection_info,
                    'fields_added': added_field_names,
                    'fields_failed': fields_failed
                }
            except Exception as e:
                logger.error(
                    f"Failed to update table schema with new fields: {e}. "
                    "Data may fail to load if new fields are not in schema."
                )
                return schema, {
                    **detection_info,
                    'fields_added': [],
                    'fields_failed': new_fields_list
                }
        
        return schema, {
            **detection_info,
            'fields_added': [],
            'fields_failed': fields_failed
        }

    def load_data(
        self,
        table_id: str,
        records: List[Dict[str, Any]],
        schema: List[bigquery.SchemaField],
        storage_mode: str = "replace",
        deduplication_key: str = "id",
    ) -> Optional[Dict[str, Any]]:
        """
        Load data to BigQuery table.

        Args:
            table_id: Full table ID
            records: List of records to load
            schema: Table schema
            storage_mode: 'replace' or 'append'
            deduplication_key: Field name for deduplication (used in append mode)

        Returns:
            Dictionary with statistics including new field detection info, or None for replace mode
        """
        # Detect and add new fields from records to schema
        # This ensures new fields from the API are automatically captured
        updated_schema, field_detection_info = self._detect_and_add_new_fields(table_id, records, schema)
        
        # Log new field detection summary
        if field_detection_info['new_fields']:
            logger.info(
                f"📊 NEW FIELD DETECTION SUMMARY for {table_id}:"
            )
            logger.info(
                f"   - New fields detected: {field_detection_info['new_fields']}"
            )
            logger.info(
                f"   - Records containing new fields: {field_detection_info['records_with_new_fields']} out of {len(records)}"
            )
            if field_detection_info['fields_added']:
                logger.info(
                    f"   - Fields successfully added to schema: {field_detection_info['fields_added']}"
                )
            if field_detection_info['fields_failed']:
                logger.warning(
                    f"   - Fields that could not be added: {field_detection_info['fields_failed']}"
                )
            # Log sample values for new fields
            for field_name, sample_value in field_detection_info['sample_values'].items():
                logger.info(
                    f"   - Sample value for '{field_name}': {sample_value} (type: {type(sample_value).__name__})"
                )
        
        # Load the data
        load_result = None
        if storage_mode == "replace":
            self.load_data_replace(table_id, records, updated_schema)
        elif storage_mode == "append":
            load_result = self.load_data_append_merge(
                table_id, records, updated_schema, deduplication_key
            )
        else:
            raise ValueError(
                f"Unknown storage_mode: {storage_mode}. "
                "Must be 'replace' or 'append'"
            )
        
        # Verify new fields are in the table after loading
        if field_detection_info['new_fields']:
            logger.info(f"🔍 Verifying new fields are present in table {table_id}...")
            verification = self._verify_fields_in_table(table_id, field_detection_info['new_fields'])
            
            all_verified = all(verification.values())
            if all_verified:
                logger.info(
                    f"✅ VERIFICATION SUCCESS: All {len(field_detection_info['new_fields'])} new field(s) "
                    f"are present in table {table_id}"
                )
            else:
                missing_fields = [f for f, exists in verification.items() if not exists]
                logger.warning(
                    f"⚠️  VERIFICATION WARNING: {len(missing_fields)} field(s) not found in table: {missing_fields}"
                )
                logger.info(
                    f"   Fields verified as present: {[f for f, exists in verification.items() if exists]}"
                )
            
            # Add verification info to result
            field_detection_info['verification'] = verification
            field_detection_info['all_fields_verified'] = all_verified
        
        # Combine load results with field detection info
        if load_result:
            return {**load_result, **field_detection_info}
        elif field_detection_info['new_fields']:
            return field_detection_info
        else:
            return None

