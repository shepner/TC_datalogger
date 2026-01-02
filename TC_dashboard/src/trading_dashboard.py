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
            -- Parse quantity: Extract number if present, otherwise default to 1 (for "a/an/some" patterns)
            COALESCE(
              CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64),
              1
            ) AS quantity,
            -- Parse item_name: Handle both patterns
            COALESCE(
              REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from '),
              REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ')
            ) AS item_name,
            TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) AS user_name,
            REGEXP_EXTRACT(event, r' with the message (.+)$') AS comment
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            STARTS_WITH(event, 'You were sent')
            -- Exclude money transfers (e.g., "You were sent $2,000,000 from ...")
            AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
            -- Exclude events from specific users
            AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
            -- Include both formats: with quantity prefix OR with "a/an/some"
            AND (
              REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
              OR REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ') IS NOT NULL
            )
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
          fm.id AS user_id,
          pe.item_name,
          pe.quantity,
          SAFE_CAST(items.value.market_price AS INT64) AS market_price,
          pe.quantity * SAFE_CAST(items.value.market_price AS INT64) AS total_value,
          -- Calculate sales fee: 5% for Xanax, 4% for others
          CASE 
            WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
            ELSE 0.04
          END AS sales_fee,
          -- Calculate buy price: market_price * (1 - sales_fee)
          CAST(
            SAFE_CAST(items.value.market_price AS INT64) * 
            (1 - CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END)
          AS INT64) AS buy_price,
          -- Calculate payment amount: buy_price * quantity
          CAST(
            pe.quantity * 
            (SAFE_CAST(items.value.market_price AS INT64) * 
            (1 - CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END))
          AS INT64) AS payment_amount,
          pe.comment,
          pe.event_id
        FROM
          parsed_events AS pe
        LEFT JOIN
          `torncity-402423.torn_data.v2_torn_items-raw` AS items
        ON
          LOWER(TRIM(pe.item_name)) = LOWER(TRIM(items.name))
        LEFT JOIN
          `torncity-402423.torn_data.v2_faction_40832_members-raw` AS fm
        ON
          LOWER(TRIM(pe.user_name)) = LOWER(TRIM(fm.name))
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
        member_filter: Optional[str] = None,
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
            -- Parse quantity: Extract number if present, otherwise default to 1 (for "a/an/some" patterns)
            COALESCE(
              CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64),
              1
            ) AS quantity,
            -- Parse item_name: Handle both patterns
            COALESCE(
              REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from '),
              REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ')
            ) AS item_name,
            TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) AS user_name
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            STARTS_WITH(event, 'You were sent')
            -- Exclude money transfers (e.g., "You were sent $2,000,000 from ...")
            AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
            -- Exclude events from specific users
            AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
            -- Include both formats: with quantity prefix OR with "a/an/some"
            AND (
              REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
              OR REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ') IS NOT NULL
            )
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
            fm.id AS user_id,
            pe.event_id,
            pe.timestamp,
            pe.item_name,
            pe.quantity,
            SAFE_CAST(items.value.market_price AS INT64) AS market_price,
            -- Calculate sales fee: 5% for Xanax, 4% for others
            CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END AS sales_fee,
            -- Calculate buy price: market_price * (1 - sales_fee)
            CAST(
              SAFE_CAST(items.value.market_price AS INT64) * 
              (1 - CASE 
                WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
                ELSE 0.04
              END)
            AS INT64) AS buy_price,
            -- Calculate payment amount: buy_price * quantity
            CAST(
              pe.quantity * 
              (SAFE_CAST(items.value.market_price AS INT64) * 
              (1 - CASE 
                WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
                ELSE 0.04
              END))
            AS INT64) AS payment_amount
          FROM
            parsed_events AS pe
          LEFT JOIN
            `torncity-402423.torn_data.v2_torn_items-raw` AS items
          ON
            LOWER(TRIM(pe.item_name)) = LOWER(TRIM(items.name))
          LEFT JOIN
            `torncity-402423.torn_data.v2_faction_40832_members-raw` AS fm
          ON
            LOWER(TRIM(pe.user_name)) = LOWER(TRIM(fm.name))
          LEFT JOIN
            paid_events AS paid
          ON
            pe.event_id = paid.event_id
          WHERE
            paid.event_id IS NULL
        )
        SELECT
          user_name AS member_name,
          MAX(user_id) AS member_id,
          COUNT(*) AS trade_count,
          SUM(quantity) AS total_quantity,
          SUM(payment_amount) AS total_payment_amount,
          ARRAY_AGG(
            STRUCT(
              event_id,
              timestamp,
              user_name,
              user_id,
              item_name,
              quantity,
              market_price,
              sales_fee,
              buy_price,
              payment_amount
            )
            ORDER BY timestamp DESC
          ) AS trades
        FROM
          trades_with_payment
        GROUP BY
          user_name
        """
        
        query = query.replace("@days_back", str(days_back))
        
        if member_filter:
            query = query.replace(
                "GROUP BY\n          user_name",
                f"GROUP BY\n          user_name\n        HAVING\n          LOWER(TRIM(user_name)) = LOWER(TRIM('{member_filter}'))"
            )
        
        query += "\n        ORDER BY\n          LOWER(user_name) ASC"
        
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
        
        # Format: [Quantity] [Item Name] * $[Individual Value] = $[Total Value] deposited to the vault!
        # Where Individual Value is the payment per item, Total Value is total payment
        message = f"{quantity} {item_name} * ${individual_payment} = ${payment_amount} deposited to the vault!"
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
            # Check if this event_id already exists in the paid trades table
            check_query = f"""
            SELECT COUNT(*) as count
            FROM `{PAID_TRADES_TABLE}`
            WHERE event_id = @event_id
            """
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("event_id", "STRING", event_id)
                ]
            )
            
            result = self.bq.client.query(check_query, job_config=job_config).result()
            row = next(result, None)
            
            if row and row.count > 0:
                logger.warning(f"Event {event_id} is already marked as paid. Skipping duplicate insert.")
                return
            
            # If trade info not provided, get it from the event
            if trade is None:
                trade = self._get_trade_info(event_id)
            
            if trade is None:
                logger.warning(f"Could not find trade info for event {event_id}")
                return

            # Insert into BigQuery
            # Handle both user_name (from individual trades) and member_name (from grouped trades)
            member_name = trade.get("user_name") or trade.get("member_name") or ""
            
            row = {
                "event_id": event_id,
                "member_name": member_name,
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
            TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) AS user_name
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
          -- Calculate buy price: market_price * (1 - sales_fee)
          CAST(
            SAFE_CAST(items.value.market_price AS INT64) * 
            (1 - CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END)
          AS INT64) AS buy_price,
          -- Calculate payment amount: buy_price * quantity
          CAST(
            pe.quantity * 
            (SAFE_CAST(items.value.market_price AS INT64) * 
            (1 - CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END))
          AS INT64) AS payment_amount
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

    def get_member_names(self, days_back: int = 365, show_paid: bool = False) -> List[str]:
        """
        Get list of unique member names who have trades available for viewing.

        Args:
            days_back: Number of days to look back
            show_paid: If True, return members with paid trades; if False, return members with pending trades

        Returns:
            List of member names sorted alphabetically (case-insensitive)
        """
        if show_paid:
            # Get members who have paid trades
            query = """
            WITH parsed_events AS (
              SELECT
                id AS event_id,
                TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) AS user_name
              FROM
                `torncity-402423.torn_data.v2_torn_user_events-raw`
              WHERE
                STARTS_WITH(event, 'You were sent')
                AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
                AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
                AND (
                  REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
                  OR REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ') IS NOT NULL
                )
                AND TIMESTAMP_SECONDS(SAFE_CAST(timestamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days_back DAY)
                AND TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) IS NOT NULL
                AND TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) != ''
            ),
            paid_events AS (
              SELECT DISTINCT event_id
              FROM
                `torncity-402423.torn_data.trading_paid_events`
            )
            SELECT DISTINCT
              pe.user_name
            FROM
              parsed_events AS pe
            INNER JOIN
              paid_events AS paid
            ON
              pe.event_id = paid.event_id
            ORDER BY
              LOWER(pe.user_name) ASC
            """
        else:
            # Get members who have pending (unpaid) trades
            query = """
            WITH parsed_events AS (
              SELECT
                id AS event_id,
                TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) AS user_name
              FROM
                `torncity-402423.torn_data.v2_torn_user_events-raw`
              WHERE
                STARTS_WITH(event, 'You were sent')
                AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
                AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
                AND (
                  REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
                  OR REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ') IS NOT NULL
                )
                AND TIMESTAMP_SECONDS(SAFE_CAST(timestamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days_back DAY)
                AND TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) IS NOT NULL
                AND TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) != ''
            ),
            paid_events AS (
              SELECT DISTINCT event_id
              FROM
                `torncity-402423.torn_data.trading_paid_events`
            )
            SELECT DISTINCT
              pe.user_name
            FROM
              parsed_events AS pe
            LEFT JOIN
              paid_events AS paid
            ON
              pe.event_id = paid.event_id
            WHERE
              paid.event_id IS NULL
            ORDER BY
              LOWER(pe.user_name) ASC
            """
        
        query = query.replace("@days_back", str(days_back))
        results = self.bq.execute_query(query)
        return [row.get('user_name', '') for row in results if row.get('user_name')]

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
            TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) AS user_name,
            CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64) AS quantity,
            REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from ') AS item_name
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            STARTS_WITH(event, 'You were sent')
            -- Exclude money transfers (e.g., "You were sent $2,000,000 from ...")
            AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
            -- Exclude events from specific users
            AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
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

    def get_paid_trades(
        self,
        days_back: int = 30,
        member_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get paid trades (items that have been marked as paid).

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
            -- Parse quantity: Extract number if present, otherwise default to 1 (for "a/an/some" patterns)
            COALESCE(
              CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64),
              1
            ) AS quantity,
            -- Parse item_name: Handle both patterns
            COALESCE(
              REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from '),
              REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ')
            ) AS item_name,
            TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) AS user_name
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            STARTS_WITH(event, 'You were sent')
            AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
            AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
            -- Include both formats: with quantity prefix OR with "a/an/some"
            AND (
              REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
              OR REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ') IS NOT NULL
            )
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
          fm.id AS user_id,
          pe.item_name,
          pe.quantity,
          SAFE_CAST(items.value.market_price AS INT64) AS market_price,
          pe.quantity * SAFE_CAST(items.value.market_price AS INT64) AS total_value,
          -- Calculate sales fee: 5% for Xanax, 4% for others
          CASE 
            WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
            ELSE 0.04
          END AS sales_fee,
          -- Calculate buy price: market_price * (1 - sales_fee)
          CAST(
            SAFE_CAST(items.value.market_price AS INT64) * 
            (1 - CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END)
          AS INT64) AS buy_price,
          -- Calculate payment amount: buy_price * quantity
          CAST(
            pe.quantity * 
            (SAFE_CAST(items.value.market_price AS INT64) * 
            (1 - CASE 
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END))
          AS INT64) AS payment_amount,
          pe.event_id
        FROM
          parsed_events AS pe
        LEFT JOIN
          `torncity-402423.torn_data.v2_torn_items-raw` AS items
        ON
          LOWER(TRIM(pe.item_name)) = LOWER(TRIM(items.name))
        LEFT JOIN
          `torncity-402423.torn_data.v2_faction_40832_members-raw` AS fm
        ON
          LOWER(TRIM(pe.user_name)) = LOWER(TRIM(fm.name))
        INNER JOIN
          paid_events AS paid
        ON
          pe.event_id = paid.event_id
        WHERE
          paid.event_id IS NOT NULL
        """
        
        # Replace parameter
        query = query.replace("@days_back", str(days_back))
        
        if member_filter:
            query = query.replace(
                "WHERE paid.event_id IS NOT NULL",
                f"WHERE paid.event_id IS NOT NULL AND LOWER(TRIM(pe.user_name)) = LOWER(TRIM('{member_filter}'))"
            )

        results = self.bq.execute_query(query)
        return results

    def get_paid_trades_by_member(
        self,
        days_back: int = 30,
        member_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get paid trades grouped by member.

        Args:
            days_back: Number of days to look back

        Returns:
            List of member dictionaries with their paid trades
        """
        query = """
        WITH parsed_events AS (
          SELECT
            id AS event_id,
            DATETIME(TIMESTAMP_SECONDS(timestamp)) AS timestamp,
            event,
            -- Parse quantity: Extract number if present, otherwise default to 1 (for "a/an/some" patterns)
            COALESCE(
              CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64),
              1
            ) AS quantity,
            -- Parse item_name: Handle both patterns
            COALESCE(
              REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from '),
              REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ')
            ) AS item_name,
            TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)')) AS user_name
          FROM
            `torncity-402423.torn_data.v2_torn_user_events-raw`
          WHERE
            STARTS_WITH(event, 'You were sent')
            AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
            AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
            -- Include both formats: with quantity prefix OR with "a/an/some"
            AND (
              REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
              OR REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ') IS NOT NULL
            )
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
            fm.id AS user_id,
            pe.event_id,
            pe.timestamp,
            pe.item_name,
            pe.quantity,
            SAFE_CAST(items.value.market_price AS INT64) AS market_price,
            -- Calculate sales fee: 5% for Xanax, 4% for others
            CASE
              WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
              ELSE 0.04
            END AS sales_fee,
            -- Calculate buy price: market_price * (1 - sales_fee)
            CAST(
              SAFE_CAST(items.value.market_price AS INT64) *
              (1 - CASE
                WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
                ELSE 0.04
              END)
            AS INT64) AS buy_price,
            -- Calculate payment amount: buy_price * quantity
            CAST(
              pe.quantity *
              (SAFE_CAST(items.value.market_price AS INT64) *
              (1 - CASE
                WHEN LOWER(TRIM(pe.item_name)) = 'xanax' THEN 0.05
                ELSE 0.04
              END))
            AS INT64) AS payment_amount
          FROM
            parsed_events AS pe
          LEFT JOIN
            `torncity-402423.torn_data.v2_torn_items-raw` AS items
          ON
            LOWER(TRIM(pe.item_name)) = LOWER(TRIM(items.name))
          LEFT JOIN
            `torncity-402423.torn_data.v2_faction_40832_members-raw` AS fm
          ON
            LOWER(TRIM(pe.user_name)) = LOWER(TRIM(fm.name))
          INNER JOIN
            paid_events AS paid
          ON
            pe.event_id = paid.event_id
          WHERE
            paid.event_id IS NOT NULL
        )
        SELECT
          user_name AS member_name,
          MAX(user_id) AS member_id,
          COUNT(*) AS trade_count,
          SUM(quantity) AS total_quantity,
          SUM(payment_amount) AS total_payment_amount,
          ARRAY_AGG(
            STRUCT(
              event_id,
              timestamp,
              user_name,
              user_id,
              item_name,
              quantity,
              market_price,
              sales_fee,
              buy_price,
              payment_amount
            )
            ORDER BY timestamp DESC
          ) AS trades
        FROM
          trades_with_payment
        GROUP BY
          user_name
        """
        query = query.replace("@days_back", str(days_back))
        
        if member_filter:
            query = query.replace(
                "GROUP BY\n          user_name",
                f"GROUP BY\n          user_name\n        HAVING\n          LOWER(TRIM(user_name)) = LOWER(TRIM('{member_filter}'))"
            )
        
        query += "\n        ORDER BY\n          LOWER(user_name) ASC"
        
        return self.bq.execute_query(query)

    def get_raw_events_for_user(
        self,
        user_name: str,
        days_back: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Get raw event logs for a specific user.

        Args:
            user_name: Username to get events for
            days_back: Number of days to look back

        Returns:
            List of raw event dictionaries
        """
        query = """
        SELECT
          id AS event_id,
          DATETIME(TIMESTAMP_SECONDS(timestamp)) AS timestamp,
          event,
          DATETIME(fetched_at) AS fetched_at
        FROM
          `torncity-402423.torn_data.v2_torn_user_events-raw`
        WHERE
          STARTS_WITH(event, 'You were sent')
          AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
          AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
          AND LOWER(TRIM(REGEXP_EXTRACT(event, r' from (.+?)(?: with the message|$)'))) = LOWER(TRIM(@user_name))
          AND TIMESTAMP_SECONDS(SAFE_CAST(timestamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days_back DAY)
        ORDER BY
          timestamp DESC
        """
        
        # Replace parameters
        query = query.replace("@user_name", f"'{user_name}'")
        query = query.replace("@days_back", str(days_back))
        
        results = self.bq.execute_query(query)
        return results

    def get_max_days_back(self) -> int:
        """
        Get the maximum number of days back available in the events table.
        
        Returns:
            Maximum number of days back (or 365 if calculation fails)
        """
        query = """
        SELECT
          DATE_DIFF(
            CURRENT_DATE(),
            DATE(TIMESTAMP_SECONDS(MIN(SAFE_CAST(timestamp AS INT64)))),
            DAY
          ) AS max_days_back
        FROM
          `torncity-402423.torn_data.v2_torn_user_events-raw`
        WHERE
          STARTS_WITH(event, 'You were sent')
          AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
          AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
          AND REGEXP_EXTRACT(event, r'You were sent (\\d+)x ') IS NOT NULL
        """
        
        try:
            results = self.bq.execute_query(query)
            if results and len(results) > 0:
                max_days = results[0].get('max_days_back', 365)
                # Cap at 365 days as a reasonable maximum
                return min(int(max_days), 365) if max_days else 365
            return 365
        except Exception as e:
            logger.error(f"Error calculating max days back: {e}", exc_info=True)
            return 365

    def validate_paid_trades(self, event_ids: List[str]) -> Dict[str, Any]:
        """
        Validate that trades were marked as paid in BigQuery.
        
        Args:
            event_ids: List of event IDs to validate
            
        Returns:
            Dictionary with validation results
        """
        if not event_ids:
            return {
                "valid": True,
                "message": "No event IDs provided",
                "found": [],
                "missing": []
            }
        
        # Build query to check which event_ids exist in paid trades table
        event_ids_str = "', '".join(event_ids)
        query = f"""
        SELECT 
            event_id,
            member_name,
            item_name,
            quantity,
            payment_amount,
            paid_at
        FROM
            `{PAID_TRADES_TABLE}`
        WHERE
            event_id IN ('{event_ids_str}')
        ORDER BY
            paid_at DESC
        """
        
        try:
            results = self.bq.execute_query(query)
            found_ids = {row['event_id'] for row in results}
            missing_ids = set(event_ids) - found_ids
            
            return {
                "valid": len(missing_ids) == 0,
                "total_requested": len(event_ids),
                "found_count": len(found_ids),
                "missing_count": len(missing_ids),
                "found": list(found_ids),
                "missing": list(missing_ids),
                "details": results
            }
        except Exception as e:
            logger.error(f"Error validating paid trades: {e}", exc_info=True)
            return {
                "valid": False,
                "error": str(e),
                "found": [],
                "missing": event_ids
            }

