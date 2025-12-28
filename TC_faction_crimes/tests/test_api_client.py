"""Tests for API client."""

import pytest
import requests
from unittest.mock import Mock, patch
from src.api_client import TornCityAPIClient, RateLimiter


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_rate_limiter_waits(self):
        """Test that rate limiter enforces minimum interval."""
        limiter = RateLimiter(requests_per_minute=60)
        # First request should not wait
        limiter.wait_if_needed()
        
        # Second request should wait
        import time
        start = time.time()
        limiter.wait_if_needed()
        elapsed = time.time() - start
        
        # Should have waited at least 0.9 seconds (60 req/min = 1 sec/req)
        assert elapsed >= 0.9


class TestTornCityAPIClient:
    """Tests for TornCityAPIClient class."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        return TornCityAPIClient(
            api_key="test_key",
            rate_limit=60,
            timeout=30,
            max_retries=1,
            retry_delay=1,
        )

    @patch("src.api_client.requests.get")
    def test_successful_request(self, mock_get, client):
        """Test successful API request."""
        mock_response = Mock()
        mock_response.json.return_value = {"data": [{"id": "1"}]}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = client._make_request("https://api.torn.com/v2/faction/crimes")
        
        assert result == {"data": [{"id": "1"}]}
        mock_get.assert_called_once()

    @patch("src.api_client.requests.get")
    def test_api_error_response(self, mock_get, client):
        """Test API error response."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "error": "Invalid API key",
            "code": 2
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="API returned error"):
            client._make_request("https://api.torn.com/v2/faction/crimes")

    @patch("src.api_client.requests.get")
    def test_retry_on_timeout(self, mock_get, client):
        """Test retry on timeout error."""
        mock_get.side_effect = [
            requests.exceptions.Timeout("Request timed out"),
            Mock(json=Mock(return_value={"data": []}), raise_for_status=Mock())
        ]

        # Should retry and succeed
        result = client._make_request("https://api.torn.com/v2/faction/crimes")
        assert mock_get.call_count == 2
        assert result == {"data": []}

    @patch("src.api_client.requests.get")
    def test_fetch_page(self, mock_get, client):
        """Test fetching a single page."""
        mock_response = Mock()
        mock_response.json.return_value = {"data": [{"id": "1"}]}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = client.fetch_page("/v2/faction/crimes", offset=0)
        
        assert "data" in result
        assert len(result["data"]) == 1

    @patch("src.api_client.requests.get")
    def test_fetch_all_pages_with_duplicates(self, mock_get, client):
        """Test pagination that detects duplicates when API loops back."""
        # First page: 3 unique records
        # Second page: 2 new records + 1 duplicate
        # Third page: All duplicates (API looped back)
        # Fourth page: All duplicates again (should stop after 2 consecutive)
        def mock_response_generator():
            yield Mock(
                json=Mock(return_value={
                    "crimes": [
                        {"id": "1", "data": "record1"},
                        {"id": "2", "data": "record2"},
                        {"id": "3", "data": "record3"},
                    ],
                    "_metadata": {"next": "?offset=3"}
                }),
                raise_for_status=Mock()
            )
            yield Mock(
                json=Mock(return_value={
                    "crimes": [
                        {"id": "4", "data": "record4"},
                        {"id": "5", "data": "record5"},
                        {"id": "1", "data": "record1"},  # duplicate
                    ],
                    "_metadata": {"next": "?offset=6"}
                }),
                raise_for_status=Mock()
            )
            yield Mock(
                json=Mock(return_value={
                    "crimes": [
                        {"id": "1", "data": "record1"},  # All duplicates
                        {"id": "2", "data": "record2"},
                        {"id": "3", "data": "record3"},
                    ],
                    "_metadata": {}
                }),
                raise_for_status=Mock()
            )
            # Keep returning all-duplicate pages until code stops
            while True:
                yield Mock(
                    json=Mock(return_value={
                        "crimes": [
                            {"id": "1", "data": "record1"},
                            {"id": "2", "data": "record2"},
                            {"id": "3", "data": "record3"},
                        ],
                        "_metadata": {}
                    }),
                    raise_for_status=Mock()
                )
        
        mock_get.side_effect = mock_response_generator()

        records = list(client.fetch_all_pages("/v2/faction/crimes"))
        
        # Should have 5 unique records (1, 2, 3, 4, 5)
        assert len(records) == 5
        ids = {r["id"] for r in records}
        assert ids == {"1", "2", "3", "4", "5"}

    @patch("src.api_client.requests.get")
    def test_fetch_all_pages_no_next_url(self, mock_get, client):
        """Test pagination continues even when next_url is missing."""
        # First page: 100 records, no next URL
        # Second page: 50 records, no next URL
        # Third page: empty (should stop after 3 consecutive empty)
        def mock_response_generator():
            yield Mock(
                json=Mock(return_value={
                    "crimes": [{"id": str(i), "data": f"record{i}"} for i in range(100)],
                    "_metadata": {}  # No next URL
                }),
                raise_for_status=Mock()
            )
            yield Mock(
                json=Mock(return_value={
                    "crimes": [{"id": str(i), "data": f"record{i}"} for i in range(100, 150)],
                    "_metadata": {}  # No next URL
                }),
                raise_for_status=Mock()
            )
            # Return empty pages until code stops (after 3 consecutive empty)
            for _ in range(5):
                yield Mock(
                    json=Mock(return_value={
                        "crimes": [],
                        "_metadata": {}
                    }),
                    raise_for_status=Mock()
                )
        
        mock_get.side_effect = mock_response_generator()

        records = list(client.fetch_all_pages("/v2/faction/crimes"))
        
        # Should have 150 unique records
        assert len(records) == 150

