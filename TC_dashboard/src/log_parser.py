"""Log parser for extracting health information from service logs."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LogParser:
    """Parser for extracting health metrics from log files."""

    # Log format: YYYY-MM-DD HH:MM:SS - logger_name - LEVEL - message
    LOG_PATTERN = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - [^-]+ - (\w+) - (.+)$"
    )

    def __init__(self, log_file_path: str):
        """
        Initialize log parser.

        Args:
            log_file_path: Path to the log file
        """
        self.log_file_path = Path(log_file_path)

    def _parse_log_line(self, line: str) -> Optional[Dict]:
        """
        Parse a single log line.

        Args:
            line: Log line to parse

        Returns:
            Dictionary with timestamp, level, message, or None if not parseable
        """
        match = self.LOG_PATTERN.match(line.strip())
        if not match:
            return None

        timestamp_str, level, message = match.groups()
        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            return {
                "timestamp": timestamp,
                "level": level,
                "message": message,
            }
        except ValueError:
            return None

    def get_last_successful_run(self) -> Optional[datetime]:
        """
        Get timestamp of last successful pipeline run.

        Looks for log entries indicating successful completion:
        - "Successfully processed endpoint"
        - "Load completed"

        Returns:
            Datetime of last successful run, or None if not found
        """
        if not self.log_file_path.exists():
            return None

        last_success = None
        success_patterns = [
            "Successfully processed endpoint",
            "Load completed",
        ]

        try:
            with open(self.log_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = self._parse_log_line(line)
                    if not parsed:
                        continue

                    message = parsed["message"]
                    if any(pattern in message for pattern in success_patterns):
                        # Only consider INFO level success messages
                        if parsed["level"] == "INFO":
                            last_success = parsed["timestamp"]
        except Exception as e:
            logger.error(f"Error reading log file {self.log_file_path}: {e}")

        return last_success

    def get_recent_errors(self, limit: int = 10) -> List[Dict]:
        """
        Get recent error entries from log file.

        Args:
            limit: Maximum number of errors to return

        Returns:
            List of error dictionaries with timestamp, level, and message
        """
        if not self.log_file_path.exists():
            return []

        errors = []

        try:
            with open(self.log_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = self._parse_log_line(line)
                    if not parsed:
                        continue

                    if parsed["level"] == "ERROR":
                        errors.append({
                            "timestamp": parsed["timestamp"],
                            "level": parsed["level"],
                            "message": parsed["message"],
                        })

            # Return most recent errors (last N)
            return errors[-limit:] if len(errors) > limit else errors
        except Exception as e:
            logger.error(f"Error reading log file {self.log_file_path}: {e}")
            return []

    def get_log_stats(self) -> Dict:
        """
        Get basic statistics about the log file.

        Returns:
            Dictionary with file size, last modified time, line count
        """
        if not self.log_file_path.exists():
            return {
                "exists": False,
                "size": 0,
                "last_modified": None,
                "line_count": 0,
            }

        try:
            stat = self.log_file_path.stat()
            line_count = sum(1 for _ in open(self.log_file_path, "r", encoding="utf-8"))

            return {
                "exists": True,
                "size": stat.st_size,
                "last_modified": datetime.fromtimestamp(stat.st_mtime),
                "line_count": line_count,
            }
        except Exception as e:
            logger.error(f"Error getting log stats for {self.log_file_path}: {e}")
            return {
                "exists": False,
                "size": 0,
                "last_modified": None,
                "line_count": 0,
            }

