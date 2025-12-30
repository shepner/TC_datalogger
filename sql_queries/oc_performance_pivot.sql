-- OC Performance Pivot Query
-- 
-- Purpose: Provide data structure for OC Performance Dashboard pivot view
--          Returns all member attempts for each OC name + position combination
--          Includes all OC instances with the same name
--          Only includes members who are currently in the faction
-- 
-- Outputs:
--   - oc_name: OC name (e.g., "Bank Robbery", "Drug Deal")
--   - difficulty: OC difficulty level (1-5)
--   - position_id: Position ID (e.g., "P0", "P1", "P2")
--   - position: Position name (e.g., "Looter", "Car Thief")
--   - member_name: Member's name
--   - checkpoint_pass_rate: Checkpoint pass rate (0.0-1.0)
--   - status: Success or Failure (from slot.user.outcome)
--   - crime_id: Individual OC instance ID
--   - executed_at: When the OC was executed
--
-- Usage: Run this query to get all member performance data grouped by OC name and position
--        Frontend can then aggregate this data as needed (most recent, average, etc.)

WITH current_members AS (
  SELECT DISTINCT id AS member_id
  FROM
    `torncity-402423.torn_data.v2_faction_40832_members-raw`
),
oc_slots AS (
  SELECT
    crime.id AS crime_id,
    crime.name AS oc_name,
    crime.difficulty,
    TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) AS executed_at,
    slot.position AS position,
    slot.position_id,
    slot.user.id AS member_id,
    slot.user.outcome AS outcome,
    slot.checkpoint_pass_rate AS checkpoint_pass_rate
  FROM
    `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
    UNNEST(crime.slots) AS slot
  WHERE
    slot.user.id IS NOT NULL
    AND crime.executed_at IS NOT NULL
    AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
    AND slot.user.checkpoint_pass_rate IS NOT NULL
    -- Only include members who are currently in the faction
    AND slot.user.id IN (SELECT member_id FROM current_members)
)
SELECT
  os.oc_name,
  os.difficulty,
  os.position_id,
  os.position,
  COALESCE(m.name, CAST(os.member_id AS STRING)) AS member_name,
  os.checkpoint_pass_rate,
  CASE
    WHEN os.outcome = 'Successful' THEN 'Success'
    ELSE 'Failure'
  END AS status,
  os.crime_id,
  os.executed_at
FROM
  oc_slots AS os
INNER JOIN
  `torncity-402423.torn_data.v2_faction_40832_members-raw` AS m
ON
  os.member_id = m.id
ORDER BY
  os.oc_name ASC,
  os.difficulty DESC,
  os.position_id ASC,
  os.executed_at DESC,
  m.name ASC;

