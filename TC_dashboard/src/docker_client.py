"""Docker client for checking container status."""

import logging
from typing import Dict, Optional

try:
    import docker
except ImportError:
    docker = None

logger = logging.getLogger(__name__)


class DockerClient:
    """Client for interacting with Docker API."""

    def __init__(self):
        """Initialize Docker client."""
        self.client = None
        if docker:
            try:
                self.client = docker.from_env()
            except Exception as e:
                logger.warning(f"Could not connect to Docker: {e}")

    def is_available(self) -> bool:
        """Check if Docker client is available."""
        return self.client is not None

    def get_container_status(self, container_name: str) -> Dict[str, any]:
        """
        Get status of a Docker container.

        Args:
            container_name: Name of the container

        Returns:
            Dictionary with status information:
            - running: bool
            - status: str (e.g., "running", "stopped", "not_found")
            - started_at: Optional[str]
        """
        if not self.client:
            return {
                "running": False,
                "status": "docker_unavailable",
                "started_at": None,
            }

        try:
            container = self.client.containers.get(container_name)
            container.reload()  # Refresh container state

            return {
                "running": container.status == "running",
                "status": container.status,
                "started_at": container.attrs.get("State", {}).get("StartedAt"),
            }
        except docker.errors.NotFound:
            return {
                "running": False,
                "status": "not_found",
                "started_at": None,
            }
        except Exception as e:
            logger.error(f"Error checking container {container_name}: {e}")
            return {
                "running": False,
                "status": "error",
                "started_at": None,
            }

    def get_all_containers_status(self, container_names: list) -> Dict[str, Dict]:
        """
        Get status for multiple containers.

        Args:
            container_names: List of container names

        Returns:
            Dictionary mapping container names to their status
        """
        return {
            name: self.get_container_status(name) for name in container_names
        }

