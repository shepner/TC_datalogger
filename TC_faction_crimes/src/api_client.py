"""Torn City API client with rate limiting and pagination support."""

import logging
import time
from typing import Any, Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple rate limiter to respect API rate limits."""

    def __init__(self, requests_per_minute: int):
        """
        Initialize rate limiter.

        Args:
            requests_per_minute: Maximum requests allowed per minute
        """
        self.requests_per_minute = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute
        self.last_request_time = 0.0

    def wait_if_needed(self) -> None:
        """Wait if necessary to respect rate limit."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        if time_since_last < self.min_interval:
            sleep_time = self.min_interval - time_since_last
            logger.debug(f"Rate limiting: sleeping {sleep_time:.2f} seconds")
            time.sleep(sleep_time)

        self.last_request_time = time.time()


class TornCityAPIClient:
    """Client for interacting with Torn City API."""

    def __init__(
        self,
        api_key: str,
        rate_limit: int = 60,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: int = 60,
        base_url: str = "https://api.torn.com",
    ):
        """
        Initialize Torn City API client.

        Args:
            api_key: Torn City API key
            rate_limit: Maximum requests per minute
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
            base_url: API base URL (defaults to https://api.torn.com)
        """
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.rate_limiter = RateLimiter(rate_limit)
        self.base_url = base_url

    def _make_request(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make a single API request with retry logic.

        Args:
            url: Full API URL
            params: Query parameters

        Returns:
            JSON response as dictionary

        Raises:
            requests.RequestException: If request fails after retries
        """
        if params is None:
            params = {}

        # Add API key to parameters
        params["key"] = self.api_key

        # Rate limiting
        self.rate_limiter.wait_if_needed()

        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(
                    f"Making API request to {url} (attempt {attempt + 1})"
                )
                response = requests.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()

                data = response.json()
                if "error" in data:
                    error_code = data.get("code", "unknown")
                    error_msg = data.get("error", "Unknown error")
                    raise ValueError(
                        f"API returned error: {error_code} - {error_msg}"
                    )

                return data

            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response else None
                if status_code in (401, 403):
                    # Authentication errors - don't retry
                    logger.error(f"Authentication error: {e}")
                    raise
                if status_code == 429:
                    # Rate limit - wait longer before retry
                    wait_time = self.retry_delay * (attempt + 1)
                    logger.warning(
                        f"Rate limited. Waiting {wait_time} seconds before retry"
                    )
                    time.sleep(wait_time)
                    last_exception = e
                    continue

                # Other HTTP errors - retry with exponential backoff
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"HTTP error {status_code}: {e}. "
                        f"Retrying in {wait_time} seconds"
                    )
                    time.sleep(wait_time)
                    last_exception = e
                    continue
                raise

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as e:
                # Network errors - retry with exponential backoff
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"Network error: {e}. Retrying in {wait_time} seconds"
                    )
                    time.sleep(wait_time)
                    last_exception = e
                    continue
                raise

            except Exception as e:
                # Unexpected errors - don't retry
                logger.error(f"Unexpected error: {e}")
                raise

        # If we exhausted retries, raise the last exception
        if last_exception:
            raise last_exception

        raise RuntimeError("Request failed but no exception was raised")

    def fetch_page(
        self,
        endpoint: str,
        offset: int = 0,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch a single page of results from the API.

        Args:
            endpoint: API endpoint path (e.g., '/v2/faction/crimes')
            offset: Pagination offset
            params: Additional query parameters

        Returns:
            API response as dictionary
        """
        url = f"{self.base_url}{endpoint}"
        if params is None:
            params = {}

        # Add pagination
        if offset > 0:
            params["offset"] = offset

        return self._make_request(url, params)

    def fetch_all_pages(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Fetch all pages of results using pagination.
        
        Handles APIs that loop back to the beginning by tracking seen record IDs.
        Continues fetching until duplicates are detected or no more records are returned.

        Args:
            endpoint: API endpoint path
            params: Additional query parameters

        Yields:
            Individual records from all pages
        """
        offset = 0
        total_records = 0
        seen_ids = set()  # Track seen record IDs to detect loops
        consecutive_empty_pages = 0
        consecutive_duplicate_pages = 0
        max_consecutive_empty = 3  # Stop after 3 empty pages
        max_consecutive_duplicates = 2  # Stop after 2 consecutive pages of all duplicates

        while True:
            logger.info(f"Fetching page at offset {offset}")
            response = self.fetch_page(endpoint, offset=offset, params=params)

            # Handle different response structures
            if isinstance(response, dict):
                # Check for metadata with pagination info
                metadata = response.get("_metadata", {})
                
                # Torn City API v2 returns data in different keys:
                # - "data" for generic endpoints
                # - "crimes" for /v2/faction/crimes
                # - "members" for /v2/faction/members
                # - "items" for /v2/torn/items
                # - etc.
                records = None
                for key in ["data", "crimes", "members", "items"]:
                    if key in response and isinstance(response[key], list):
                        records = response[key]
                        break
                
                # If no standard key found, check if response itself is a list
                # or if there's a single array value
                if records is None:
                    # Check for any list value in the response
                    for key, value in response.items():
                        if key not in ["_metadata", "error", "code"] and isinstance(value, list):
                            records = value
                            logger.debug(f"Found records in key: {key}")
                            break
                
                # Handle single object responses (e.g., basic endpoint returns {"basic": {...}})
                if records is None:
                    for key, value in response.items():
                        if key not in ["_metadata", "error", "code"] and isinstance(value, dict):
                            # Wrap single object in a list
                            records = [value]
                            logger.debug(f"Found single object in key: {key}, wrapping in list")
                            break

                if not records:
                    consecutive_empty_pages += 1
                    logger.warning(
                        f"Empty page at offset {offset} (consecutive empty: {consecutive_empty_pages})"
                    )
                    if consecutive_empty_pages >= max_consecutive_empty:
                        logger.info(
                            f"Stopping after {consecutive_empty_pages} consecutive empty pages. "
                            f"Total unique records fetched: {total_records}"
                        )
                        break
                    # Try next offset even if page is empty
                    records_in_page = 0
                else:
                    consecutive_empty_pages = 0
                    records_in_page = len(records)
                    
                    # Track duplicates in this page
                    new_records = []
                    duplicate_count = 0
                    
                    for record in records:
                        # Extract record ID (handle different possible ID field names)
                        record_id = None
                        if "id" in record:
                            record_id = str(record["id"])
                        elif "crime_id" in record:
                            record_id = str(record["crime_id"])
                        elif "record_id" in record:
                            record_id = str(record["record_id"])
                        
                        if record_id:
                            if record_id in seen_ids:
                                duplicate_count += 1
                                logger.debug(f"Duplicate record detected: ID {record_id}")
                            else:
                                seen_ids.add(record_id)
                                new_records.append(record)
                        else:
                            # If no ID field, treat as new (shouldn't happen but handle gracefully)
                            logger.warning("Record missing ID field, treating as new")
                            new_records.append(record)
                    
                    # Yield only new records
                    for record in new_records:
                        total_records += 1
                        yield record
                    
                    logger.info(
                        f"Page at offset {offset}: {records_in_page} total records, "
                        f"{len(new_records)} new, {duplicate_count} duplicates. "
                        f"Total unique records so far: {total_records}"
                    )
                    
                    # Check if entire page was duplicates (API has looped back)
                    if records_in_page > 0 and duplicate_count == records_in_page:
                        consecutive_duplicate_pages += 1
                        logger.warning(
                            f"All records in page were duplicates (API looped back). "
                            f"Consecutive duplicate pages: {consecutive_duplicate_pages}"
                        )
                        if consecutive_duplicate_pages >= max_consecutive_duplicates:
                            logger.info(
                                f"Stopping after {consecutive_duplicate_pages} consecutive pages "
                                f"of all duplicates. Total unique records fetched: {total_records}"
                            )
                            break
                    else:
                        consecutive_duplicate_pages = 0

                # Determine next offset
                next_url = metadata.get("next")
                if next_url:
                    # Try to parse offset from next URL
                    try:
                        from urllib.parse import urlparse, parse_qs
                        parsed = urlparse(next_url)
                        query_params = parse_qs(parsed.query)
                        if "offset" in query_params:
                            next_offset = int(query_params["offset"][0])
                            logger.debug(f"Parsed next offset from URL: {next_offset}")
                            if next_offset <= offset:
                                # Next offset is same or less - API has looped
                                logger.warning(
                                    f"Next offset ({next_offset}) <= current offset ({offset}). "
                                    f"API may have looped back. Stopping pagination."
                                )
                                break
                            offset = next_offset
                        else:
                            # No offset in URL, increment by records received
                            offset += records_in_page if records_in_page > 0 else 100
                    except Exception as e:
                        logger.debug(f"Could not parse next URL: {e}. Incrementing offset.")
                        # Fall back to incrementing
                        offset += records_in_page if records_in_page > 0 else 100
                else:
                    # No next URL provided - continue with offset increment
                    # This handles APIs that don't provide next URLs
                    if records_in_page == 0:
                        # Already handled above with consecutive_empty_pages check
                        offset += 100  # Try next page size increment
                    else:
                        offset += records_in_page
                        logger.debug(
                            f"No next URL in metadata. Incrementing offset to {offset} "
                            f"based on {records_in_page} records received"
                        )
                
                # Safety check: prevent infinite loops
                if offset > 1000000:  # Arbitrary large limit
                    logger.warning(
                        f"Offset exceeded safety limit (1000000). Stopping pagination. "
                        f"Total unique records fetched: {total_records}"
                    )
                    break
                    
            else:
                # Non-dict response - yield it as-is
                logger.warning("Unexpected response format")
                break
        
        logger.info(
            f"Pagination complete. Total unique records fetched: {total_records} "
            f"(tracked {len(seen_ids)} unique IDs)"
        )

    def fetch_all(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch all records from all pages as a list.

        Args:
            endpoint: API endpoint path
            params: Additional query parameters

        Returns:
            List of all records
        """
        return list(self.fetch_all_pages(endpoint, params))

