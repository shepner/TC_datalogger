-- OC Rewards with Items - Calculate Total Reward Value
-- 
-- Purpose: Calculate total reward value for OCs including both monetary rewards
--          and item rewards (with market prices)
-- 
-- Outputs:
--   - crime_id: OC crime ID
--   - crime_name: OC name
--   - difficulty: OC difficulty level
--   - executed_at: When the OC was executed
--   - money_reward: Monetary reward amount
--   - item_reward_value: Total value of item rewards (market price * quantity)
--   - total_reward_value: Sum of money and item rewards
--   - respect_reward: Respect reward
--   - item_details: JSON array of item details (id, name, quantity, market_price)
--
-- Usage: Run this query to get accurate OC reward analysis including items
--        that are paid to the faction instead of money.

WITH oc_rewards AS (
  SELECT
    crime.id AS crime_id,
    crime.name AS crime_name,
    crime.difficulty,
    TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) AS executed_at,
    crime.rewards.money AS money_reward,
    crime.rewards.respect AS respect_reward,
    crime.rewards.items AS item_rewards
  FROM
    `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime
  WHERE
    crime.executed_at IS NOT NULL
    AND crime.rewards IS NOT NULL
),
item_values AS (
  SELECT
    oc.crime_id,
    oc.crime_name,
    oc.difficulty,
    oc.executed_at,
    oc.money_reward,
    oc.respect_reward,
    item.id AS item_id,
    item.quantity AS item_quantity,
    items_data.name AS item_name,
    items_data.value.market_price AS item_market_price,
    (item.quantity * SAFE_CAST(items_data.value.market_price AS INT64)) AS item_total_value
  FROM
    oc_rewards AS oc,
    UNNEST(oc.item_rewards) AS item
  LEFT JOIN
    `torncity-402423.torn_data.v2_torn_items-raw` AS items_data
  ON
    item.id = items_data.id
)
SELECT
  crime_id,
  crime_name,
  difficulty,
  executed_at,
  COALESCE(MAX(money_reward), 0) AS money_reward,
  COALESCE(SUM(item_total_value), 0) AS item_reward_value,
  COALESCE(MAX(money_reward), 0) + COALESCE(SUM(item_total_value), 0) AS total_reward_value,
  COALESCE(MAX(respect_reward), 0) AS respect_reward,
  ARRAY_AGG(
    STRUCT(
      item_id,
      item_name,
      item_quantity,
      item_market_price
    )
    ORDER BY item_id
  ) AS item_details
FROM
  item_values
GROUP BY
  crime_id,
  crime_name,
  difficulty,
  executed_at
ORDER BY
  executed_at DESC;

