"""Configuration management for Torn City API to BigQuery pipeline."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Config:
    """Manages configuration from JSON file and environment variables."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration.

        Args:
            config_path: Path to configuration JSON file. If None, uses default.
        """
        if config_path is None:
            # Default config path
            project_root = Path(__file__).parent.parent
            config_path = project_root / "config" / "TC_API_config.json"

        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(self.config_path, "r") as f:
            self.config = json.load(f)

    def get_api_base_url(self) -> str:
        """
        Get API base URL.

        Returns:
            API base URL
        """
        # Check environment variable first
        env_url = os.getenv("TC_API_BASE_URL")
        if env_url:
            return env_url

        # Fall back to config file
        api_config = self.config.get("api", {})
        return api_config.get("base_url", "https://api.torn.com")

    def get_api_key(self, key_name: str) -> Optional[str]:
        """
        Get API key by name, checking environment variables first.

        Args:
            key_name: Name of the API key in config

        Returns:
            API key value or None if not found
        """
        # Check environment variable first (format: TC_API_KEY_<KEY_NAME>)
        env_key = f"TC_API_KEY_{key_name.upper()}"
        env_value = os.getenv(env_key)
        if env_value:
            return env_value

        # Fall back to config file
        api_keys = self.config.get("api_keys", {})
        return api_keys.get(key_name)

    def get_gcp_credentials_path(self) -> str:
        """
        Get GCP credentials file path.

        Returns:
            Path to credentials file
        """
        # Check environment variable first
        env_path = os.getenv("TC_GCP_CREDENTIALS_PATH")
        if env_path:
            return env_path

        # Fall back to config file
        gcp_config = self.config.get("gcp", {})
        credentials_path = gcp_config.get("credentials_path", "config/credentials.json")

        # If relative path, make it relative to project root
        if not Path(credentials_path).is_absolute():
            project_root = Path(__file__).parent.parent
            credentials_path = str(project_root / credentials_path)

        return credentials_path

    def get_gcp_project_id(self) -> str:
        """
        Get GCP project ID.

        Returns:
            Project ID
        """
        # Check environment variable first
        env_project = os.getenv("TC_GCP_PROJECT_ID")
        if env_project:
            return env_project

        # Fall back to config file
        gcp_config = self.config.get("gcp", {})
        project_id = gcp_config.get("project_id")
        if not project_id:
            raise ValueError("GCP project_id not configured")
        return project_id

    def get_gcp_dataset_id(self) -> str:
        """
        Get BigQuery dataset ID.

        Returns:
            Dataset ID
        """
        # Check environment variable first
        env_dataset = os.getenv("TC_GCP_DATASET_ID")
        if env_dataset:
            return env_dataset

        # Fall back to config file
        gcp_config = self.config.get("gcp", {})
        dataset_id = gcp_config.get("dataset_id")
        if not dataset_id:
            raise ValueError("GCP dataset_id not configured")
        return dataset_id

    def get_gcp_allowed_pre_existing_tables(self) -> List[str]:
        """
        Get list of allowed pre-existing tables that can be modified.

        Returns:
            List of allowed table names
        """
        # Check environment variable first (comma-separated)
        env_tables = os.getenv("TC_GCP_ALLOWED_PRE_EXISTING_TABLES")
        if env_tables:
            return [table.strip() for table in env_tables.split(",") if table.strip()]

        # Fall back to config file
        gcp_config = self.config.get("gcp", {})
        allowed_tables = gcp_config.get("allowed_pre_existing_tables", [])
        return allowed_tables if isinstance(allowed_tables, list) else []

    def get_endpoints(self) -> List[Dict[str, Any]]:
        """
        Get all configured endpoints.

        Returns:
            List of endpoint configuration dictionaries
        """
        return self.config.get("endpoints", [])

    def get_endpoint(self, endpoint_name: str) -> Optional[Dict[str, Any]]:
        """
        Get endpoint configuration by name.

        Args:
            endpoint_name: Name of the endpoint

        Returns:
            Endpoint configuration dictionary or None if not found
        """
        endpoints = self.get_endpoints()
        for endpoint in endpoints:
            if endpoint.get("name") == endpoint_name:
                return endpoint
        return None

    def get_rate_limit(self, endpoint: Dict[str, Any]) -> int:
        """
        Get rate limit for an endpoint.

        Args:
            endpoint: Endpoint configuration dictionary

        Returns:
            Rate limit (requests per minute)
        """
        return endpoint.get("rate_limit") or self.config.get("defaults", {}).get("rate_limit", 60)

    def get_timeout(self, endpoint: Dict[str, Any]) -> int:
        """
        Get timeout for an endpoint.

        Args:
            endpoint: Endpoint configuration dictionary

        Returns:
            Timeout in seconds
        """
        return endpoint.get("timeout") or self.config.get("defaults", {}).get("timeout", 30)

    def get_max_retries(self, endpoint: Dict[str, Any]) -> int:
        """
        Get max retries for an endpoint.

        Args:
            endpoint: Endpoint configuration dictionary

        Returns:
            Maximum number of retries
        """
        return endpoint.get("max_retries") or self.config.get("defaults", {}).get("max_retries", 3)

    def get_retry_delay(self, endpoint: Dict[str, Any]) -> int:
        """
        Get retry delay for an endpoint.

        Args:
            endpoint: Endpoint configuration dictionary

        Returns:
            Retry delay in seconds
        """
        return endpoint.get("retry_delay") or self.config.get("defaults", {}).get("retry_delay", 60)

    def get_timezone(self) -> str:
        """
        Get timezone for scheduling.

        Returns:
            IANA timezone identifier
        """
        return self.config.get("defaults", {}).get("timezone", "America/Chicago")
