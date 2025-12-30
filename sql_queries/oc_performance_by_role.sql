-- OC Performance by Role and Level
-- 
-- Purpose: Show member performance in each role at each OC level
-- 
-- Outputs:
--   - member_id: Member ID
--   - member_name: Member name
--   - oc_level: OC difficulty level (1-5)
--   - role: Position/role name (e.g., Car Thief, Techie)
--   - position_id: Position ID (e.g., P1, P2)
--   - total_attempts: Total number of times member was in this role/level
--   - successful_attempts: Number of successful attempts
--   - success_rate: Percentage of successful attempts
--   - avg_progress: Average progress percentage
--   - avg_checkpoint_pass_rate: Average checkpoint pass rate
--   - last_attempt_date: Date of most recent attempt
--
-- Usage: Run this query to analyze member performance across different OC roles and levels

WITH oc_slots AS (
  SELECT
    crime.id AS crime_id,
    crime.name AS crime_name,
    crime.difficulty AS oc_level,
    TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) AS executed_at,
    slot.position AS role,
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
)
SELECT
  os.member_id,
  COALESCE(m.name, CAST(os.member_id AS STRING)) AS member_name,
  os.oc_level,
  os.role,
  os.position_id,
  COUNT(*) AS total_attempts,
  COUNTIF(os.outcome = 'Successful') AS successful_attempts,
  ROUND(100.0 * COUNTIF(os.outcome = 'Successful') / COUNT(*), 1) AS success_rate,
  ROUND(AVG(os.progress), 1) AS avg_progress,
  ROUND(AVG(os.checkpoint_pass_rate), 1) AS avg_checkpoint_pass_rate,
  MAX(os.executed_at) AS last_attempt_date
FROM
  oc_slots AS os
LEFT JOIN
  `torncity-402423.torn_data.v2_faction_40832_members-raw` AS m
ON
  os.member_id = m.id
GROUP BY
  os.member_id,
  m.name,
  os.oc_level,
  os.role,
  os.position_id
HAVING
  COUNT(*) >= 1
ORDER BY
  os.oc_level DESC,
  os.role ASC,
  success_rate DESC,
  COALESCE(m.name, CAST(os.member_id AS STRING)) ASC;

