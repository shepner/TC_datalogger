-- Query: Parse "You were sent" events from user events table
-- Purpose: Extract item quantity, item name, and sender username from events
-- Table: torncity-402423.torn_data.v2_torn_user_events-raw
--
-- This query filters events that start with "You were sent" and parses them to extract:
-- - quantity: The number of items sent (e.g., 29, or 1 for single items)
-- - item_name: The name of the item (e.g., Shark Fin, Advanced Driving Manual)
-- - user_name: The username of the sender (e.g., Domeno1969)
--
-- Expected event formats:
--   - "You were sent <quantity>x <item_name> from <user_name>"
--   - "You were sent a/an/some <item_name> from <user_name>"
--   - Either format may include " with the message: ..." at the end
--
-- Examples:
--   - "You were sent 29x Shark Fin from Domeno1969"
--   - "You were sent an Advanced Driving Manual from Domeno1969"
--   - "You were sent a Bag of Chocolate Kisses from Goddess-Macha with the message: Because I can"
--
-- Usage: Copy and paste into BigQuery SQL editor and execute

SELECT
  -- Convert timestamp to datetime
  -- Assumes timestamp is stored as INTEGER (Unix timestamp in seconds)
  -- If stored as TIMESTAMP type, use: DATETIME(timestamp) instead
  DATETIME(TIMESTAMP_SECONDS(timestamp)) AS timestamp,
  
  -- Original event text
  event,
  
  -- Fetched at timestamp (when the record was loaded into BigQuery)
  fetched_at,
  
  -- Parse quantity: Extract the number if present, otherwise default to 1
  -- Pattern 1: "You were sent " followed by digits (before "x ")
  -- Pattern 2: If no quantity found, default to 1 (for "a/an/some" patterns)
  COALESCE(
    CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64),
    1
  ) AS quantity,
  
  -- Parse item_name: Handle both patterns
  -- Pattern 1: "You were sent \d+x (.+?) from " (quantity specified)
  -- Pattern 2: "You were sent (?:a|an|some) (.+?) from " (single item with article)
  COALESCE(
    REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from '),
    REGEXP_EXTRACT(event, r'You were sent (?:a|an|some) (.+?) from ')
  ) AS item_name,
  
  -- Parse user_name: Extract text after " from "
  -- Usernames cannot have spaces, so capture non-space characters
  -- Stop before " with the message" if present
  REGEXP_EXTRACT(event, r' from ([^\s]+)') AS user_name

FROM
  `torncity-402423.torn_data.v2_torn_user_events-raw`

WHERE
  -- Filter only events that start with "You were sent"
  STARTS_WITH(event, 'You were sent')
  -- Exclude money transfers (e.g., "You were sent $2,000,000 from ...")
  AND NOT REGEXP_CONTAINS(event, r'You were sent \$')

ORDER BY
  timestamp ASC;

