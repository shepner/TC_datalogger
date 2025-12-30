-- Faction Requirements Check
-- 
-- Purpose: Calculate faction requirements compliance for all members
-- 
-- Requirements (matching spreadsheet formula):
--   - If days_in_faction < 30: "N/A"
--   - Otherwise: (OC count >= 3 OR trading count > 480 in 30 days) = requirements met
--   - Chains participation (requirement)
--   - If offline 2+ days: don't promote
--
-- Outputs:
--   - member_id: Member ID
--   - member_name: Member name
--   - level: Member level
--   - position: Current faction position
--   - days_in_faction: Days member has been in faction
--   - days_inactive: Days since last action
--   - oc_count_30d: OC participations in last 30 days
--   - oc_requirement_met: Whether OC requirement is met (3+ per month)
--   - trading_count_30d: Trading items sent in last 30 days
--   - trading_requirement_met: Whether trading requirement is met (480+ in 30 days)
--   - chain_participation: Whether member participated in chains (requirement)
--   - all_requirements_met: Whether all requirements are met (OC OR trading, AND chains)
--   - can_promote: Whether member can be promoted (requirements met AND active within 2 days)
--   - action: Recommended action (promote, demote, remove, none)
--
-- Usage: Run this query at end of month to determine promotions/demotions

WITH member_info AS (
  SELECT
    id AS member_id,
    name AS member_name,
    level,
    position,
    days_in_faction,
    TIMESTAMP_SECONDS(SAFE_CAST(last_action.timestamp AS INT64)) AS last_action_timestamp,
    DATE_DIFF(CURRENT_DATE(), DATE(TIMESTAMP_SECONDS(SAFE_CAST(last_action.timestamp AS INT64))), DAY) AS days_inactive
  FROM
    `torncity-402423.torn_data.v2_faction_40832_members-raw`
),
oc_participation AS (
  SELECT
    slot.user.id AS member_id,
    COUNT(DISTINCT crime.id) AS oc_count_30d
  FROM
    `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
    UNNEST(crime.slots) AS slot
  WHERE
    slot.user.id IS NOT NULL
    AND crime.executed_at IS NOT NULL
    AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  GROUP BY
    slot.user.id
),
trading_items AS (
  SELECT
    TRIM(REGEXP_EXTRACT(event, r' from (.+)$')) AS user_name,
    SUM(CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64)) AS trading_count_30d
  FROM
    `torncity-402423.torn_data.v2_torn_user_events-raw`
  WHERE
    STARTS_WITH(event, 'You were sent')
    -- Exclude money transfers (e.g., "You were sent $2,000,000 from ...")
    AND NOT REGEXP_CONTAINS(event, r'You were sent \$')
    -- Exclude events from specific users
    AND NOT REGEXP_CONTAINS(event, r' from Duke(?: |$)')
    AND TIMESTAMP_SECONDS(SAFE_CAST(timestamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  GROUP BY
    user_name
),
chain_participation AS (
  -- Attackers: array of objects with id field
  SELECT DISTINCT
    CAST(JSON_EXTRACT_SCALAR(participant_json, '$.id') AS INT64) AS member_id
  FROM
    `torncity-402423.torn_data.v2_faction_40832_chains-raw` AS chain,
    UNNEST(JSON_EXTRACT_ARRAY(chain.attackers)) AS participant_json
  WHERE
    JSON_EXTRACT_SCALAR(participant_json, '$.id') IS NOT NULL
    AND chain.end IS NOT NULL
    AND TIMESTAMP_SECONDS(
      COALESCE(
        CAST(EXTRACT(EPOCH FROM TIMESTAMP(chain.end)) AS INT64),
        SAFE_CAST(chain.end AS INT64)
      )
    ) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  UNION DISTINCT
  -- Non-attackers: array of integers (member IDs directly)
  SELECT DISTINCT
    CAST(participant_id AS INT64) AS member_id
  FROM
    `torncity-402423.torn_data.v2_faction_40832_chains-raw` AS chain,
    UNNEST(JSON_EXTRACT_ARRAY(chain.non_attackers)) AS participant_id
  WHERE
    participant_id IS NOT NULL
    AND participant_id != ''
    AND chain.end IS NOT NULL
    AND TIMESTAMP_SECONDS(
      COALESCE(
        CAST(EXTRACT(EPOCH FROM TIMESTAMP(chain.end)) AS INT64),
        SAFE_CAST(chain.end AS INT64)
      )
    ) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
)
SELECT
  m.member_id,
  m.member_name,
  m.level,
  m.position,
  m.days_in_faction,
  m.days_inactive,
  COALESCE(oc.oc_count_30d, 0) AS oc_count_30d,
  COALESCE(oc.oc_count_30d, 0) >= 3 AS oc_requirement_met,
  COALESCE(t.trading_count_30d, 0) AS trading_count_30d,
  COALESCE(t.trading_count_30d, 0) > 480 AS trading_requirement_met,
  cp.member_id IS NOT NULL AS chain_participation,
  -- Requirements met if: (OC >= 3 OR trading > 480) AND chains participation
  -- But if days_in_faction < 30, return NULL (N/A)
  CASE
    WHEN m.days_in_faction < 30 THEN NULL
    ELSE (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
         AND cp.member_id IS NOT NULL
  END AS all_requirements_met,
  -- Can promote if requirements met AND active within 2 days
  CASE
    WHEN m.days_in_faction < 30 THEN NULL
    ELSE (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
         AND cp.member_id IS NOT NULL
         AND m.days_inactive <= 2
  END AS can_promote,
  CASE
    -- If less than 30 days in faction, no action
    WHEN m.days_in_faction < 30 THEN 'none'
    -- Level 1: remove if requirements not met
    WHEN m.level = 1 AND NOT (
      (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
      AND cp.member_id IS NOT NULL
    ) THEN 'remove'
    -- Level > 1: promote if requirements met and active
    WHEN m.level > 1 AND (
      (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
      AND cp.member_id IS NOT NULL
      AND m.days_inactive <= 2
    ) THEN 'promote'
    -- Level > 1: demote if requirements not met
    WHEN m.level > 1 AND NOT (
      (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
      AND cp.member_id IS NOT NULL
    ) THEN 'demote'
    ELSE 'none'
  END AS action
FROM
  member_info AS m
LEFT JOIN
  oc_participation AS oc
ON
  m.member_id = oc.member_id
LEFT JOIN
  trading_items AS t
ON
  LOWER(TRIM(m.member_name)) = LOWER(TRIM(t.user_name))
LEFT JOIN
  chain_participation AS cp
ON
  m.member_id = cp.member_id
ORDER BY
  m.level DESC,
  m.member_name ASC;

