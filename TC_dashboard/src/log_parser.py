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

    def get_record_summary(self) -> Dict:
        """
        Extract record statistics from log file.

        Returns:
            Dictionary with:
            - last_fetch_count: Number of records fetched in last successful run
            - last_inserted: Number of records inserted in last load
            - last_updated: Number of records updated in last load
            - last_total_processed: Total records processed in last load
            - last_total_records: Total records in BigQuery table (from last stats)
            - last_unique_ids: Unique IDs in BigQuery table (from last stats)
            - last_fetch_timestamp: Timestamp of last fetch
            - last_stats_timestamp: Timestamp of last BigQuery stats
        """
        if not self.log_file_path.exists():
            return {
                "last_fetch_count": None,
                "last_inserted": None,
                "last_updated": None,
                "last_total_processed": None,
                "last_total_records": None,
                "last_unique_ids": None,
                "last_fetch_timestamp": None,
                "last_stats_timestamp": None,
            }

        summary = {
            "last_fetch_count": None,
            "last_inserted": None,
            "last_updated": None,
            "last_total_processed": None,
            "last_total_records": None,
            "last_unique_ids": None,
            "last_fetch_timestamp": None,
            "last_stats_timestamp": None,
        }

        # Patterns to match
        fetch_pattern = re.compile(r"Fetched (\d+) records from API")
        load_pattern = re.compile(
            r"Load completed: (\d+) inserted, (\d+) updated \(total: (\d+) processed\)"
        )
        total_records_pattern = re.compile(r"Total records: ([\d,]+)")
        unique_ids_pattern = re.compile(r"Unique IDs: ([\d,]+)")

        try:
            with open(self.log_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = self._parse_log_line(line)
                    if not parsed:
                        continue

                    message = parsed["message"]
                    timestamp = parsed["timestamp"]

                    # Match "Fetched X records from API"
                    fetch_match = fetch_pattern.search(message)
                    if fetch_match and parsed["level"] == "INFO":
                        summary["last_fetch_count"] = int(fetch_match.group(1))
                        summary["last_fetch_timestamp"] = timestamp

                    # Match "Load completed: X inserted, Y updated (total: Z processed)"
                    load_match = load_pattern.search(message)
                    if load_match and parsed["level"] == "INFO":
                        summary["last_inserted"] = int(load_match.group(1))
                        summary["last_updated"] = int(load_match.group(2))
                        summary["last_total_processed"] = int(load_match.group(3))

                    # Match "Total records: X" (from BigQuery stats section)
                    total_match = total_records_pattern.search(message)
                    if total_match and parsed["level"] == "INFO":
                        # Remove commas from number
                        count_str = total_match.group(1).replace(",", "")
                        summary["last_total_records"] = int(count_str)
                        summary["last_stats_timestamp"] = timestamp

                    # Match "Unique IDs: X" (from BigQuery stats section)
                    unique_match = unique_ids_pattern.search(message)
                    if unique_match and parsed["level"] == "INFO":
                        # Remove commas from number
                        count_str = unique_match.group(1).replace(",", "")
                        summary["last_unique_ids"] = int(count_str)

        except Exception as e:
            logger.error(f"Error parsing record summary from {self.log_file_path}: {e}")

        return summary

    def get_run_history(self, limit: int = 10) -> List[Dict]:
        """
        Get history of recent pipeline runs with their statistics.

        Tracks runs by looking for "Successfully processed endpoint" messages and
        collecting statistics that appeared before each one.

        Args:
            limit: Maximum number of runs to return

        Returns:
            List of run dictionaries, each containing:
            - run_timestamp: When the run completed
            - fetch_count: Records fetched
            - inserted: Records inserted
            - updated: Records updated
            - total_processed: Total records processed
            - total_records: Total records in BigQuery at that time
            - unique_ids: Unique IDs in BigQuery at that time
        """
        if not self.log_file_path.exists():
            return []

        runs = []
        pending_stats = {}

        # Patterns to match
        fetch_pattern = re.compile(r"Fetched (\d+) records from API")
        load_pattern = re.compile(
            r"Load completed: (\d+) inserted, (\d+) updated \(total: (\d+) processed\)"
        )
        success_pattern = re.compile(r"Successfully processed endpoint")
        total_records_pattern = re.compile(r"Total records: ([\d,]+)")
        unique_ids_pattern = re.compile(r"Unique IDs: ([\d,]+)")

        try:
            with open(self.log_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = self._parse_log_line(line)
                    if not parsed:
                        continue

                    message = parsed["message"]
                    timestamp = parsed["timestamp"]

                    # Match "Fetched X records from API"
                    fetch_match = fetch_pattern.search(message)
                    if fetch_match and parsed["level"] == "INFO":
                        pending_stats["fetch_count"] = int(fetch_match.group(1))
                        pending_stats["fetch_timestamp"] = timestamp

                    # Match "Load completed: X inserted, Y updated (total: Z processed)"
                    load_match = load_pattern.search(message)
                    if load_match and parsed["level"] == "INFO":
                        pending_stats["inserted"] = int(load_match.group(1))
                        pending_stats["updated"] = int(load_match.group(2))
                        pending_stats["total_processed"] = int(load_match.group(3))

                    # Match "Total records: X" (from BigQuery stats section)
                    total_match = total_records_pattern.search(message)
                    if total_match and parsed["level"] == "INFO":
                        count_str = total_match.group(1).replace(",", "")
                        pending_stats["total_records"] = int(count_str)
                        pending_stats["stats_timestamp"] = timestamp

                    # Match "Unique IDs: X" (from BigQuery stats section)
                    unique_match = unique_ids_pattern.search(message)
                    if unique_match and parsed["level"] == "INFO":
                        count_str = unique_match.group(1).replace(",", "")
                        pending_stats["unique_ids"] = int(count_str)

                    # When we see "Successfully processed endpoint", save the run
                    if success_pattern.search(message) and parsed["level"] == "INFO":
                        # Create a run from pending stats
                        run = {
                            "run_timestamp": timestamp,
                            "fetch_count": pending_stats.get("fetch_count"),
                            "inserted": pending_stats.get("inserted"),
                            "updated": pending_stats.get("updated"),
                            "total_processed": pending_stats.get("total_processed"),
                            "total_records": pending_stats.get("total_records"),
                            "unique_ids": pending_stats.get("unique_ids"),
                        }
                        # Only add if we have at least some data
                        if run["fetch_count"] is not None or run["inserted"] is not None:
                            runs.append(run)
                        # Keep stats that persist across runs (like total_records)
                        # but reset per-run stats
                        persistent_stats = {
                            "total_records": pending_stats.get("total_records"),
                            "unique_ids": pending_stats.get("unique_ids"),
                        }
                        pending_stats = persistent_stats

        except Exception as e:
            logger.error(f"Error parsing run history from {self.log_file_path}: {e}")

        # Return most recent runs (last N), reversed so newest is first
        return list(reversed(runs[-limit:])) if len(runs) > limit else list(reversed(runs))

