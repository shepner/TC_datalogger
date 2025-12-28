"""Data processing and transformation for Torn City API records."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)


class DataProcessor:
    """Processes and transforms API records for BigQuery storage."""

    @staticmethod
    def _extract_field_names(record: Dict[str, Any], prefix: str = "") -> Set[str]:
        """
        Recursively extract all field names from a nested record structure.
        
        Args:
            record: Record dictionary
            prefix: Prefix for nested field names (for RECORD types)
            
        Returns:
            Set of field names (using dot notation for nested fields)
        """
        field_names = set()
        
        for key, value in record.items():
            field_name = f"{prefix}.{key}" if prefix else key
            field_names.add(field_name)
            
            # Handle nested dictionaries (RECORD types)
            if isinstance(value, dict):
                nested_fields = DataProcessor._extract_field_names(value, field_name)
                field_names.update(nested_fields)
            # Handle lists of dictionaries (REPEATED RECORD types)
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                nested_fields = DataProcessor._extract_field_names(value[0], field_name)
                field_names.update(nested_fields)
        
        return field_names

    @staticmethod
    def detect_new_fields(
        records: List[Dict[str, Any]],
        known_schema_fields: Set[str]
    ) -> Set[str]:
        """
        Detect fields in API records that are not in the known schema.
        
        Args:
            records: List of API records
            known_schema_fields: Set of known field names from schema
            
        Returns:
            Set of new field names found in the records
        """
        all_fields = set()
        
        for record in records:
            record_fields = DataProcessor._extract_field_names(record)
            all_fields.update(record_fields)
        
        # Find fields that are in records but not in schema
        # Note: We compare base field names (without nested paths for now)
        new_fields = set()
        for field in all_fields:
            # Check if this field or any parent path is in the schema
            field_parts = field.split('.')
            base_field = field_parts[0]
            
            # Check base field and common variations
            if base_field not in known_schema_fields:
                # Also check if it's a nested field we should track
                if '.' not in field or base_field in known_schema_fields:
                    new_fields.add(field)
        
        return new_fields

    @staticmethod
    def process_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single API record for BigQuery, preserving the structure.

        Args:
            record: Raw API record dictionary from Torn City API

        Returns:
            Processed record compatible with BigQuery schema
        """
        # Validate required field
        if "id" not in record:
            raise ValueError("Record missing required 'id' field")

        # Create a copy of the record to avoid modifying the original
        processed = record.copy()

        # Add fetched_at timestamp
        processed["fetched_at"] = datetime.utcnow().isoformat() + "Z"

        # Ensure all fields match the schema structure
        # The API returns the data in the correct format, we just need to ensure
        # null values are handled properly and types are correct

        return processed

    @staticmethod
    def process_records(
        records: List[Dict[str, Any]],
        known_schema_fields: Set[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Process a list of API records for BigQuery.

        Args:
            records: List of raw API records from Torn City API
            known_schema_fields: Optional set of known schema field names for new field detection

        Returns:
            List of processed records ready for BigQuery
        """
        processed = []
        errors = []

        # Detect new fields if schema fields are provided
        if known_schema_fields and records:
            new_fields = DataProcessor.detect_new_fields(records, known_schema_fields)
            if new_fields:
                logger.warning(
                    f"Detected {len(new_fields)} new field(s) in API response not in schema: {sorted(new_fields)}. "
                    f"These fields will be included in the data but may need schema update."
                )

        for record in records:
            try:
                processed_record = DataProcessor.process_record(record)
                processed.append(processed_record)
            except Exception as e:
                error_msg = f"Failed to process record: {e}"
                logger.error(error_msg)
                errors.append({"record": record, "error": str(e)})

        if errors:
            logger.warning(
                f"Failed to process {len(errors)} out of {len(records)} records"
            )

        logger.info(
            f"Processed {len(processed)} records successfully "
            f"({len(errors)} errors)"
        )

        return processed

