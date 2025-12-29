-- Query: Parse "You were sent" events from user events table
-- Purpose: Extract item quantity, item name, and sender username from events
-- Table: torncity-402423.torn_data.v2_torn_user_events-raw
--
-- This query filters events that start with "You were sent" and parses them to extract:
-- - quantity: The number of items sent (e.g., 29)
-- - item_name: The name of the item (e.g., Shark Fin)
-- - user_name: The username of the sender (e.g., Domeno1969)
--
-- Expected event format: "You were sent <quantity>x <item_name> from <user_name>"
-- Example: "You were sent 29x Shark Fin from Domeno1969"
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
  
  -- Parse quantity: Extract the number after "You were sent "
  -- Pattern: "You were sent " followed by digits (before "x ")
  CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64) AS quantity,
  
  -- Parse item_name: Extract text between "x " and " from "
  -- Pattern: After "x ", capture everything up to " from "
  REGEXP_EXTRACT(event, r'You were sent \d+x (.+?) from ') AS item_name,
  
  -- Parse user_name: Extract text after " from "
  -- Pattern: Everything after " from " (capture to end of string, handling potential trailing whitespace)
  TRIM(REGEXP_EXTRACT(event, r' from (.+)$')) AS user_name

FROM
  `torncity-402423.torn_data.v2_torn_user_events-raw`

WHERE
  -- Filter only events that start with "You were sent"
  STARTS_WITH(event, 'You were sent')

ORDER BY
  timestamp ASC;

