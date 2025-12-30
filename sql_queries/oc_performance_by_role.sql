-- OC Performance by Role and Level
-- 
-- Purpose: Show member performance in each role at each OC level
--          Includes probability of success over time for each OC name and position
--          Only includes members who are in the member table
-- 
-- Outputs:
--   - member_id: Member ID
--   - member_name: Member name
--   - oc_level: OC difficulty level (1-5)
--   - oc_name: OC name (e.g., "Bank Robbery", "Drug Deal")
--   - position: Position/role name (e.g., Car Thief, Techie)
--   - position_id: Position ID (e.g., P1, P2)
--   - executed_at: When the OC was executed
--   - progress: Probability of success (progress percentage)
--   - outcome: Outcome status (e.g., Successful)
--   - checkpoint_pass_rate: Checkpoint pass rate for this attempt
--
-- Usage: Run this query to analyze member performance over time for each OC and position

WITH current_members AS (
  SELECT DISTINCT id AS member_id
  FROM
    `torncity-402423.torn_data.v2_faction_40832_members-raw`
),
oc_slots AS (
  SELECT
    crime.id AS crime_id,
    crime.name AS oc_name,
    crime.difficulty AS oc_level,
    TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) AS executed_at,
    DATE(TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64))) AS executed_date,
    slot.position AS position,
    slot.position_id,
    slot.user.id AS member_id,
    slot.user.progress AS progress,
    slot.user.outcome AS outcome,
    slot.checkpoint_pass_rate AS checkpoint_pass_rate
  FROM
    `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
    UNNEST(crime.slots) AS slot
  WHERE
    slot.user.id IS NOT NULL
    AND crime.executed_at IS NOT NULL
    AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
    AND slot.user.progress IS NOT NULL
    -- Only include members who are currently in the faction
    AND slot.user.id IN (SELECT member_id FROM current_members)
),
SELECT
  os.member_id,
  COALESCE(m.name, CAST(os.member_id AS STRING)) AS member_name,
  os.oc_level,
  os.oc_name,
  os.position,
  os.position_id,
  os.executed_at,
  os.executed_date,
  os.progress,
  os.outcome,
  os.checkpoint_pass_rate
FROM
  oc_slots AS os
INNER JOIN
  `torncity-402423.torn_data.v2_faction_40832_members-raw` AS m
ON
  os.member_id = m.id
ORDER BY
  os.member_name ASC,
  os.oc_level DESC,
  os.oc_name ASC,
  os.position ASC,
  os.executed_at DESC;

