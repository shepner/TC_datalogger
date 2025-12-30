"""Scheduler for running pipeline at regular intervals."""

import logging
import re
import time
from datetime import datetime
from typing import Callable

import pytz

logger = logging.getLogger(__name__)


def parse_iso8601_duration(duration_str: str) -> int:
    """
    Parse ISO 8601 duration string to seconds.

    Args:
        duration_str: ISO 8601 duration (e.g., "PT15M", "PT1H", "P1D")

    Returns:
        Duration in seconds
    """
    # Pattern to match ISO 8601 duration: P[nD]T[nH][nM][nS]
    # Examples: PT15M, PT1H, P1D, PT1H30M
    pattern = r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    match = re.match(pattern, duration_str)

    if not match:
        raise ValueError(f"Invalid ISO 8601 duration format: {duration_str}")

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)

    total_seconds = (days * 24 * 60 * 60) + (hours * 60 * 60) + (minutes * 60) + seconds

    if total_seconds == 0:
        raise ValueError(f"Duration must be greater than 0: {duration_str}")

    return total_seconds


class Scheduler:
    """Scheduler that runs a function at regular intervals."""

    def __init__(
        self,
        interval_seconds: int,
        timezone: str = "America/Chicago",
        function: Callable[[], None] = None,
    ):
        """
        Initialize scheduler.

        Args:
            interval_seconds: Interval between runs in seconds
            timezone: IANA timezone identifier
            function: Function to call at each interval
        """
        self.interval_seconds = interval_seconds
        self.timezone = pytz.timezone(timezone)
        self.function = function

    def run_forever(self) -> None:
        """Run the scheduled function forever at the specified interval."""
        if not self.function:
            raise ValueError("No function specified for scheduler")

        logger.info(
            f"Starting scheduler: interval={self.interval_seconds}s, "
            f"timezone={self.timezone}"
        )

        while True:
            try:
                # Get current time in the specified timezone
                now = datetime.now(self.timezone)
                logger.info(f"Running scheduled task at {now}")

                # Run the function
                self.function()

                # Calculate next run time
                next_run = now.timestamp() + self.interval_seconds
                next_run_dt = datetime.fromtimestamp(next_run, tz=self.timezone)
                logger.info(f"Next run scheduled for {next_run_dt}")

                # Sleep until next run
                sleep_time = self.interval_seconds
                logger.debug(f"Sleeping for {sleep_time} seconds")
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                logger.info("Scheduler interrupted by user")
                break
            except Exception as e:
                logger.error(f"Error in scheduled task: {e}", exc_info=True)
                # Sleep a bit before retrying to avoid tight error loops
                time.sleep(min(60, self.interval_seconds))
