"""Health checker that orchestrates health checks for all services."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.docker_client import DockerClient
from src.log_parser import LogParser

logger = logging.getLogger(__name__)


class HealthChecker:
    """Orchestrates health checks for all microservices."""

    # Service configuration mapping
    SERVICES = {
        "tc-faction-crimes": {
            "name": "TC Faction Crimes",
            "container": "tc-faction-crimes-pipeline",
            "log_path": "/app/logs/faction_crimes/cron.log",
            "log_path_host": "TC_faction_crimes/logs/cron.log",
        },
        "tc-faction-members": {
            "name": "TC Faction Members",
            "container": "tc-faction-members-pipeline",
            "log_path": "/app/logs/faction_members/cron.log",
            "log_path_host": "TC_faction_members/logs/cron.log",
        },
        "tc-items": {
            "name": "TC Items",
            "container": "tc-items-pipeline",
            "log_path": "/app/logs/items/cron.log",
            "log_path_host": "TC_items/logs/cron.log",
        },
        "tc-user-events": {
            "name": "TC User Events",
            "container": "tc-user-events-pipeline",
            "log_path": "/app/logs/user_events/cron.log",
            "log_path_host": "TC_user_events/logs/cron.log",
        },
    }

    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize health checker.

        Args:
            base_path: Base path to the project root (for log file resolution)
        """
        self.docker_client = DockerClient()
        self.base_path = Path(base_path) if base_path else Path(__file__).parent.parent.parent

    def _resolve_log_path(self, service_key: str) -> str:
        """
        Resolve the actual log file path.

        Args:
            service_key: Service key from SERVICES dict

        Returns:
            Path to log file
        """
        service_config = self.SERVICES[service_key]
        # Try container path first (when running in Docker with mounted volumes)
        container_path = Path(service_config["log_path"])
        if container_path.exists():
            return str(container_path)

        # Fall back to host path (if running locally)
        host_path = self.base_path / service_config["log_path_host"]
        if host_path.exists():
            return str(host_path)

        # Return the container path as default (may not exist yet)
        return str(container_path)

    def check_service_health(self, service_key: str) -> Dict:
        """
        Check health of a single service.

        Args:
            service_key: Service key from SERVICES dict

        Returns:
            Dictionary with health information:
            - service_name: str
            - container_status: dict
            - last_successful_run: Optional[datetime]
            - recent_errors: List[dict]
            - log_stats: dict
        """
        if service_key not in self.SERVICES:
            return {
                "error": f"Unknown service: {service_key}",
            }

        service_config = self.SERVICES[service_key]

        # Check container status
        container_status = self.docker_client.get_container_status(
            service_config["container"]
        )

        # Parse logs
        log_path = self._resolve_log_path(service_key)
        log_parser = LogParser(log_path)

        last_successful_run = log_parser.get_last_successful_run()
        recent_errors = log_parser.get_recent_errors(limit=10)
        log_stats = log_parser.get_log_stats()

        # Determine overall health status
        health_status = self._determine_health_status(
            container_status,
            last_successful_run,
            recent_errors,
        )

        return {
            "service_key": service_key,
            "service_name": service_config["name"],
            "container_status": container_status,
            "last_successful_run": (
                last_successful_run.isoformat() if last_successful_run else None
            ),
            "recent_errors": [
                {
                    "timestamp": e["timestamp"].isoformat(),
                    "level": e["level"],
                    "message": e["message"],
                }
                for e in recent_errors
            ],
            "log_stats": {
                "exists": log_stats["exists"],
                "last_modified": (
                    log_stats["last_modified"].isoformat()
                    if log_stats["last_modified"]
                    else None
                ),
                "line_count": log_stats["line_count"],
            },
            "health_status": health_status,
        }

    def _determine_health_status(
        self,
        container_status: Dict,
        last_successful_run: Optional[datetime],
        recent_errors: List[Dict],
    ) -> str:
        """
        Determine overall health status.

        Args:
            container_status: Container status dict
            last_successful_run: Last successful run timestamp
            recent_errors: List of recent errors

        Returns:
            Health status: "healthy", "degraded", or "unhealthy"
        """
        # Unhealthy if container is not running
        if not container_status.get("running", False):
            return "unhealthy"

        # Check if last run was recent (within last 30 minutes)
        if last_successful_run:
            time_since_last_run = (datetime.now() - last_successful_run).total_seconds()
            if time_since_last_run > 30 * 60:  # 30 minutes
                return "degraded"
        else:
            # No successful run found
            return "degraded"

        # Check for recent errors
        if recent_errors:
            # If errors are very recent (last 15 minutes), consider degraded
            most_recent_error = recent_errors[-1]["timestamp"]
            time_since_error = (datetime.now() - most_recent_error).total_seconds()
            if time_since_error < 15 * 60:  # 15 minutes
                return "degraded"

        return "healthy"

    def check_all_services(self) -> Dict:
        """
        Check health of all services.

        Returns:
            Dictionary with health information for all services
        """
        results = {}
        for service_key in self.SERVICES.keys():
            try:
                results[service_key] = self.check_service_health(service_key)
            except Exception as e:
                logger.error(f"Error checking health for {service_key}: {e}")
                results[service_key] = {
                    "service_key": service_key,
                    "error": str(e),
                    "health_status": "error",
                }

        return {
            "services": results,
            "timestamp": datetime.now().isoformat(),
            "docker_available": self.docker_client.is_available(),
        }

