"""Trading Items Dashboard for Torn City faction management."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from google.cloud import bigquery
from src.bigquery_client import BigQueryClient

logger = logging.getLogger(__name__)

# BigQuery table for tracking paid trades
PAID_TRADES_TABLE = "torncity-402423.torn_data.trading_paid_events"

# Schema for paid trades table
PAID_TRADES_SCHEMA = [
    bigquery.SchemaField("event_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("member_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("item_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("quantity", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("payment_amount", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("paid_at", "TIMESTAMP", mode="REQUIRED"),
]


class TradingDashboard:
    """Manages trading items data and payment tracking."""

    def __init__(self, bigquery_client: BigQueryClient):
        """
        Initialize trading dashboard.

        Args:
            bigquery_client: BigQuery client for querying data
        """
        self.bq = bigquery_client
        # Ensure paid trades table exists
        self._ensure_paid_trades_table()

    def _ensure_paid_trades_table(self) -> None:
        """Ensure the paid trades table exists in BigQuery."""
        try:
            self.bq.ensure_table_exists(PAID_TRADES_TABLE, PAID_TRADES_SCHEMA)
        except Exception as e:
            logger.error(f"Error ensuring paid trades table exists: {e}", exc_info=True)
            # Don't fail initialization, but log the error

    def get_pending_trades(
        self,
        days_back: int = 30,
        member_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get pending trades (items sent but not yet paid).

        Args:
            days_back: Number of days to look back
            member_filter: Optional member name to filter by

        Returns:
            List of trade dictionaries
        """
        query = """
        WITH parsed_events AS (
          SELECT
            id AS event_id,
            DATETIME(TIMESTAMP_SECONDS(timestamp)) AS timestamp,
            event,
            CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64) AS quantity,
            REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from ') AS item_name,
            TRIM(REGEXP_EXTRACT(event, r' from (.+)$')) AS user_name
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            STARTS_WITH(event, 'You were sent')
            -- Exclude money transfers (e.g., "You were sent $2,000,000 from ...")
            AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
            AND REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
            AND TIMESTAMP_SECONDS(SAFE_CAST(timestamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days_back DAY)
        ),
        paid_events AS (
          SELECT DISTINCT event_id
          FROM
            `torncity-402423.torn_data.trading_paid_events`
        )
        SELECT
          pe.timestamp,
          pe.user_name,
          pe.item_name,
          pe.quantity,
          SAFE_CAST(items.value.market_price AS INT64) AS market_price,
          pe.quantity * SAFE_CAST(items.value.market_price AS INT64) AS total_value,
          -- Calculate payment amount: 5% for Xanax, 4% for others
          CAST(
            pe.quantity * SAFE_CAST(items.value.market_price AS INT64) * 
            CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END AS INT64
          ) AS payment_amount,
          pe.event_id
        FROM
          parsed_events AS pe
        LEFT JOIN
          `torncity-402423.torn_data.v2_torn_items-raw` AS items
        ON
          LOWER(TRIM(pe.item_name)) = LOWER(TRIM(items.name))
        LEFT JOIN
          paid_events AS paid
        ON
          pe.event_id = paid.event_id
        WHERE
          paid.event_id IS NULL
        """
        
        # Replace parameter
        query = query.replace("@days_back", str(days_back))
        
        if member_filter:
            query = query.replace(
                "WHERE paid.event_id IS NULL",
                f"WHERE paid.event_id IS NULL AND LOWER(TRIM(pe.user_name)) = LOWER(TRIM('{member_filter}'))"
            )

        results = self.bq.execute_query(query)
        return results

    def get_pending_trades_by_member(
        self,
        days_back: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Get pending trades grouped by member with totals.

        Args:
            days_back: Number of days to look back

        Returns:
            List of member summaries with total payment amounts
        """
        query = """
        WITH parsed_events AS (
          SELECT
            id AS event_id,
            DATETIME(TIMESTAMP_SECONDS(timestamp)) AS timestamp,
            event,
            CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64) AS quantity,
            REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from ') AS item_name,
            TRIM(REGEXP_EXTRACT(event, r' from (.+)$')) AS user_name
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            STARTS_WITH(event, 'You were sent')
            -- Exclude money transfers (e.g., "You were sent $2,000,000 from ...")
            AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
            AND REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
            AND TIMESTAMP_SECONDS(SAFE_CAST(timestamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days_back DAY)
        ),
        paid_events AS (
          SELECT DISTINCT event_id
          FROM
            `torncity-402423.torn_data.trading_paid_events`
        ),
        trades_with_payment AS (
          SELECT
            pe.user_name,
            pe.event_id,
            pe.timestamp,
            pe.item_name,
            pe.quantity,
            SAFE_CAST(items.value.market_price AS INT64) AS market_price,
            CAST(
              pe.quantity * SAFE_CAST(items.value.market_price AS INT64) * 
              CASE 
                WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
                ELSE 0.04
              END AS INT64
            ) AS payment_amount
          FROM
            parsed_events AS pe
          LEFT JOIN
            `torncity-402423.torn_data.v2_torn_items-raw` AS items
          ON
            LOWER(TRIM(pe.item_name)) = LOWER(TRIM(items.name))
          LEFT JOIN
            paid_events AS paid
          ON
            pe.event_id = paid.event_id
          WHERE
            paid.event_id IS NULL
        )
        SELECT
          user_name AS member_name,
          COUNT(*) AS trade_count,
          SUM(quantity) AS total_quantity,
          SUM(payment_amount) AS total_payment_amount,
          ARRAY_AGG(
            STRUCT(
              event_id,
              timestamp,
              item_name,
              quantity,
              market_price,
              payment_amount
            )
            ORDER BY timestamp DESC
          ) AS trades
        FROM
          trades_with_payment
        GROUP BY
          user_name
        ORDER BY
          total_payment_amount DESC
        """
        
        query = query.replace("@days_back", str(days_back))
        results = self.bq.execute_query(query)
        return results

    def format_chat_message(self, trade: Dict[str, Any]) -> str:
        """
        Format a chat message for a trade.
        
        Format: [Quantity] [Item Name] * [Individual Value] = [Total Value]
        Example: 6 Turtle Shell * 93459 = 560754
        
        Uses payment_amount (4% or 5% of market price) for the calculation.

        Args:
            trade: Trade dictionary

        Returns:
            Formatted chat message
        """
        quantity = trade.get('quantity', 0)
        item_name = trade.get('item_name', 'Unknown Item')
        market_price = trade.get('market_price', 0)
        payment_amount = trade.get('payment_amount', 0)
        
        # Calculate individual payment value (payment_amount / quantity)
        individual_payment = int(payment_amount / quantity) if quantity > 0 else 0
        
        # Format: [Quantity] [Item Name] * [Individual Value] = [Total Value]
        # Where Individual Value is the payment per item, Total Value is total payment
        message = f"{quantity} {item_name} * {individual_payment} = {payment_amount}"
        return message

    def mark_as_paid(self, event_id: str, trade: Optional[Dict[str, Any]] = None) -> None:
        """
        Mark a trade as paid in BigQuery.

        Args:
            event_id: Event ID to mark as paid
            trade: Optional trade dictionary with member_name, item_name, quantity, payment_amount
                   If not provided, will query for these values
        """
        try:
            # If trade info not provided, get it from the event
            if trade is None:
                trade = self._get_trade_info(event_id)
            
            if trade is None:
                logger.warning(f"Could not find trade info for event {event_id}")
                return

            # Insert into BigQuery
            row = {
                "event_id": event_id,
                "member_name": trade.get("user_name", ""),
                "item_name": trade.get("item_name", ""),
                "quantity": trade.get("quantity", 0),
                "payment_amount": trade.get("payment_amount", 0),
                "paid_at": datetime.utcnow().isoformat(),
            }
            
            self.bq.insert_row(PAID_TRADES_TABLE, row)
            logger.info(f"Marked event {event_id} as paid in BigQuery")
            
        except Exception as e:
            logger.error(f"Error marking event {event_id} as paid: {e}", exc_info=True)
            raise

    def unmark_as_paid(self, event_id: str) -> None:
        """
        Unmark a trade as paid (remove from BigQuery).

        Args:
            event_id: Event ID to unmark as paid
        """
        try:
            where_clause = f"event_id = '{event_id}'"
            self.bq.delete_rows(PAID_TRADES_TABLE, where_clause)
            logger.info(f"Unmarked event {event_id} as paid in BigQuery")
            
        except Exception as e:
            logger.error(f"Error unmarking event {event_id} as paid: {e}", exc_info=True)
            raise

    def _get_trade_info(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Get trade information for an event ID.

        Args:
            event_id: Event ID

        Returns:
            Trade dictionary or None if not found
        """
        query = """
        WITH parsed_events AS (
          SELECT
            id AS event_id,
            event,
            CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64) AS quantity,
            REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from ') AS item_name,
            TRIM(REGEXP_EXTRACT(event, r' from (.+)$')) AS user_name
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            id = @event_id
        )
        SELECT
          pe.user_name,
          pe.item_name,
          pe.quantity,
          SAFE_CAST(items.value.market_price AS INT64) AS market_price,
          CAST(
            pe.quantity * SAFE_CAST(items.value.market_price AS INT64) * 
            CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END AS INT64
          ) AS payment_amount
        FROM
          parsed_events AS pe
        LEFT JOIN
          `torncity-402423.torn_data.v2_torn_items-raw` AS items
        ON
          LOWER(TRIM(pe.item_name)) = LOWER(TRIM(items.name))
        LIMIT 1
        """
        
        # Replace parameter (simple string replacement for now)
        query = query.replace("@event_id", f"'{event_id}'")
        
        results = self.bq.execute_query(query)
        return results[0] if results else None

    def get_member_summary(self, days_back: int = 7) -> List[Dict[str, Any]]:
        """
        Get summary of trades per member.

        Args:
            days_back: Number of days to look back

        Returns:
            List of member summaries
        """
        query = """
        WITH parsed_events AS (
          SELECT
            id AS event_id,
            TRIM(REGEXP_EXTRACT(event, r' from (.+)$')) AS user_name,
            CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64) AS quantity,
            REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from ') AS item_name
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            STARTS_WITH(event, 'You were sent')
            -- Exclude money transfers (e.g., "You were sent $2,000,000 from ...")
            AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
            AND REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
            AND TIMESTAMP_SECONDS(SAFE_CAST(timestamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days_back DAY)
        )
        SELECT
          pe.user_name,
          SUM(pe.quantity) AS total_quantity,
          COUNT(DISTINCT pe.event_id) AS trade_count
        FROM
          parsed_events AS pe
        GROUP BY
          pe.user_name
        ORDER BY
          total_quantity DESC
        """
        
        query = query.replace("@days_back", str(days_back))
        return self.bq.execute_query(query)

