-- OC Performance by Role and Level
-- 
-- Purpose: Show member performance in each role at each OC level
--          Includes rate of advancement and most recent probability
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
--   - most_recent_progress: Most recent probability of success (progress)
--   - rate_of_advancement: Rate of progress increase over time (percentage points per day)
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
    DATE(TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64))) AS executed_date,
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
),
member_role_performance AS (
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
    MAX(os.progress) AS most_recent_progress,
    MAX(os.executed_at) AS last_attempt_date,
    MAX(os.executed_date) AS last_attempt_date_only,
    ROUND(AVG(os.checkpoint_pass_rate), 1) AS avg_checkpoint_pass_rate
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
),
progress_over_time AS (
  SELECT
    os.member_id,
    os.oc_level,
    os.role,
    os.position_id,
    os.executed_date,
    AVG(os.progress) AS avg_progress_for_date
  FROM
    oc_slots AS os
  GROUP BY
    os.member_id,
    os.oc_level,
    os.role,
    os.position_id,
    os.executed_date
  HAVING
    COUNT(*) >= 1
),
rate_calculation AS (
  SELECT
    pot.member_id,
    pot.oc_level,
    pot.role,
    pot.position_id,
    -- Calculate rate of advancement: slope of progress over time
                CASE
                  WHEN COUNT(DISTINCT pot.executed_date) >= 2 THEN
                    ROUND(
                      (MAX(pot.avg_progress_for_date) - MIN(pot.avg_progress_for_date)) /
                      NULLIF(DATE_DIFF(MAX(pot.executed_date), MIN(pot.executed_date), DAY), 0),
                      2
                    )
                  ELSE 0
                END AS rate_of_advancement
  FROM
    progress_over_time AS pot
  GROUP BY
    pot.member_id,
    pot.oc_level,
    pot.role,
    pot.position_id
)
SELECT
  mrp.member_id,
  mrp.member_name,
  mrp.oc_level,
  mrp.role,
  mrp.position_id,
  mrp.total_attempts,
  mrp.successful_attempts,
  mrp.success_rate,
  mrp.avg_progress,
  mrp.most_recent_progress,
  COALESCE(rc.rate_of_advancement, 0) AS rate_of_advancement,
  mrp.avg_checkpoint_pass_rate,
  mrp.last_attempt_date
FROM
  member_role_performance AS mrp
LEFT JOIN
  rate_calculation AS rc
ON
  mrp.member_id = rc.member_id
  AND mrp.oc_level = rc.oc_level
  AND mrp.role = rc.role
  AND mrp.position_id = rc.position_id
ORDER BY
  mrp.oc_level DESC,
  mrp.role ASC,
  mrp.success_rate DESC,
  mrp.member_name ASC;

