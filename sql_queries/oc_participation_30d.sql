-- OC Participation - 30 Day Count
-- 
-- Purpose: Count the number of OC participations per member in the last 30 days
-- 
-- Outputs:
--   - member_id: Member ID
--   - member_name: Member name
--   - oc_count_30d: Number of OCs participated in the last 30 days
--   - last_oc_date: Date of most recent OC participation
--
-- Usage: Run this query to get OC participation counts for member prioritization
--        in OC assignment email generation.

WITH oc_participations AS (
  SELECT DISTINCT
    slot.user.id AS member_id,
    crime.id AS crime_id,
    crime.name AS crime_name,
    TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) AS executed_at,
    TIMESTAMP_SECONDS(SAFE_CAST(crime.created_at AS INT64)) AS created_at
  FROM
    `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
    UNNEST(crime.slots) AS slot
  WHERE
    slot.user.id IS NOT NULL
    AND crime.executed_at IS NOT NULL
    AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
)
SELECT
  oc.member_id,
  COALESCE(m.name, CAST(oc.member_id AS STRING)) AS member_name,
  COUNT(DISTINCT oc.crime_id) AS oc_count_30d,
  MAX(oc.executed_at) AS last_oc_date
FROM
  oc_participations AS oc
LEFT JOIN
  `torncity-402423.torn_data.v2_faction_40832_members-raw` AS m
ON
  oc.member_id = m.id
GROUP BY
  oc.member_id,
  m.name
ORDER BY
  oc_count_30d ASC,
  member_name ASC;

