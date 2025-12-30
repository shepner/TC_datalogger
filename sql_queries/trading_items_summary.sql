-- Trading Items Summary
-- 
-- Purpose: Summarize "You were sent" events with item details and market prices
-- 
-- Outputs:
--   - timestamp: When the items were sent
--   - user_name: Username of the sender
--   - item_name: Name of the item sent
--   - quantity: Quantity of items sent
--   - market_price: Current market price per item
--   - total_value: Total value (quantity * market_price)
--   - event_id: Original event ID
--
-- Usage: Run this query to get trading items summary for the trading dashboard
--        Filter by date range and payment status as needed.

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
    AND REGEXP_EXTRACT(event, r'You were sent (\d+)x ') IS NOT NULL
)
SELECT
  pe.timestamp,
  pe.user_name,
  pe.item_name,
  pe.quantity,
  SAFE_CAST(items.value.market_price AS INT64) AS market_price,
  pe.quantity * SAFE_CAST(items.value.market_price AS INT64) AS total_value,
  pe.event_id
FROM
  parsed_events AS pe
LEFT JOIN
  `torncity-402423.torn_data.v2_torn_items-raw` AS items
ON
  LOWER(TRIM(pe.item_name)) = LOWER(TRIM(items.name))
ORDER BY
  pe.timestamp DESC;

