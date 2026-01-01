-- Query: Items with ID, Name, Market Price, and Fetch Timestamp
-- Purpose: Extract basic item information including ID, name, market price, and when the data was fetched
-- Table: v2_torn_items-raw
-- Use Case: Quick reference for item catalog with current market prices

SELECT
  id,
  name,
  value.market_price AS market_price,
  fetched_at
FROM
  `torncity-402423.torn_data.v2_torn_items-raw`
ORDER BY
  id;




